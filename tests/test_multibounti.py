import shlex
from pathlib import Path

import pytest

from fetpype.nodes.reconstruction import run_recon_cmd
from fetpype.nodes.segmentation import run_seg_cmd
from fetpype.pipelines.full_pipeline import create_full_pipeline, get_recon
from fetpype.workflows.utils import check_valid_pipeline, init_and_load_cfg


CONFIG = Path(__file__).resolve().parents[1] / "configs/nesvor_multibounti_docker.yaml"


def test_multibounti_workflow():
    cfg = init_and_load_cfg(CONFIG)
    check_valid_pipeline(cfg)
    assert cfg.reconstruction.output_resolution == 0.5
    workflow = create_full_pipeline(cfg)
    assert workflow.get_node("Segmentation.multibounti") is not None
    assert "SurfaceExtraction" not in workflow.list_node_names()


@pytest.mark.parametrize(
    ("preset", "resolution", "metric"),
    [
        ("nesvor_multibounti_docker.yaml", "0.5", "iqa2d"),
        ("default_docker.yaml", "0.8", None),
    ],
)
def test_nesvor_reconstruction_command(tmp_path, monkeypatch, preset, resolution, metric):
    cfg = init_and_load_cfg(CONFIG.with_name(preset))
    node = get_recon(cfg).get_node("nesvor")
    monkeypatch.chdir(tmp_path)
    stacks = ["input/axial.nii.gz", "input/sagittal.nii.gz", "input/coronal.nii.gz"]
    masks = ["masks/axial.nii.gz", "masks/sagittal.nii.gz", "masks/coronal.nii.gz"]
    commands = []
    monkeypatch.setattr("fetpype.utils.logging.run_and_tee", commands.append)

    output = run_recon_cmd(stacks, masks, node.inputs.cmd, node.inputs.cfg)

    (command,) = commands
    args = shlex.split(command, posix=False)
    stack_start = args.index("--input-stacks") + 1
    mask_start = args.index("--stack-masks") + 1
    output_flag = args.index("--output-volume")
    assert args[stack_start : mask_start - 1] == stacks
    assert args[mask_start:output_flag] == masks
    assert Path(args[output_flag + 1]) == Path(output) == tmp_path / "recon/recon.nii.gz"
    assert args[args.index("--output-resolution") + 1] == resolution
    if metric is None:
        assert "--metric" not in args
        assert "--filter-method" not in args
    else:
        assert args[args.index("--metric") + 1] == metric
        assert args[args.index("--filter-method") + 1] == "none"


@pytest.mark.parametrize("create_output", [True, False])
def test_multibounti_segmentation_output(tmp_path, monkeypatch, create_output):
    cfg = init_and_load_cfg(CONFIG).segmentation
    source = tmp_path / "recon.nii.gz"
    source.write_bytes(b"reconstruction")
    monkeypatch.chdir(tmp_path)

    def run_container(command):
        args = shlex.split(command, posix=False)
        assert "fetalsvrtk/svrtk:perinatal_brain_mri_analysis_amd" in args
        assert "run-multi-bounti-fetal-brain-segmentation-2026-general.sh" in args[-4]
        assert Path(args[-2]).read_bytes() == source.read_bytes()
        if create_output:
            Path(args[-1]).write_bytes(b"labels")

    monkeypatch.setattr("fetpype.utils.logging.run_and_tee", run_container)
    seg = tmp_path / "seg/out/seg.nii.gz"
    seg.parent.mkdir(parents=True)
    seg.write_bytes(b"stale output")
    if create_output:
        assert Path(run_seg_cmd(source, cfg.docker.cmd, cfg)) == seg
        assert seg.read_bytes() == b"labels"
    else:
        with pytest.raises(RuntimeError, match="did not produce"):
            run_seg_cmd(source, cfg.docker.cmd, cfg)
