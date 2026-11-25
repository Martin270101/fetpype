"""Run Fetpype for one or more dated DICOM folders and write an ODS report."""

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Optional


PERSON = re.compile(r"FetalVienna([0-9]+)", re.IGNORECASE)
STATUSES = (
    "Erfolgreich", "Bereits vollständig", "Fehlgeschlagen",
    "Eingabe fehlt", "Abgebrochen", "Nicht gestartet",
)
DERIVATIVE = Path("derivatives/nesvor_multibounti")
SELECTION_DIR = Path("tmp_dcm2bids/selection")


def _label(name):
    return re.sub(r"[^A-Za-z0-9]", "", name)


@dataclass
class Exam:
    source: Path
    participant: str = ""
    session: str = ""
    missing: str = ""

    @property
    def case_path(self):
        path = Path(f"sub-{self.participant}")
        return path / f"ses-{self.session}" if self.session else path

    @property
    def case_name(self):
        return "_".join(self.case_path.parts)


@dataclass
class Result:
    exam: Exam
    status: str = "Nicht gestartet"
    details: str = ""
    exitcode: Optional[int] = None
    log: Optional[Path] = None
    selection_path: Optional[Path] = None
    selected: Optional[int] = None
    rejected: Optional[int] = None
    missing_planes: str = ""
    stacks: int = 0
    reconstruction: bool = False
    segmentation: bool = False


def _number(value):
    match = re.fullmatch(r"(?:FetalVienna)?([0-9]+)", value, re.IGNORECASE)
    if match is None:
        raise argparse.ArgumentTypeError(f"Ungültige Personen-ID: {value}")
    return int(match[1])


def _exam_list(source, single, id_range):
    if single:
        if PERSON.fullmatch(source.parent.name):
            return [Exam(source, source.parent.name, _label(source.name))]
        return [Exam(source, _label(source.name) or "case")]

    people = {}
    id_widths = Counter()
    for folder in sorted(source.iterdir()):
        match = PERSON.fullmatch(folder.name)
        if match and folder.is_dir():
            people.setdefault(int(match[1]), []).append(folder)
            id_widths[len(match[1])] += 1
    id_width = id_widths.most_common(1)[0][0] if id_widths else 6
    numbers = range(id_range[0], id_range[1] + 1) if id_range else sorted(people)
    exams = []
    for number in numbers:
        folders = people.get(number, [])
        if not folders:
            participant = f"FetalVienna{number:0{id_width}d}"
            exams.append(Exam(source / participant, participant, missing="Personenordner fehlt"))
            continue
        for folder in folders:
            dates = sorted(path for path in folder.iterdir() if path.is_dir())
            if not dates:
                exams.append(Exam(folder, folder.name, missing="Kein MRT-Datumsordner"))
            for date in dates:
                exams.append(Exam(date, folder.name, _label(date.name)))

    return exams or [Exam(source, missing="Keine FetalVienna-Personenordner gefunden")]


def _volumes(folder, stem):
    return [
        path for pattern in (f"{stem}.nii", f"{stem}.nii.gz")
        for path in folder.glob(pattern) if path.is_file() and path.stat().st_size
    ]


def _read_results(result, output):
    exam = result.exam
    anat = exam.case_path / "anat"
    derivative = output / DERIVATIVE
    result.stacks = len(_volumes(output / anat, f"{exam.case_name}*_T2w"))
    result.reconstruction = bool(
        _volumes(derivative / anat, f"{exam.case_name}*_rec-nesvor_T2w")
    )
    result.segmentation = bool(
        _volumes(derivative / anat, f"{exam.case_name}*_rec-nesvor_seg-multibounti_dseg")
    )
    if result.reconstruction and not result.segmentation:
        # Older releases stored seg.nii.gz under the Nipype parameterized folder.
        parameter = f"session_{exam.session}_" if exam.session else ""
        for folder in derivative.glob(f"*{parameter}subject_{exam.participant}"):
            if exam.session or "session_" not in folder.name:
                if _volumes(folder, "**/seg"):
                    result.segmentation = True
                    break

    report = output / SELECTION_DIR / f"{exam.case_name}.json"
    try:
        selection = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(selection, dict):
        return None
    result.selection_path = report
    series = selection.get("series", [])
    accepted = [entry for entry in series if entry.get("accepted")]
    result.selected = len(accepted)
    result.rejected = len(series) - result.selected
    planes = {entry.get("orientation") for entry in accepted}
    result.missing_planes = ", ".join(plane for plane in ("AX", "COR", "SAG") if plane not in planes)
    return selection.get("variant")


def _run_exam(result, args, report_dir):
    exam = result.exam
    previous_variant = _read_results(result, args.output) if exam.participant else None
    missing = exam.missing
    if not missing:
        if not exam.source.is_dir():
            missing = "DICOM-Ordner fehlt"
        elif PERSON.fullmatch(exam.source.parent.name) and not exam.session:
            missing = "Datumsordner ergibt keine gültige Sitzungs-ID"
        elif not any(path.is_file() for path in exam.source.rglob("*")):
            missing = "Keine Dateien im MRT-Datumsordner"
    if missing:
        result.status, result.details = "Eingabe fehlt", missing
        return

    if previous_variant is not None and previous_variant != args.series_rules:
        result.status = "Fehlgeschlagen"
        result.details = f"Vorhandener Import verwendet Variante {previous_variant}. Separaten Ausgabeordner wählen."
        return
    if result.stacks and result.reconstruction and result.segmentation:
        result.status = "Bereits vollständig"
        return

    if result.stacks:
        command = [
            "fetpype_run", "--data", str(args.output), "--out", str(args.output),
            "--subjects", exam.participant,
        ]
        if exam.session:
            command.extend(["--sessions", exam.session])
        command.extend(["--config", str(args.launcher.parent.parent / "configs/nesvor_multibounti_docker.yaml")])
    else:
        command = [
            "bash", str(args.launcher), "--series-rules", args.series_rules,
            str(exam.source), str(args.output), exam.participant,
        ]
        if exam.session:
            command.append(exam.session)

    result.log = report_dir / f"{exam.case_name}.log"
    with result.log.open("w", encoding="utf-8") as log:
        result.exitcode = subprocess.run(
            command, stdout=log, stderr=subprocess.STDOUT, check=False
        ).returncode
    _read_results(result, args.output)
    if result.exitcode == 0 and result.stacks and result.reconstruction and result.segmentation:
        result.status = "Erfolgreich"
        return

    details = []
    if result.selected == 0:
        details.append("Keine passende DICOM-Serie")
    if not result.stacks:
        details.append("BIDS-T2w-Stacks fehlen")
    if not result.reconstruction:
        details.append("Rekonstruktion fehlt")
    if not result.segmentation:
        details.append("Segmentierung fehlt")
    if result.exitcode:
        details.append("Details im Log")
    result.status = "Fehlgeschlagen"
    result.details = ", ".join(details)


def _write_report(path, results):
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.style import Style, TableCellProperties, TableColumnProperties, TextProperties
    from odf.table import Table, TableCell, TableColumn, TableRow
    from odf.text import P

    document = OpenDocumentSpreadsheet()
    header = Style(name="Header", family="table-cell")
    header.addElement(TableCellProperties(backgroundcolor="#23445C", padding="0.12cm"))
    header.addElement(TextProperties(color="#FFFFFF", fontweight="bold"))
    document.automaticstyles.addElement(header)
    wrap = Style(name="Wrap", family="table-cell")
    wrap.addElement(TableCellProperties(wrapoption="wrap", padding="0.12cm"))
    document.automaticstyles.addElement(wrap)

    def sheet(name, headings, widths, rows):
        table = Table(name=name)
        for index, width in enumerate(widths):
            style = Style(name=f"{name[:1]}Column{index}", family="table-column")
            style.addElement(TableColumnProperties(columnwidth=f"{width}cm"))
            document.automaticstyles.addElement(style)
            table.addElement(TableColumn(stylename=style))
        for index, values in enumerate([headings, *rows]):
            row = TableRow()
            for value in values:
                cell_style = header if index == 0 else wrap
                if isinstance(value, int):
                    cell = TableCell(stylename=cell_style, valuetype="float", value=value)
                else:
                    cell = TableCell(stylename=cell_style, valuetype="string")
                cell.addElement(P(text="" if value is None else str(value)))
                row.addElement(cell)
            table.addElement(row)
        document.spreadsheet.addElement(table)

    counts = Counter(result.status for result in results)
    sheet("Übersicht", ("Kennzahl", "Anzahl"), (7, 3), [
        ("Berichtszeilen gesamt", len(results)),
        *((status, counts[status]) for status in STATUSES),
        ("Ausgewählte Serien (mit Bericht)", sum(result.selected or 0 for result in results)),
        ("Ausgeschlossene Serien (mit Bericht)", sum(result.rejected or 0 for result in results)),
    ])
    sheet("Untersuchungen", (
        "Person", "Sitzung", "MRT-Ordner", "Status", "Details",
        "NIfTI-Stacks", "Rekonstruktion", "Segmentierung", "Ausgewählt",
        "Ausgeschlossen", "Nicht erkannte Ebenen", "Exitcode", "Log", "Auswahlbericht",
    ), (4.4, 3.6, 8, 4.2, 8, 3, 3.8, 3.8, 3.2, 3.6, 4.5, 2.5, 8, 8), [
        (
            result.exam.participant, result.exam.session, result.exam.source,
            result.status, result.details, result.stacks,
            "Ja" if result.reconstruction else "Nein",
            "Ja" if result.segmentation else "Nein",
            result.selected, result.rejected, result.missing_planes,
            result.exitcode, result.log, result.selection_path,
        )
        for result in results
    ])
    document.save(str(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--single", action="store_true", help="Ein MRT-Datumsordner")
    modes.add_argument("--all", action="store_true", help="Alle Personen und ihre MRT-Datumsordner")
    modes.add_argument("--range", nargs=2, type=_number, metavar=("VON", "BIS"), help="Personen-ID-Bereich inklusive beider Grenzen")
    parser.add_argument("--series-rules", choices=("A", "B"), default="B")
    parser.add_argument("--launcher", type=Path, default=Path(__file__).resolve().parents[1] / "scripts/run_dicom_multibounti.sh", help=argparse.SUPPRESS)
    parser.add_argument("source", type=Path, metavar="DICOM")
    parser.add_argument("output", type=Path, metavar="BIDS")
    args = parser.parse_args(argv)
    if args.range and args.range[0] > args.range[1]:
        parser.error("VON darf nicht größer als BIS sein.")
    if not args.single and not args.source.is_dir():
        parser.error(f"DICOM-Wurzelordner fehlt: {args.source}")
    if args.single and PERSON.fullmatch(args.source.name) and any(
        path.is_dir() for path in args.source.glob("*")
    ):
        parser.error("Für --single den MRT-Datumsordner angeben.")
    try:
        import odf.opendocument
    except ModuleNotFoundError:
        parser.error('Für den ODS-Bericht Fetpype mit python -m pip install -e ".[dicom]" installieren.')

    args.source = args.source.resolve()
    args.output = args.output.resolve()
    exams = _exam_list(args.source, args.single, args.range)
    if any(args.output.is_relative_to(exam.source.resolve()) for exam in exams if exam.participant):
        parser.error("Der BIDS-Ausgabeordner muss außerhalb der DICOM-Untersuchungsordner liegen.")
    reports = args.output / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = Path(tempfile.mkdtemp(prefix=f"fetpype-{stamp}-", dir=reports))
    derivative = args.output / DERIVATIVE
    derivative.mkdir(parents=True, exist_ok=True)
    report = derivative / f"{report_dir.name}-report.ods"
    results = [Result(exam) for exam in exams]
    destinations = Counter(exam.case_name.casefold() for exam in exams if not exam.missing)
    interrupted = False
    result = None
    try:
        for index, result in enumerate(results, 1):
            exam = result.exam
            print(f"[{index}/{len(results)}] {exam.participant} {exam.session}", flush=True)
            if not exam.missing and destinations[exam.case_name.casefold()] > 1:
                result.status = "Fehlgeschlagen"
                result.details = "Mehrere MRT-Ordner ergeben dieselbe BIDS-Sitzung."
            else:
                try:
                    _run_exam(result, args, report_dir)
                except OSError as exc:
                    result.status, result.details = "Fehlgeschlagen", str(exc)
            print(f"  {result.status}", flush=True)
    except KeyboardInterrupt:
        if result is not None:
            result.status = "Abgebrochen"
        interrupted = True
    finally:
        _write_report(report, results)
        print(f"ODS-Bericht: {report}", flush=True)

    if interrupted:
        return 130
    return int(any(result.status in {"Fehlgeschlagen", "Eingabe fehlt"} for result in results))


if __name__ == "__main__":
    sys.exit(main())
