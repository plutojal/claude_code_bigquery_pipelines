#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
FUNCTION_NAME="fn-store-geocoder"
SOURCE_DIR="functions/fn_store_geocoder"
SECRET_NAME="maps-api-key"

# One-time setup (before first deploy):
#   1. Create a Maps API key restricted to the Geocoding API:
#        Console → Google Maps Platform → Keys & Credentials → Create Key
#   2. Store it in Secret Manager:
#        echo -n "YOUR_KEY" | gcloud secrets create ${SECRET_NAME} \
#          --project=${PROJECT} --data-file=-
#   3. Grant the function's service account access:
#        gcloud secrets add-iam-policy-binding ${SECRET_NAME} \
#          --project=${PROJECT} \
#          --member="serviceAccount:${PROJECT}@appspot.gserviceaccount.com" \
#          --role="roles/secretmanager.secretAccessor"

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
  --set-secrets="GOOGLE_MAPS_API_KEY=${SECRET_NAME}:latest"

FUNCTION_URI=$(gcloud functions describe "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --format="value(serviceConfig.uri)")

echo ""
echo "Deployed: ${FUNCTION_URI}"
echo ""
echo "To create the daily Cloud Scheduler job, run: bash create_scheduler.sh"
