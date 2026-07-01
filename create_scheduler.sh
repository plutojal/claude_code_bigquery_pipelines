#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
FUNCTION_NAME="fn-retail-store-matching"
DAILY_JOB="retail-store-matching-daily"
WEEKLY_JOB="retail-store-matching-weekly"
GEOCODER_FUNCTION="fn-store-geocoder"
GEOCODER_JOB="store-geocoder-daily"

FUNCTION_URI=$(gcloud functions describe "${FUNCTION_NAME}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --format="value(serviceConfig.uri)")

SERVICE_ACCOUNT="${PROJECT}@appspot.gserviceaccount.com"

echo "Creating/updating daily incremental job: ${DAILY_JOB}..."
gcloud scheduler jobs create http "${DAILY_JOB}" \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 12 * * *" \
  --uri="${FUNCTION_URI}?mode=incremental" \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${FUNCTION_URI}" \
  --http-method=POST \
  --description="Daily — matches only new stores (incremental, 12:00 UTC)" \
  2>/dev/null \
  || gcloud scheduler jobs update http "${DAILY_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --schedule="0 12 * * *" \
    --uri="${FUNCTION_URI}?mode=incremental" \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --oidc-token-audience="${FUNCTION_URI}" \
    --http-method=POST

echo "Creating/updating weekly full-refresh job: ${WEEKLY_JOB}..."
gcloud scheduler jobs create http "${WEEKLY_JOB}" \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 12 * * 0" \
  --uri="${FUNCTION_URI}?mode=full" \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${FUNCTION_URI}" \
  --http-method=POST \
  --description="Weekly Sunday — re-matches all stores against fresh SF + Sarene data (12:00 UTC)" \
  2>/dev/null \
  || gcloud scheduler jobs update http "${WEEKLY_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --schedule="0 12 * * 0" \
    --uri="${FUNCTION_URI}?mode=full" \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --oidc-token-audience="${FUNCTION_URI}" \
    --http-method=POST

# Geocoder runs at 14:00 UTC — after the 12:00 matching run and the Dataform
# refresh, so mart_stores reflects the day's new stores before geocoding.
GEOCODER_URI=$(gcloud functions describe "${GEOCODER_FUNCTION}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --gen2 \
  --format="value(serviceConfig.uri)")

echo "Creating/updating daily geocoder job: ${GEOCODER_JOB}..."
gcloud scheduler jobs create http "${GEOCODER_JOB}" \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 14 * * *" \
  --uri="${GEOCODER_URI}" \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${GEOCODER_URI}" \
  --http-method=POST \
  --description="Daily — geocodes mart_stores rows missing lat/lng via Google Maps (14:00 UTC)" \
  2>/dev/null \
  || gcloud scheduler jobs update http "${GEOCODER_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --schedule="0 14 * * *" \
    --uri="${GEOCODER_URI}" \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --oidc-token-audience="${GEOCODER_URI}" \
    --http-method=POST

echo ""
echo "Done."
echo "  Daily  (incremental): ${DAILY_JOB}  — Mon-Sun 12:00 UTC, new stores only"
echo "  Weekly (full):        ${WEEKLY_JOB} — Sundays 12:00 UTC, re-matches everything"
echo "  Daily  (geocoder):    ${GEOCODER_JOB} — Mon-Sun 14:00 UTC, fills missing lat/lng/zip"
echo ""
echo "To trigger manually:"
echo "  Incremental: gcloud scheduler jobs run ${DAILY_JOB}  --project=${PROJECT} --location=${REGION}"
echo "  Full:        gcloud scheduler jobs run ${WEEKLY_JOB} --project=${PROJECT} --location=${REGION}"
echo "  Geocoder:    gcloud scheduler jobs run ${GEOCODER_JOB} --project=${PROJECT} --location=${REGION}"
