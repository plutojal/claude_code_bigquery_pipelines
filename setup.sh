#!/usr/bin/env bash
set -euo pipefail

PROJECT="product-analytics-389809"
GCS_LOCATION="us-central1"
BQ_LOCATION="US"
BUCKET="gs://${PROJECT}-retail-stores"
DATASET="retail_stores"
TABLE="stores_normalized"
SCHEMA="schemas/stores_normalized.json"

echo "Creating GCS bucket ${BUCKET}..."
gsutil mb -p "${PROJECT}" -l "${GCS_LOCATION}" "${BUCKET}" || echo "Bucket already exists, skipping."

echo "Creating raw/ and normalized/ prefixes..."
gsutil cp /dev/null "${BUCKET}/raw/.keep"
gsutil cp /dev/null "${BUCKET}/normalized/.keep"

echo "Creating BigQuery dataset ${DATASET}..."
bq --project_id="${PROJECT}" mk \
  --dataset \
  --location="${BQ_LOCATION}" \
  --description="Retail store scrape data" \
  "${PROJECT}:${DATASET}" || echo "Dataset already exists, skipping."

echo "Creating BigQuery table ${DATASET}.${TABLE}..."
bq --project_id="${PROJECT}" mk \
  --table \
  --description="Normalized store records" \
  "${PROJECT}:${DATASET}.${TABLE}" \
  "${SCHEMA}" || echo "Table already exists, skipping."

echo "Creating BigQuery table ${DATASET}.unified_businesses..."
bq --project_id="${PROJECT}" mk \
  --table \
  --description="Matched businesses across scrape, Sarene, and Salesforce" \
  "${PROJECT}:${DATASET}.unified_businesses" \
  "schemas/unified_businesses.json" || echo "Table already exists, skipping."

echo "Creating BigQuery table ${DATASET}.zip_lookup..."
bq --project_id="${PROJECT}" mk \
  --table \
  --description="US ZIP code lookup: lat, lng, pop density, area type" \
  "${PROJECT}:${DATASET}.zip_lookup" \
  "schemas/zip_lookup.json" || echo "Table already exists, skipping."

# Use stdin redirection (<) instead of "$(cat ...)" so that SQL comments (--)
# and single-quoted strings are not misinterpreted as bq CLI flags.
_run_view() {
  local file="$1"
  local name
  name=$(basename "${file}" .sql)
  echo "Creating view ${name}..."
  bq --project_id="${PROJECT}" query --nouse_legacy_sql < "${file}" \
    || echo "Warning: could not create ${name} — run ${file} manually in BigQuery console."
}

echo "Creating views (requires access to source datasets)..."
_run_view sql/views/v_sarene_comparison_daily.sql
_run_view sql/views/v_salesforce_accounts.sql
_run_view sql/views/v_sarene_clean.sql
_run_view sql/views/v_salesforce_clean.sql
_run_view sql/views/v_sarene_order_summary.sql
_run_view sql/views/v_dashboard_stores.sql
_run_view sql/views/v_dashboard_chain_overview.sql

echo "Done."
