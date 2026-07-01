#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
FUNCTION_NAME="fn-store-geocoder"
SOURCE_DIR="functions/fn_store_geocoder"

# The Google Maps API key is passed at deploy time and stored as a function
# env var — never in the repo. Three ways to provide it:
#   1. Interactive prompt (default): bash deploy_fn_store_geocoder.sh
#      — input is hidden and doesn't reach shell history.
#   2. Env var: GOOGLE_MAPS_API_KEY=AIza... bash deploy_fn_store_geocoder.sh
#   3. Leave the prompt blank on a redeploy to keep the key already
#      configured on the deployed function (code-only update).

if [[ -n "${GOOGLE_MAPS_API_KEY:-}" ]]; then
  KEY="${GOOGLE_MAPS_API_KEY}"
  echo "Using API key from GOOGLE_MAPS_API_KEY env var."
else
  read -rsp "Google Maps API key (blank = keep the key already deployed): " KEY
  echo ""
fi

ENV_FLAG=()
if [[ -n "${KEY}" ]]; then
  ENV_FLAG=(--set-env-vars="GOOGLE_MAPS_API_KEY=${KEY}")
else
  echo "No key provided — keeping the env vars already set on the function."
fi

echo "Deploying ${FUNCTION_NAME}..."

gcloud functions deploy "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --runtime=python311 \
  --source="${SOURCE_DIR}" \
  --entry-point=fn_store_geocoder \
  --trigger-http \
  --allow-unauthenticated \
  --memory=512Mi \
  --timeout=540s \
  ${ENV_FLAG[@]+"${ENV_FLAG[@]}"}

FUNCTION_URI=$(gcloud functions describe "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --format="value(serviceConfig.uri)")

echo ""
echo "Deployed: ${FUNCTION_URI}"
echo ""
echo "To create the daily Cloud Scheduler job, run: bash create_scheduler.sh"
