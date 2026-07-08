#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
FUNCTION_NAME="fn-pipeline-monitor"
SOURCE_DIR="functions/fn_pipeline_monitor"

echo "Deploying ${FUNCTION_NAME}..."

gcloud functions deploy "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --runtime=python311 \
  --source="${SOURCE_DIR}" \
  --entry-point=fn_pipeline_monitor \
  --trigger-http \
  --no-allow-unauthenticated \
  --memory=256Mi \
  --timeout=120s

FUNCTION_URI=$(gcloud functions describe "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --format="value(serviceConfig.uri)")

echo ""
echo "Deployed: ${FUNCTION_URI}"
echo ""
echo "To create the daily Cloud Scheduler job, run: bash create_scheduler.sh"
