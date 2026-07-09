#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
FUNCTION_NAME="fn-retail-store-matching"
SOURCE_DIR="functions/fn_retail_store_matching"

echo "Deploying ${FUNCTION_NAME}..."

gcloud functions deploy "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --runtime=python311 \
  --source="${SOURCE_DIR}" \
  --entry-point=fn_retail_store_matching \
  --trigger-http \
  --no-allow-unauthenticated \
  --memory=4Gi \
  --timeout=540s

FUNCTION_URI=$(gcloud functions describe "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --format="value(serviceConfig.uri)")

echo ""
echo "Deployed: ${FUNCTION_URI}"
echo ""
echo "To create the daily Cloud Scheduler job:"
echo "  gcloud scheduler jobs create http retail-store-matching-daily \\"
echo "    --project=${PROJECT} \\"
echo "    --location=${REGION} \\"
echo "    --schedule='0 6 * * *' \\"
echo "    --uri=${FUNCTION_URI} \\"
echo "    --oidc-service-account-email=<YOUR_SERVICE_ACCOUNT>@${PROJECT}.iam.gserviceaccount.com"
