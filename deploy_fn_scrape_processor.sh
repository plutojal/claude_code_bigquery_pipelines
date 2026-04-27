#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
BUCKET="product-analytics-389809-retail-stores"
FUNCTION_NAME="fn-scrape-processor"
SOURCE_DIR="functions/fn_scrape_processor"

echo "Deploying ${FUNCTION_NAME}..."

gcloud functions deploy "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --runtime=python311 \
  --source="${SOURCE_DIR}" \
  --entry-point=fn_scrape_processor \
  --trigger-event-filters="type=google.cloud.storage.object.v1.finalized" \
  --trigger-event-filters="bucket=${BUCKET}" \
  --memory=512Mi \
  --timeout=540s

echo "Deployed. Trigger: any new file in gs://${BUCKET}/normalized/"
