# Fetpype  [![](https://img.shields.io/badge/Docs-https%3A%2F%2Ffetpype.github.io%2Ffetpype%2F-green)](https://fetpype.github.io/fetpype/)

Fetpype is a pipeline for processing *in vivo* human fetal brain MRI

This pipeline is the fruit of a collaborative effort shared across teams involved in the ERANET MultiFact project. *Fetpype* is in active development, contributions and feedback are very warmly welcome.

# What is *Fetpype*?
*Fetpype* is a library that aims at facilitating the analysis of fetal brain MRI by integrating the variety of tools commonly used in processing pipelines. 
Starting from clinical acquisition of fetal brain MRI (T2-weighted fast spin echo sequences), it performs pre-processing, reconstruction, segmentation and surface extraction.

It aims at making easy the integration of a variety of tools existing to process fetal brain MR images by providing a wrapper around docker/singularity containers and working with BIDS formatted data. 

More information are available on the [documentation](https://fetpype.github.io/fetpype/).

## Optional add-on for DICOM import and SVRTK Multi-BOUNTI

This fork adds an optional launcher for DICOM import, NeSVoR reconstruction
at 0.5 mm, SVRTK Multi-BOUNTI segmentation, and ODS batch reporting. Start
the add-on through `scripts/run_dicom_multibounti.sh` or select its preset
explicitly for existing BIDS-NIfTI data. The standard Fetpype workflow and
presets are described in the [Fetpype documentation](https://fetpype.github.io/fetpype/).

### Install the add-on

Use Bash on Linux with GPU-enabled Docker. Set up the Python
environment once on the machine where the pipeline will run.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dicom]" "dcm2bids>=3,<4" dcm2niix
```

Activate the environment again when you open a new shell.

### Check Docker GPU access

The preset launches the reconstruction and segmentation containers for
you. Docker downloads their images on first use if they are missing from
the local cache. [Docker image pull behavior](https://docs.docker.com/reference/cli/docker/container/run/#pull)

On the Linux GPU host, run these checks from the account you will use
for Fetpype.

```bash
nvidia-smi -L
docker info >/dev/null
docker run --rm --gpus all ubuntu nvidia-smi
```

The first command checks that Linux can see the NVIDIA GPU. The second
checks that your account can reach the Docker daemon. If the final
command lists a GPU, Docker can pass it into a container and you can start
the add-on. [Docker GPU access](https://docs.docker.com/engine/containers/gpu/)

If the host sees the GPU and Docker is reachable but the container test
fails, give the error output to the server administrator. On a standard
Linux Docker installation, an administrator installs the NVIDIA Container
Toolkit for the distribution, configures the Docker runtime, and restarts
Docker. Rootless Docker has its own configuration steps.
[NVIDIA Container Toolkit for Linux](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#configuring-docker)

### Run one examination

```bash
bash scripts/run_dicom_multibounti.sh /data/dicoms/ID1111 /data/bids_nifti
```

Give the launcher the DICOM directory for one case and the BIDS output
directory. With two paths it derives the participant label from the case
folder name, keeps only letters and digits, and uses Variant B. For
`ID1111`, the stacks go into `sub-ID1111/anat/`.

For `FetalVienna000001/20250612`, pass the date directory as the input.
The launcher uses the parent directory as the participant and the date as
the session, yielding `sub-FetalVienna000001/ses-20250612/anat/`.

The launcher checks the header and applies the series
selection rules before Dcm2Bids converts the accepted series to separate
NIfTI stacks. Variant A selects T2 series. Variant B also admits eligible
bSSFP series, including WAHNSINN, for joint reconstruction with the T2 stacks.
The selection report is written to
`<BIDS_OUT>/tmp_dcm2bids/selection/sub-ID1111.json`. The
[input guide](docs/input_data.md) also shows how to select a participant
and optional session from a BIDS-organized DICOM folder tree.

The [Multi-BOUNTI preset](configs/nesvor_multibounti_docker.yaml) prepares the
stacks and their masks, then reconstructs the brain with NeSVoR at 0.5 mm
isotropic resolution. NeSVoR logs one `iqa2d` score and rank per stack to
`<BIDS_OUT>/nipype/logs/<timestamp>/pypeline.log`. The preset uses
`--filter-method none` so all stacks passed to NeSVoR remain available for
reconstruction. SVRTK Multi-BOUNTI segments the reconstructed volume. Both
outputs go to `<BIDS_OUT>/derivatives/nesvor_multibounti/`.

If the stacks are already in BIDS-NIfTI format, run the preset directly.
Use a BIDS output path without spaces for Fetpype's container bind mounts.
See the [input guide](docs/input_data.md) for more details.

```bash
fetpype_run --data /data/bids_nifti --out /data/bids_nifti \
  --subjects 01 --sessions 01 --config configs/nesvor_multibounti_docker.yaml
```

### Startup modes with an ODS report

Use a mode as the first option. `--single` takes one MRT date folder.
`--all` and `--range` take the root containing the `FetalVienna...`
person folders.

```bash
bash scripts/run_dicom_multibounti.sh --single /data/dicoms/FetalVienna000001/20250612 /data/bids_nifti
bash scripts/run_dicom_multibounti.sh --all /data/dicoms /data/bids_nifti
bash scripts/run_dicom_multibounti.sh --range 000001 000020 /data/dicoms /data/bids_nifti
```

The range is inclusive. Bounds can also be entered as `1 20` or as complete
IDs such as `FetalVienna000001 FetalVienna000020`. Missing IDs in the report
use the digit width most common in the source root, with six digits as
the default. Every dated examination is processed separately
with Variant B by default. Add `--series-rules A` after the mode to select
Variant A. Use separate output roots when comparing variants.

The launcher writes the ODS file directly into the results directory at
`<BIDS_OUT>/derivatives/nesvor_multibounti/fetpype-<run>-report.ods`.
Per-exam logs are saved under `<BIDS_OUT>/reports/fetpype-<run>/`.
The report contains summary
counts and one row per examination or missing entry, including the outcome,
selected series, missing orientations in the accepted DICOM headers,
output-file checks, and paths to the logs and selection reports. A failed
exam is recorded and processing continues. Complete results are reused.
Existing BIDS stacks can be used to resume incomplete reconstructions or
segmentations. A batch with missing or failed entries returns exit code 1
after writing the report.

### Run the workflow tests

Run these commands from the checkout root.

```bash
python -m pip install -e ".[dev,dicom]"
python -m pytest -q tests/test_dicom_selection.py tests/test_dicom_launcher.py tests/test_dicom_batch.py tests/test_multibounti.py
```

The focused tests cover header-based series selection, forwarding T2 and
bSSFP together, DICOM import, NeSVoR quality options, Multi-BOUNTI output
handling, and the batch ODS report. They use synthetic headers and
substitute test commands for the external tools. For the full suite,
install Graphviz (`dot`) and run `python -m pytest -q`.

# Reference
If you use `Fetpype` in your research, please cite the following paper:

```
@article{sanchez2026fetpype,
  author = {Sanchez, Thomas and Martí-Juan, Gerard and Meunier, David and Ballester, Miguel Angel Gonzalez and Camara, Oscar and Eixarch, Elisenda and Piella, Gemma and Cuadra, Meritxell Bach and Auzias, Guillaume},
  title = {Fetpype: An Open-Source Pipeline for Reproducible Fetal Brain MRI Analysis}, 
  doi = {10.21105/joss.10278},
  url = {https://doi.org/10.21105/joss.10278},
  year = {2026},
  publisher = {The Open Journal},
  volume = {11},
  number = {122},
  pages = {10278},
  journal = {Journal of Open Source Software}
}
```



