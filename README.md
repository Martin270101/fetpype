# Fetpype  [![](https://img.shields.io/badge/Docs-https%3A%2F%2Ffetpype.github.io%2Ffetpype%2F-green)](https://fetpype.github.io/fetpype/)

Fetpype is a pipeline for processing *in vivo* human fetal brain MRI

This pipeline is the fruit of a collaborative effort shared across teams involved in the ERANET MultiFact project. *Fetpype* is in active development, contributions and feedback are very warmly welcome.

# What is *Fetpype*?
*Fetpype* is a library that aims at facilitating the analysis of fetal brain MRI by integrating the variety of tools commonly used in processing pipelines. 
Starting from clinical acquisition of fetal brain MRI (T2-weighted fast spin echo sequences), it performs pre-processing, reconstruction, segmentation and surface extraction.

It aims at making easy the integration of a variety of tools existing to process fetal brain MR images by providing a wrapper around docker/singularity containers and working with BIDS formatted data. 

More information are available on the [documentation](https://fetpype.github.io/fetpype/).

## DICOM to NeSVoR and SVRTK Multi-BOUNTI

From a Fetpype checkout, install Fetpype in editable mode with
`python -m pip install -e ".[dicom]"`. Run the launcher in a Linux environment with
Bash, Dcm2Bids 3, dcm2niix, and GPU-enabled Docker.

```bash
bash scripts/run_dicom_multibounti.sh --series-rules B --bids-dicom-root \
  /data/dicom_bids /data/bids_nifti 01 01
```

The launcher finds the DICOMs for `sub-01/ses-01` under the source root or
its `sourcedata/` directory. It checks the header and applies the series
selection rules before Dcm2Bids converts the accepted series to separate
NIfTI stacks. Variant A selects T2 series. Variant B also admits eligible
bSSFP series, including WAHNSINN, for joint reconstruction with the T2 stacks.
The selection report is written to
`<BIDS_OUT>/tmp_dcm2bids/selection/sub-01_ses-01.json`. For a dataset without
session directories, omit the session argument.

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

### Run the workflow tests

Run these commands from the checkout root.

```bash
python -m pip install -e ".[dev,dicom]"
python -m pytest -q tests/test_dicom_selection.py tests/test_dicom_launcher.py tests/test_multibounti.py
```

The focused tests cover header-based series selection, forwarding T2 and
bSSFP together, DICOM import, the NeSVoR quality options and stack forwarding,
and Multi-BOUNTI output handling. They use synthetic headers and substitute
test commands for the external tools. For the full suite, install Graphviz
(`dot`) and run `python -m pytest -q`.

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



