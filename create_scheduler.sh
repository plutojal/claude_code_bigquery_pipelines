#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
FUNCTION_NAME="fn-retail-store-matching"
JOB_NAME="retail-store-matching-daily"

FUNCTION_URI=$(gcloud functions describe "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --format="value(serviceConfig.uri)")

SERVICE_ACCOUNT="${PROJECT}@appspot.gserviceaccount.com"

echo "Creating/updating Cloud Scheduler job: ${JOB_NAME}..."

gcloud scheduler jobs create http "${JOB_NAME}" \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 12 * * *" \
  --uri="${FUNCTION_URI}" \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${FUNCTION_URI}" \
  --http-method=POST \
  --description="Daily store matching — runs at 12:00 UTC" \
  2>/dev/null \
  || gcloud scheduler jobs update http "${JOB_NAME}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --schedule="0 12 * * *" \
    --uri="${FUNCTION_URI}" \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --oidc-token-audience="${FUNCTION_URI}" \
    --http-method=POST

echo "Done. Job runs daily at 12:00 UTC."
echo "To trigger manually: gcloud scheduler jobs run ${JOB_NAME} --project=${PROJECT} --location=${REGION}"
