#!/usr/bin/env bash
# Download the user-selected public medicine-name dataset for local clinician
# autocomplete. The archive remains outside Git and is never imported into the
# patient/clinical database.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination="$project_root/data/medicine_catalog/india-medicines-and-drug-info-dataset.zip"
url="https://www.kaggle.com/api/v1/datasets/download/apkaayush/india-medicines-and-drug-info-dataset"

mkdir -p "$(dirname "$destination")"
curl --fail --location --retry 3 --output "$destination" "$url"
printf 'Medicine catalogue archive downloaded to %s\n' "$destination"
