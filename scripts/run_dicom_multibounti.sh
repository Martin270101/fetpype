#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: bash $0 DICOM_CASE_DIR BIDS_OUT"
    echo "       bash $0 DICOM_DIR BIDS_OUT PARTICIPANT [SESSION] DCM2BIDS_CONFIG.json"
    echo "       bash $0 --bids-dicom-root SOURCE_ROOT BIDS_OUT PARTICIPANT [SESSION] DCM2BIDS_CONFIG.json"
    echo "       bash $0 --series-rules A|B [--bids-dicom-root] SOURCE BIDS_OUT PARTICIPANT [SESSION]"
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    usage
    exit 0
fi
bids_source=false
series_rules=
while [[ ${1:-} == --* ]]; do
    case "$1" in
        --bids-dicom-root)
            bids_source=true
            shift
            ;;
        --series-rules)
            if [[ ${2:-} != A && ${2:-} != B ]]; then
                echo "--series-rules requires A or B." >&2
                exit 2
            fi
            series_rules=$2
            shift 2
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if ! $bids_source && [[ $# == 2 ]]; then
    if [[ ! -d "$1" ]]; then
        echo "DICOM directory not found: $1" >&2
        exit 2
    fi
    source_dir=$(CDPATH= cd -- "$1" && pwd)
    participant=$(printf '%s' "${source_dir##*/}" | LC_ALL=C tr -cd '[:alnum:]')
    participant=${participant:-case}
    series_rules=${series_rules:-B}
    set -- "$1" "$2" "$participant"
    echo "Using BIDS participant sub-$participant."
fi
if [[ -n "$series_rules" ]]; then
    set -- "$@" "$repo/configs/dcm2bids_selected_t2w.json"
fi
if [[ $# != 4 && $# != 5 ]]; then
    usage >&2
    exit 2
fi

dicom=$1
bids=$2
participant=$3
session=
if [[ $# == 5 ]]; then
    session=$4
fi
mapping=${!#}

if [[ ! -f "$mapping" ]]; then
    echo "Dcm2Bids JSON file not found: $mapping" >&2
    exit 2
fi
if [[ ! $participant =~ ^[a-zA-Z0-9]+$ || ($# == 5 && ! $session =~ ^[a-zA-Z0-9]+$) ]]; then
    echo "Use alphanumeric participant and session labels without sub-/ses-." >&2
    exit 2
fi

shopt -s nullglob
case_path="sub-$participant"
case_name="$case_path"
convert_session=()
process_session=()
if [[ -n "$session" ]]; then
    case_path+="/ses-$session"
    case_name+="_ses-$session"
    convert_session=(-s "$session")
    process_session=(--sessions "$session")
fi

if $bids_source; then
    source_root=$dicom
    dicom="$source_root/$case_path"
    if [[ -d "$source_root/sourcedata/$case_path" ]]; then
        dicom="$source_root/sourcedata/$case_path"
    fi
    if [[ -z "$session" ]]; then
        sessions=("$dicom"/ses-*/)
        if (( ${#sessions[@]} > 0 )); then
            echo "Specify a session for $dicom to import its DICOMs separately." >&2
            exit 2
        fi
    fi
fi
if [[ ! -d "$dicom" ]]; then
    echo "DICOM directory not found: $dicom" >&2
    exit 2
fi

for tool in dcm2niix dcm2bids dcm2bids_scaffold fetpype_run docker; do
    if ! command -v "$tool" >/dev/null; then
        echo "Required command not found: $tool" >&2
        exit 127
    fi
done

anat="$bids/$case_path/anat"
if [[ -e "$bids/$case_path" ]]; then
    echo "The BIDS output case already exists at $bids/$case_path. Run fetpype_run on the existing stacks or choose a new import destination." >&2
    exit 2
fi
if [[ ! -f "$bids/dataset_description.json" ]]; then
    dcm2bids_scaffold -o "$bids"
fi
conversion_args=(-p "$participant" "${convert_session[@]}" -c "$mapping" -o "$bids")
if [[ -n "$series_rules" ]]; then
    report="$bids/tmp_dcm2bids/selection/$case_name.json"
    python -m fetpype.dicom_selection "$series_rules" "$dicom" "$report" -- "${conversion_args[@]}"
else
    dcm2bids -d "$dicom" "${conversion_args[@]}"
fi

stacks=("$anat"/*_T2w.nii "$anat"/*_T2w.nii.gz)
if (( ${#stacks[@]} == 0 )); then
    echo "No T2w stacks were imported into $anat. Check the Dcm2Bids mapping." >&2
    exit 1
fi

exec fetpype_run --data "$bids" --out "$bids" \
    --subjects "$participant" "${process_session[@]}" \
    --config "$repo/configs/nesvor_multibounti_docker.yaml"
