import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("odf")
from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.teletype import extractText

from fetpype.dicom_batch import main


def make_exam(root, participant, date):
    folder = root / participant / date
    folder.mkdir(parents=True)
    (folder / "image.dcm").touch()
    return folder


def make_outputs(output, participant, session, *, segment):
    name = f"sub-{participant}_ses-{session}"
    base = Path(f"sub-{participant}/ses-{session}/anat")
    inputs = output / base
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / f"{name}_T2w.nii.gz").write_bytes(b"stack")
    derivative = output / "derivatives/nesvor_multibounti" / base
    derivative.mkdir(parents=True, exist_ok=True)
    (derivative / f"{name}_rec-nesvor_T2w.nii.gz").write_bytes(b"volume")
    if segment:
        (derivative / f"{name}_rec-nesvor_seg-multibounti_dseg.nii.gz").write_bytes(b"labels")


def report_tables(output):
    (path,) = (output / "derivatives/nesvor_multibounti").glob("fetpype-*-report.ods")
    workbook = load(str(path))
    return {
        table.getAttribute("name"): [
            [extractText(cell) for cell in row.getElementsByType(TableCell)]
            for row in table.getElementsByType(TableRow)
        ]
        for table in workbook.spreadsheet.getElementsByType(Table)
    }


@pytest.mark.parametrize("exitcode", [17, 0])
def test_range_continues_and_reports_failed_and_missing_exams(tmp_path, monkeypatch, exitcode):
    source, output = tmp_path / "DICOM", tmp_path / "BIDS"
    first = make_exam(source, "FetalVienna00001", "20250612")
    second = make_exam(source, "FetalVienna00001", "20250613")
    (source / "FetalVienna00003").mkdir()
    make_exam(source, "FetalVienna00004", "2025-06-14")
    calls = []

    def run(command, *, stdout, stderr, check):
        calls.append(Path(command[4]))
        participant, session = command[-2:]
        if session == "20250612":
            make_outputs(output, participant, session, segment=False)
            selection = output / "tmp_dcm2bids/selection/sub-FetalVienna00001_ses-20250612.json"
            selection.parent.mkdir(parents=True)
            selection.write_text(json.dumps({
                "variant": "B",
                "series": [
                    {"accepted": True, "orientation": "AX"},
                    {"accepted": True, "orientation": "COR"},
                    {"accepted": False, "orientation": "SAG"},
                ],
            }), encoding="utf-8")
            stdout.write("Segmentierungsaufruf beendet\n")
            return SimpleNamespace(returncode=exitcode)
        make_outputs(output, participant, session, segment=True)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("fetpype.dicom_batch.subprocess.run", run)
    code = main([
        "--launcher", "launcher.sh", "--range", "00001", "FetalVienna00003",
        str(source), str(output),
    ])

    assert code == 1
    assert calls == [first, second]
    tables = report_tables(output)
    counts = dict(tables["Übersicht"][1:])
    assert counts["Erfolgreich"] == "1"
    assert counts["Fehlgeschlagen"] == "1"
    assert counts["Eingabe fehlt"] == "2"
    header, *rows = tables["Untersuchungen"]
    exams = [dict(zip(header, row)) for row in rows]
    failed, succeeded, missing_person, missing_date = exams
    assert failed["Exitcode"] == str(exitcode)
    assert "Segmentierung fehlt" in failed["Details"]
    assert "Segmentierungsaufruf beendet" in Path(failed["Log"]).read_text(encoding="utf-8")
    assert failed["Ausgewählt"] == "2"
    assert failed["Ausgeschlossen"] == "1"
    assert failed["Nicht erkannte Ebenen"] == "SAG"
    assert succeeded["Status"] == "Erfolgreich"
    assert missing_person["Person"] == "FetalVienna00002"
    assert "Personenordner fehlt" in missing_person["Details"]
    assert "Kein MRT-Datumsordner" in missing_date["Details"]


def test_all_reuses_complete_outputs_and_resumes_imported_exams(tmp_path, monkeypatch):
    source, output = tmp_path / "DICOM", tmp_path / "BIDS"
    for participant in ("FetalVienna00001", "FetalVienna00002"):
        make_exam(source, participant, "2025-06-12")
    make_outputs(output, "FetalVienna00001", "20250612", segment=False)
    legacy = output / "derivatives/nesvor_multibounti/session_20250612_subject_FetalVienna00001"
    legacy.mkdir()
    (legacy / "seg.nii.gz").write_bytes(b"older segmentation")
    make_outputs(output, "FetalVienna00002", "20250612", segment=False)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        make_outputs(output, "FetalVienna00002", "20250612", segment=True)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("fetpype.dicom_batch.subprocess.run", run)
    assert main(["--launcher", "launcher.sh", "--all", str(source), str(output)]) == 0

    (command,) = calls
    assert command[:9] == [
        "fetpype_run", "--data", str(output), "--out", str(output),
        "--subjects", "FetalVienna00002", "--sessions", "20250612",
    ]
    counts = dict(report_tables(output)["Übersicht"][1:])
    assert counts["Bereits vollständig"] == "1"
    assert counts["Erfolgreich"] == "1"


def test_single_records_empty_input_without_running_tools(tmp_path, monkeypatch):
    source = tmp_path / "FetalVienna00001/2025-06-12"
    source.mkdir(parents=True)
    output = tmp_path / "BIDS"

    def unexpected_run(*args, **kwargs):
        pytest.fail("Empty input should not start conversion")

    monkeypatch.setattr("fetpype.dicom_batch.subprocess.run", unexpected_run)
    assert main(["--launcher", "launcher.sh", "--single", str(source), str(output)]) == 1
    header, row = report_tables(output)["Untersuchungen"]
    exam = dict(zip(header, row))
    assert exam["Person"] == "FetalVienna00001"
    assert exam["Sitzung"] == "20250612"
    assert exam["Status"] == "Eingabe fehlt"
    assert "Keine Dateien" in exam["Details"]


@pytest.mark.parametrize("existing_person", [False, True])
def test_range_reports_missing_six_digit_ids(tmp_path, existing_person):
    source, output = tmp_path / "DICOM", tmp_path / "BIDS"
    source.mkdir()
    if existing_person:
        (source / "FetalVienna000001").mkdir()

    assert main(["--range", "1", "2", str(source), str(output)]) == 1

    header, *rows = report_tables(output)["Untersuchungen"]
    assert [row[header.index("Person")] for row in rows] == [
        "FetalVienna000001", "FetalVienna000002",
    ]
