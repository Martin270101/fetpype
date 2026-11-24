"""Check the source DICOM series and import the eligible stacks with Dcm2Bids."""

import argparse
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np


BODY = (
    "abdomen", "abd", "body", "placenta", "plap", "leber", "femur", "niere",
    "kidney", "kid", "nacl", "thorax", "bibi", "herz", "aorta", "cardio",
    "cochl", "sinus", "haemo", "hemo", "spine", "wirbel", "myelo", "mrcp",
    "thrive", "dynamic", "dynamisch", "dixon", "vane", "vibe", "bone",
)
CARDIAC = (
    "cine", "mapping", "feir", "molli", "t1map", "t2map", "hla", "vla",
    "sax", "lax", "2ch", "4ch", "short axis", "long axis",
)
NON_T2 = (
    "dwi", "dwibs", "diff", "dti", "adc", "trace", "flair", "epi", "t1",
    "tfe_t1", "survey", "surv", "sucher", "localiz", "scout", "loc_",
    "perf", "swi", "tof", "angio_t1", "unknown", "fizpez", "bone", "magic",
    "bold", "calibration", "b1 cal", "3d free", "retro", "spair", "ssh_80",
    "80_fp",
)
T2_NAMES = (
    "haste", "ssfse", "tuff", "t2_tse", "t2w_tse", "_head", "mvxd",
    "ssh_t2", "tse_t2",
)
T2_TIMINGS = re.compile(r"t2[/ _-]+(?:2[34]0|200[/ _-]+100)(?!\d)")
BSSFP = ("wahnsinn", "balanc", "b-tfe", "bffe", "bssfp", "trufi", "fiesta")
HEADER_TAGS = (
    "Modality", "SeriesInstanceUID", "SOPInstanceUID", "SeriesDescription",
    "SliceThickness", "ImageOrientationPatient", "MRAcquisitionType",
    "EchoTime", "NumberOfFrames",
)
ACCEPTED_REASONS = {"t2_name", "t2_echo_time", "bssfp"}


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _plane(values):
    """Find the dominant patient axis perpendicular to the slice plane."""
    try:
        directions = np.asarray(values, dtype=float).reshape(2, 3)
    except (TypeError, ValueError):
        return None
    if not np.allclose(np.linalg.norm(directions, axis=1), 1, atol=0.01):
        return None
    row, column = directions
    if abs(np.dot(row, column)) > 0.01:
        return None
    normal = np.abs(np.cross(row, column))
    other, largest = np.argsort(normal)[-2:]
    if np.isclose(normal[largest], normal[other]):
        return None
    return ("SAG", "COR", "AX")[largest]


def _match(names, keywords):
    return any(keyword in name for name in names for keyword in keywords)


def _classification(headers, names, thicknesses, plane, echo_times, variant):
    if not 8 <= len(headers) <= 120:
        return "slice_count"
    if any(_finite_number(header.get("NumberOfFrames", 1)) != 1 for header in headers):
        return "multiframe"
    if any(value is None or not 0 < value <= 6.5 for value in thicknesses):
        return "slice_thickness"
    if plane is None:
        return "orientation"
    if any(str(header.get("MRAcquisitionType", "")).strip().upper() == "3D"
           for header in headers):
        return "3d_acquisition"
    if _match(names, BODY):
        return "body"
    if _match(names, CARDIAC):
        return "cardiac"

    bssfp = _match(names, BSSFP)
    # FFE alone is excluded. Balanced FFE and bFFE are bSSFP aliases.
    if _match(names, NON_T2) or (not bssfp and _match(names, ("ffe",))):
        return "non_t2"
    if bssfp:
        return "bssfp" if variant == "B" else "bssfp_variant_a"
    if _match(names, T2_NAMES) or any(T2_TIMINGS.search(name) for name in names):
        return "t2_name"
    if all(value is not None and value >= 80 for value in echo_times):
        return "t2_echo_time"
    return "no_t2_evidence"


def select_series(source, variant):
    """Return the eligible DICOM files grouped by series and a decision report."""
    from pydicom import dcmread
    from pydicom.errors import InvalidDicomError

    series = defaultdict(dict)
    skipped = 0
    duplicates = 0
    for path in sorted(Path(source).rglob("*")):
        if not path.is_file():
            continue
        try:
            header = dcmread(
                path, stop_before_pixels=True, specific_tags=HEADER_TAGS, force=True
            )
            uid = header.get("SeriesInstanceUID")
            instance = header.get("SOPInstanceUID")
            is_mr = header.get("Modality") == "MR"
        except (InvalidDicomError, OSError, EOFError, ValueError):
            skipped += 1
            continue
        if not (uid and instance and is_mr):
            skipped += 1
            continue
        if instance in series[uid]:
            duplicates += 1
            continue
        series[uid][instance] = (path, header)

    selected = []
    decisions = []
    for uid, instances in series.items():
        paths, headers = zip(*instances.values())
        descriptions = sorted({str(header.get("SeriesDescription", "") or "")
                               for header in headers})
        names = [description.casefold() for description in descriptions]
        thicknesses = [_finite_number(header.get("SliceThickness")) for header in headers]
        echo_times = [_finite_number(header.get("EchoTime")) for header in headers]
        planes = {_plane(header.get("ImageOrientationPatient")) for header in headers}
        plane = next(iter(planes)) if len(planes) == 1 else None
        reason = _classification(headers, names, thicknesses, plane, echo_times, variant)
        accepted = reason in ACCEPTED_REASONS
        if accepted:
            selected.append(paths)
        decisions.append({
            "series_uid": uid,
            "series_descriptions": descriptions,
            "file_count": len(paths),
            "max_slice_thickness_mm": max((v for v in thicknesses if v is not None), default=None),
            "min_echo_time_ms": min((v for v in echo_times if v is not None), default=None),
            "orientation": plane,
            "accepted": accepted,
            "reason": reason,
        })

    return selected, {
        "variant": variant,
        "skipped_files": skipped,
        "duplicate_instances": duplicates,
        "series": decisions,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=("A", "B"))
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("conversion_options", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.source.is_dir():
        parser.error(f"DICOM directory not found: {args.source}")

    try:
        selected, report = select_series(args.source, args.variant)
    except ModuleNotFoundError as exc:
        if exc.name != "pydicom":
            raise
        parser.error(
            'DICOM header selection requires pydicom. '
            'Install with: python -m pip install -e ".[dicom]"'
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"Selected {len(selected)} of {len(report['series'])} series. "
        f"Report: {args.report}",
        flush=True,
    )
    if not selected:
        print("No DICOM series met the selection criteria.", file=sys.stderr)
        return 1

    conversion_options = args.conversion_options
    if conversion_options[:1] == ["--"]:
        conversion_options = conversion_options[1:]
    with tempfile.TemporaryDirectory(prefix="fetpype-dicom-") as temp_dir:
        for series_number, paths in enumerate(selected, 1):
            series_dir = Path(temp_dir) / f"series-{series_number}"
            series_dir.mkdir()
            for instance_number, path in enumerate(paths, 1):
                destination = series_dir / f"slice-{instance_number}.dcm"
                try:
                    os.link(path.resolve(), destination)
                except OSError:
                    shutil.copyfile(path, destination)
        return subprocess.call(["dcm2bids", "-d", temp_dir, *conversion_options])


if __name__ == "__main__":
    sys.exit(main())
