# Retail Store Pipelines

BigQuery pipelines for the retail store account universe: store scrape
ingestion, distributor matching, and location enrichment. Dataform handles
the downstream models (`int_*`, `mart_*`); this repo owns the raw tables,
Cloud Functions, and deploy scripts.

> **New to the pipeline?** See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for the
> day-to-day commands: invoking functions, deploying, firing schedulers,
> reading logs, and fixing common errors.

## Components

| Path | What it does |
|---|---|
| `functions/fn_retail_store_scrape_processor/` | Processes raw store scrape files into `stores_normalized` |
| `functions/fn_retail_store_matching/` | Matches stores against `int_distributor_account_universe`, writes `account_universe` |
| `functions/fn_store_geocoder/` | Geocodes `mart_stores` rows missing lat/lng via Google Maps, writes `store_geocodes` |
| `setup.sh` | Creates the GCS bucket, dataset, and base tables |
| `create_scheduler.sh` | Creates/updates the Cloud Scheduler jobs (matching daily/weekly, geocoder daily) |
| `deploy_fn_*.sh` | Per-function deploy scripts |

## Deploying the geocoder

The geocoder needs a Google Maps API key (restricted to the Geocoding API).
The key is **never stored in this repo** — the deploy script prompts for it
with hidden input and sets it as a function env var:

```bash
bash deploy_fn_store_geocoder.sh
# → Google Maps API key (blank = keep the key already deployed): [paste key]
```

On redeploys, leave the prompt blank to keep the key already configured on
the function.

## Retrieving the Maps API key

If you need the key again (it's called `store-geocoder` in GCP), you can
always read it back from Google Cloud — nothing to memorise:

```bash
# One-liner: look up the key ID by name and print the key string
gcloud services api-keys get-key-string $(gcloud services api-keys list \
  --project=product-analytics-389809 \
  --filter="displayName=store-geocoder" \
  --format="value(uid)") \
  --project=product-analytics-389809
```

Or read it off the deployed function:

```bash
gcloud functions describe fn-store-geocoder \
  --project=product-analytics-389809 --region=us-central1 --gen2 \
  --format="value(serviceConfig.environmentVariables.GOOGLE_MAPS_API_KEY)"
```

Or in the Console: **Google Maps Platform → Keys & Credentials → SHOW KEY**.
