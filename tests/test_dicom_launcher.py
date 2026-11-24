import os
import shutil
import subprocess
from pathlib import Path

import pytest


def find_bash():
    if os.name == "nt" and (git := shutil.which("git")):
        git_bash = Path(git).resolve().parents[1] / "bin/bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    return shutil.which("bash") if os.name == "posix" else None


BASH = find_bash()


def run_launcher(tmp_path, args, dicom, conversion="stack"):
    launcher = Path(__file__).resolve().parents[1] / "scripts/run_dicom_multibounti.sh"
    (tmp_path / "mapping.json").write_text("{}")
    tools = {
        "dcm2niix": "exit 0",
        "docker": "exit 0",
        "dcm2bids_scaffold": 'mkdir -p "$2"; touch "$2/dataset_description.json"',
        "dcm2bids": '''
[[ $2 == "$DICOM" ]] || exit 20
[[ $CONVERSION != failed ]] || exit 22
if [[ $CONVERSION == stack ]]; then
    [[ $3 == -p && $4 == 01 ]] || exit 23
    target=sub-01
    name=sub-01
    if [[ $5 == -s ]]; then
        [[ $6 == 02 ]] || exit 24
        target+=/ses-02
        name+=_ses-02
        shift 2
    fi
    [[ $5 == -c && $7 == -o ]] || exit 25
    [[ $6 == mapping.json || $6 == */configs/dcm2bids_selected_t2w.json ]] || exit 25
    mkdir -p "$8/$target/anat"
    touch "$8/$target/anat/${name}_run-1_T2w.nii.gz"
fi
''',
        "python": '''
[[ $1 == -m && $2 == fetpype.dicom_selection && $3 == B && $4 == "$DICOM" ]] || exit 26
[[ $5 == converted/tmp_dcm2bids/selection/sub-01_ses-02.json && $6 == -- ]] || exit 27
shift 6
[[ $6 == */configs/dcm2bids_selected_t2w.json ]] || exit 28
touch selection-called
dcm2bids -d "$DICOM" "$@"
''',
        "fetpype_run": 'printf "%s\\n" "$@" > calls',
    }
    for name, script in tools.items():
        executable = tmp_path / name
        executable.write_text(f"#!/usr/bin/env bash\n{script}\n")
        executable.chmod(0o755)

    return subprocess.run(
        [BASH, launcher.as_posix(), *args],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
             "CONVERSION": conversion, "DICOM": dicom},
        capture_output=True, text=True,
    )


@pytest.mark.skipif(BASH is None, reason="requires Bash")
@pytest.mark.parametrize("conversion", ["stack", "empty", "failed"])
def test_dicom_launcher(tmp_path, conversion):
    (tmp_path / "dicom files").mkdir()
    calls = tmp_path / "calls"
    result = run_launcher(
        tmp_path,
        ["dicom files", "bids dataset", "01", "02", "mapping.json"],
        "dicom files",
        conversion,
    )

    if conversion == "stack":
        assert result.returncode == 0, result.stderr
        args = calls.read_text().splitlines()
        assert args[:-1] == [
            "--data", "bids dataset", "--out", "bids dataset", "--subjects", "01",
            "--sessions", "02", "--config",
        ]
        assert args[-1].endswith("/configs/nesvor_multibounti_docker.yaml")
    else:
        assert result.returncode == (22 if conversion == "failed" else 1)
        assert not calls.exists()


@pytest.mark.skipif(BASH is None, reason="requires Bash")
@pytest.mark.parametrize(
    ("dicom", "session", "expected_stack"),
    [
        ("source/sub-01/ses-02", ["02"], "sub-01/ses-02/anat/sub-01_ses-02_run-1_T2w.nii.gz"),
        ("source/sourcedata/sub-01/ses-02", ["02"], "sub-01/ses-02/anat/sub-01_ses-02_run-1_T2w.nii.gz"),
        ("source/sub-01", [], "sub-01/anat/sub-01_run-1_T2w.nii.gz"),
    ],
)
def test_import_from_bids_dicom_root(tmp_path, dicom, session, expected_stack):
    raw_file = tmp_path / dicom / "anat" / "series" / "image.dcm"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"source DICOM")
    result = run_launcher(
        tmp_path,
        ["--bids-dicom-root", "source", "converted", "01", *session, "mapping.json"],
        dicom,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "converted" / expected_stack).is_file()
    assert raw_file.read_bytes() == b"source DICOM"
    args = (tmp_path / "calls").read_text().splitlines()
    expected_selection = ["--subjects", "01", "--sessions", "02"] if session else ["--subjects", "01"]
    assert args[:-1] == ["--data", "converted", "--out", "converted", *expected_selection, "--config"]


@pytest.mark.skipif(BASH is None, reason="requires Bash")
def test_bids_dicom_root_requires_selection_when_sessions_exist(tmp_path):
    (tmp_path / "source/sub-01/ses-02/anat").mkdir(parents=True)
    result = run_launcher(
        tmp_path,
        ["--bids-dicom-root", "source", "converted", "01", "mapping.json"],
        "source/sub-01",
    )

    assert result.returncode == 2
    assert "Specify a session" in result.stderr
    assert not (tmp_path / "converted").exists()


@pytest.mark.skipif(BASH is None, reason="requires Bash")
def test_launcher_uses_header_rules_before_running_fetpype(tmp_path):
    (tmp_path / "source/sub-01/ses-02").mkdir(parents=True)
    result = run_launcher(
        tmp_path,
        ["--series-rules", "B", "--bids-dicom-root", "source", "converted", "01", "02"],
        "source/sub-01/ses-02",
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "selection-called").is_file()
    assert (tmp_path / "calls").is_file()
