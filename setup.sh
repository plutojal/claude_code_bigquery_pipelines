#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
REGION="us-central1"
BUCKET="gs://${PROJECT}-retail-stores"
DATASET="retail_stores"
TABLE="stores_normalized"
SCHEMA="schemas/stores_normalized.json"

echo "Creating GCS bucket ${BUCKET}..."
gsutil mb -p "${PROJECT}" -l "${REGION}" "${BUCKET}" || echo "Bucket already exists, skipping."

echo "Creating raw/ and normalized/ prefixes..."
gsutil cp /dev/null "${BUCKET}/raw/.keep"
gsutil cp /dev/null "${BUCKET}/normalized/.keep"

echo "Creating BigQuery dataset ${DATASET}..."
bq --project_id="${PROJECT}" mk \
  --dataset \
  --location="${REGION}" \
  --description="Retail store scrape data" \
  "${PROJECT}:${DATASET}" || echo "Dataset already exists, skipping."

echo "Creating BigQuery table ${DATASET}.${TABLE}..."
bq --project_id="${PROJECT}" mk \
  --table \
  --description="Normalized store records" \
  "${PROJECT}:${DATASET}.${TABLE}" \
  "${SCHEMA}" || echo "Table already exists, skipping."

echo "Creating views (requires access to source datasets)..."
bq --project_id="${PROJECT}" query --nouse_legacy_sql \
  "$(cat sql/views/v_sarene_comparison_daily.sql)" \
  || echo "Warning: could not create v_sarene_comparison_daily — run sql/views/v_sarene_comparison_daily.sql manually in BigQuery console."
bq --project_id="${PROJECT}" query --nouse_legacy_sql \
  "$(cat sql/views/v_salesforce_accounts.sql)" \
  || echo "Warning: could not create v_salesforce_accounts — run sql/views/v_salesforce_accounts.sql manually in BigQuery console."

echo "Done."
