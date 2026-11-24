# BIDS Input Data Preparation
This guide explains how to prepare your fetal brain MRI data in BIDS (Brain Imaging Data Structure) format for use with Fetpype. 

## Overview 
Fetpype expects input data to follow the BIDS specification for neuroimaging datasets. This standardized format ensures reproducibility, facilitates data sharing, and enables automatic processing pipeline execution.

## BIDS Structure

The BIDS structure is as follows:

```
dataset/
├── dataset_description.json           # Dataset metadata (required)
├── participants.tsv                   # Subject information (required for dHCP)
├── README                             # Dataset description (recommended)
├── CHANGES                            # Version history (recommended)
└── sub-<subject_id>/                  # Subject directories
    └── [ses-<session_id>/]            # Session directories (optional)
        └── anat/                      # Anatomical data directory
            ├── sub-<subject_id>[_ses-<session_id>][_acq-<acquisition>]_run-<run_number>_T2w.nii.gz
            ├── sub-<subject_id>[_ses-<session_id>][_acq-<acquisition>]_run-<run_number>_T2w.nii.gz
            ├── sub-<subject_id>[_ses-<session_id>][_acq-<acquisition>]_run-<run_number>_T2w.nii.gz
            └── ...
```
!!! Note
    More information on the structure of the `participants.tsv` file needed for the dHCP pipeline is provided on [this page](segmentation.md#gestational-age-requirement).

## Naming Conventions

The naming convention for the files is as follows:

```
sub-<subject_id>[_ses-<session_id>][_acq-<acquisition>]_run-<run_number>_T2w.nii.gz
```

The following entities are supported:

- `sub-<subject_id>`: Subject identifier
- `ses-<session_id>`: Session identifier
- `acq-<acquisition>`: Acquisition identifier
- `run-<run_number>`: Run number

## DICOM to BIDS Conversion

There are several tools that can be used to convert DICOM to BIDS. We recommend using [dcm2bids](https://github.com/dcm2bids/dcm2bids). Other options are to use [dcm2niix](https://github.com/rordenlab/dcm2niix) to create the nifti files and then convert the files to BIDS

### DICOM to NeSVoR and SVRTK Multi-BOUNTI

Run this workflow from a Fetpype checkout in a Linux environment with Bash,
Dcm2Bids 3, dcm2niix, and GPU-enabled Docker. Install Fetpype in editable mode
so that `fetpype_run` is available. Use a BIDS output path without spaces for
Fetpype's container bind mounts. See the
[SVRTK requirements](https://github.com/SVRTK/perinatal-brain-mri-analysis#auto-processing-scripts)
for supported inputs and GPU memory.

### Header-based series selection

Install the optional DICOM dependency with `python -m pip install -e ".[dicom]"`.
For one case folder, give the launcher the input and output paths:

```bash
bash scripts/run_dicom_multibounti.sh /data/dicoms/ID1111 /data/bids_nifti
```

This uses Variant B and derives the BIDS participant label from the folder
name. The label keeps ASCII letters and digits and falls back to `case`
when there are none. `ID1111` produces NIfTI stacks in
`/data/bids_nifti/sub-ID1111/anat/` without a session.

Use `--series-rules A` for T2-only selection or `--series-rules B` to also
include eligible bSSFP series. The launcher supplies the Dcm2Bids mapping
automatically in this mode.

```bash
bash scripts/run_dicom_multibounti.sh --series-rules B --bids-dicom-root \
  /data/dicom_bids /data/bids_nifti 01 01
```

The selector reads headers and groups MR files by `SeriesInstanceUID`.
Each series needs 8–120 distinct single-frame DICOM instances, a positive
slice thickness of at most 6.5 mm on every slice, and a consistent AX,
COR, or SAG orientation. The orientation comes from the dominant slice
normal in DICOM patient coordinates. An explicit `3D` acquisition is
excluded. An empty acquisition type is allowed.

Case-insensitive name rules exclude body, cardiac, and non-T2 acquisitions.
The remaining T2 candidates pass by name or, for unrecognized names, by a
DICOM echo time of at least 80 ms on all slices. Variant B also accepts
bSSFP aliases such as `WAHNSINN`, `bFFE` and `Balanced FFE`. Those aliases
are exempt from the generic `FFE` exclusion, while the other exclusions
still apply.

The report in `<BIDS_OUT>/tmp_dcm2bids/selection/` records the decision and
reason per series. The source files stay in place. Dcm2Bids writes accepted
stacks under the workflow's `T2w` input suffix. Variant B sends the selected
T2 and bSSFP stacks to one reconstruction and records which series are
bSSFP in the report.

### Import using a Dcm2Bids JSON only

The mapping in `configs/dcm2bids_t2w.example.json` selects series whose
`SeriesDescription` contains `T2` or `WAHNSINN`, ignoring case. Use
`dcm2bids_helper` on a representative scan if you need to inspect the
series descriptions.

If the original DICOMs are already organized under `sub-01/ses-01/`, pass
their root directory with `--bids-dicom-root`. The launcher looks for the
selected case under `sourcedata/` first and then directly under the source
root. Dcm2Bids uses dcm2niix to read the DICOM series in that case directory
and writes the matching T2w NIfTI files to the separate BIDS output.

```bash
bash scripts/run_dicom_multibounti.sh --bids-dicom-root \
  /data/dicom_bids /data/bids_nifti 01 01 configs/dcm2bids_t2w.example.json
```

For a subject organized without a `ses-*` directory, omit the session
argument. This writes the stacks to `sub-01/anat/` in the output dataset.

You can also pass a case's DICOM directory directly.

```bash
bash scripts/run_dicom_multibounti.sh /data/dicom/case01 /data/bids 01 01 \
  configs/dcm2bids_t2w.example.json
```

The launcher imports a new subject/session into BIDS, then runs Fetpype for
that case. The `nesvor_multibounti_docker.yaml` preset reconstructs at 0.5 mm
isotropic resolution and invokes the SVRTK Multi-BOUNTI model for general
fetal acquisitions. It uses Fetpype's reconstruction bias correction and
passes `0` for the model script's optional N4 correction. The reconstruction
and segmentation are written to `/data/bids/derivatives/nesvor_multibounti/`.

NeSVoR logs an `iqa2d` quality score and rank for each input stack in
`<BIDS_OUT>/nipype/logs/<timestamp>/pypeline.log`. The preset keeps all stacks
for reconstruction by setting `--filter-method none`.

For a session already converted to BIDS NIfTI, run the preset directly. This also lets you
retry processing after a successful import.

```bash
fetpype_run --data /data/bids --out /data/bids --subjects 01 --sessions 01 \
  --config configs/nesvor_multibounti_docker.yaml
```
