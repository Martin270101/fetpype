import json
from pathlib import Path

import pytest

pydicom = pytest.importorskip("pydicom")
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

from fetpype.dicom_selection import main, select_series


def write_series(root, name, *, count=8, thickness=6.5, echo=80, acquisition="",
                 orientation=(1, 0, 0, 0, 1, 0), frames=None):
    root.mkdir(parents=True)
    series_uid = generate_uid()
    for index in range(count):
        instance_uid = generate_uid()
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = MRImageStorage
        meta.MediaStorageSOPInstanceUID = instance_uid
        path = root / f"slice-{index}"
        header = FileDataset(path, {}, file_meta=meta, preamble=bytes(128))
        header.Modality = "MR"
        header.SOPClassUID = MRImageStorage
        header.SOPInstanceUID = instance_uid
        header.SeriesInstanceUID = series_uid
        header.SeriesDescription = name
        header.SliceThickness = thickness
        header.EchoTime = echo
        header.MRAcquisitionType = acquisition
        if orientation is not None:
            header.ImageOrientationPatient = list(orientation)
        if frames is not None:
            header.NumberOfFrames = frames
        header.save_as(path, enforce_file_format=True)
    return series_uid


@pytest.mark.parametrize(
    ("case", "name", "overrides", "accepted", "reason"),
    [
        ("min", "ax HASTE", {"echo": 20}, True, "t2_name"),
        ("max", "t2/240 AX", {"count": 120, "echo": 20}, True, "t2_name"),
        ("separator", "T2 200_100", {"echo": 20}, True, "t2_name"),
        ("fallback", "generic axial", {}, True, "t2_echo_time"),
        ("fallback-low", "generic axial", {"echo": 79.9}, False, "no_t2_evidence"),
        ("below-min", "HASTE", {"count": 7}, False, "slice_count"),
        ("above-max", "HASTE", {"count": 121}, False, "slice_count"),
        ("thick", "HASTE", {"thickness": 6.6}, False, "slice_thickness"),
        ("unoriented", "HASTE", {"orientation": None}, False, "orientation"),
        ("3d", "HASTE", {"acquisition": "3D"}, False, "3d_acquisition"),
        ("multiframe", "HASTE", {"frames": 2}, False, "multiframe"),
        ("body", "Abdomen HASTE", {}, False, "body"),
        ("body-bssfp", "Placenta WAHNSINN", {}, False, "body"),
        ("cardiac", "Cine WAHNSINN", {}, False, "cardiac"),
        ("diffusion", "DWI AX", {"echo": 95}, False, "non_t2"),
        ("diffusion-bssfp", "DWI WAHNSINN", {"echo": 95}, False, "non_t2"),
        ("bffe", "AX bFFE", {"echo": 2}, True, "bssfp"),
        ("balanced-ffe", "COR Balanced FFE", {"echo": 2}, True, "bssfp"),
        ("plain-ffe", "AX FFE", {}, False, "non_t2"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_series_selection(tmp_path, case, name, overrides, accepted, reason):
    uid = write_series(tmp_path / case, name, **overrides)

    selected, report = select_series(tmp_path, "B")

    (entry,) = report["series"]
    assert entry["series_uid"] == uid
    assert entry["accepted"] is accepted
    assert entry["reason"] == reason
    assert bool(selected) is accepted


def test_variant_a_excludes_bssfp_above_echo_threshold(tmp_path):
    write_series(tmp_path / "series", "SAG WAHNSINN", echo=95)
    selected, report = select_series(tmp_path, "A")
    assert not selected
    assert report["series"][0]["reason"] == "bssfp_variant_a"


def test_variant_b_converts_t2_and_bssfp_together(tmp_path, monkeypatch):
    source = tmp_path / "source"
    t2_uid = write_series(source / "a" / "t2", "HASTE AX")
    bssfp_uid = write_series(source / "b" / "bssfp", "wahnsinn", echo=3,
                             orientation=(1, 0, 0, 0, 0, 1))
    write_series(source / "c" / "excluded", "DWI", echo=95)
    (source / "notes.txt").write_text("not a DICOM")
    report_path = tmp_path / "out" / "selection.json"
    conversion_args = ["-p", "01", "-s", "02", "-c", "selected.json", "-o", "out"]
    stages = []

    def run_converter(args):
        assert args[:2] == ["dcm2bids", "-d"]
        stage = Path(args[2])
        stages.append(stage)
        assert args[3:] == conversion_args
        headers = [pydicom.dcmread(path) for path in stage.rglob("*.dcm")]
        assert len(headers) == 16
        assert {header.SeriesInstanceUID for header in headers} == {t2_uid, bssfp_uid}
        return 0

    monkeypatch.setattr("fetpype.dicom_selection.subprocess.call", run_converter)
    assert main(["B", str(source), str(report_path), "--", *conversion_args]) == 0
    (stage,) = stages
    assert not stage.exists()
    assert len(list(source.rglob("slice-*"))) == 24
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["skipped_files"] == 1
    assert {entry["orientation"] for entry in report["series"] if entry["accepted"]} == {"AX", "COR"}


def test_no_eligible_series_stops_before_conversion(tmp_path, monkeypatch):
    source = tmp_path / "source"
    write_series(source / "series", "T1 SAG")
    report_path = tmp_path / "out" / "selection.json"

    def unexpected_conversion(_):
        pytest.fail("Dcm2Bids should not be called with an empty selection")

    monkeypatch.setattr("fetpype.dicom_selection.subprocess.call", unexpected_conversion)
    assert main(["B", str(source), str(report_path), "--", "-o", "out"]) == 1
    assert report_path.is_file()


def test_bad_slice_header_rejects_entire_series(tmp_path):
    source = tmp_path / "series"
    write_series(source, "HASTE")
    path = source / "slice-7"
    header = pydicom.dcmread(path)
    header.SliceThickness = 7
    header.save_as(path, enforce_file_format=True)

    selected, report = select_series(source, "B")

    assert not selected
    assert report["series"][0]["reason"] == "slice_thickness"
