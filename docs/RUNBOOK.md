# Operations Runbook

Day-to-day commands for running and maintaining the retail store pipeline.
Everything here runs from **Cloud Shell** in project `product-analytics-389809`
(you already have the gcloud/bq/git tools there — no local setup needed).

Shared constants used throughout:

```bash
PROJECT=product-analytics-389809
REGION=us-central1
```

---

## Table of contents

1. [The pipeline at a glance](#the-pipeline-at-a-glance)
2. [Getting the code](#getting-the-code)
3. [Invoking a function manually](#invoking-a-function-manually)
4. [The matching function](#the-matching-function)
5. [The geocoder function](#the-geocoder-function)
6. [Deploying a function](#deploying-a-function)
7. [The schedulers](#the-schedulers)
8. [Watching logs](#watching-logs)
9. [Common errors and fixes](#common-errors-and-fixes)

---

## The pipeline at a glance

| Function | What it does | Writes to | Schedule |
|---|---|---|---|
| `fn-retail-store-scrape-processor` | Normalises raw scrape files | `stores_normalized` | (triggered by upload) |
| `fn-retail-store-matching` | Matches stores to distributor accounts | `account_universe` | daily 12:00 UTC (incremental), Sun 12:00 UTC (full) |
| `fn-store-geocoder` | Geocodes stores missing lat/lng | `store_geocodes` | daily 14:00 UTC |
| `fn-pipeline-monitor` | Freshness backstop; logs stale runs | `pipeline_errors` | daily 15:00 UTC |

Dataform builds the `int_*` and `mart_*` models that sit between and after
these functions. This repo owns the raw tables, the functions, and the
deploy/scheduler scripts.

---

## Getting the code

```bash
cd ~/claude_code_bigquery_pipelines || \
  git clone https://github.com/plutojal/claude_code_bigquery_pipelines.git ~/claude_code_bigquery_pipelines
cd ~/claude_code_bigquery_pipelines
git pull origin main
```

Always `git pull` before deploying so you ship the latest committed code.

---

## Invoking a function manually

All functions are **authenticated-only** — a plain `curl` gets `401/403`. You
must send an identity token. Because gcloud in Cloud Shell sometimes prints
noise that corrupts inline `$(...)`, capture the token to a variable and
sanity-check it first (a real token starts with `eyJ`):

```bash
TOKEN=$(gcloud auth print-identity-token 2>/dev/null | grep -o 'eyJ[A-Za-z0-9_.-]*' | head -1)
echo "${TOKEN:0:20}"   # must print eyJ... — if blank, run: gcloud auth login
```

Then fetch the function URL and call it:

```bash
FUNCTION_URI=$(gcloud functions describe FUNCTION_NAME \
  --project=$PROJECT --region=$REGION --gen2 \
  --format="value(serviceConfig.uri)")

curl -H "Authorization: Bearer ${TOKEN}" "${FUNCTION_URI}"
```

Tokens expire after **1 hour** — re-run the `TOKEN=...` line if a call
suddenly starts returning 401.

---

## The matching function

`fn-retail-store-matching` takes a `mode` query parameter:

- `mode=incremental` — matches only stores not already in `account_universe` (fast, daily default)
- `mode=full` — re-matches every store against fresh distributor data (slower, weekly)

```bash
FUNCTION_URI=$(gcloud functions describe fn-retail-store-matching \
  --project=$PROJECT --region=$REGION --gen2 --format="value(serviceConfig.uri)")

# Full re-match (all stores)
curl --max-time 600 -H "Authorization: Bearer ${TOKEN}" "${FUNCTION_URI}?mode=full"
```

Verify the run populated the distributor metrics:

```bash
bq query --nouse_legacy_sql \
'SELECT store_id, dm.distributor_name, dm.case_volume, dm.revenue, dm.revenue_per_case
 FROM `product-analytics-389809.retail_stores.account_universe`,
 UNNEST(distributor_matches) AS dm
 WHERE dm.matched AND dm.revenue IS NOT NULL
 LIMIT 10'
```

---

## The geocoder function

`fn-store-geocoder` geocodes stores where `latitude IS NULL`, one row at a
time (a crash resumes where it left off — already-geocoded stores are
skipped). It takes an optional `limit`:

```bash
FUNCTION_URI=$(gcloud functions describe fn-store-geocoder \
  --project=$PROJECT --region=$REGION --gen2 --format="value(serviceConfig.uri)")

# Test on a single store
curl -H "Authorization: Bearer ${TOKEN}" "${FUNCTION_URI}?limit=1"

# Full backlog
curl --max-time 1800 -H "Authorization: Bearer ${TOKEN}" "${FUNCTION_URI}"
```

The response reports the full picture, e.g.
`OK (2691 pending, 2691 processed, 2689 geocoded, 2 unresolvable (cached), 0 transient, 0 remaining)`.

- **geocoded** — coordinates written
- **unresolvable (cached)** — Google returned no result; recorded so it's never retried
- **transient** — a temporary failure; will retry on the next run

Inspect stores that could not be geocoded:

```bash
bq query --nouse_legacy_sql \
'SELECT store_id, geocode_status FROM `product-analytics-389809.retail_stores.store_geocodes`
 WHERE lat IS NULL'
```

To force a re-geocode of one store (e.g. after fixing its address upstream),
delete its row and it will be picked up next run:

```bash
bq query --nouse_legacy_sql \
'DELETE FROM `product-analytics-389809.retail_stores.store_geocodes` WHERE store_id = "STORE_ID"'
```

> Freshly written rows sit in BigQuery's streaming buffer for up to ~90 min,
> during which they cannot be deleted or updated. Wait it out if a DELETE fails.

---

## Deploying a function

Each function has its own script. Deploy after `git pull`:

```bash
bash deploy_fn_retail_store_matching.sh
bash deploy_fn_store_geocoder.sh
```

**The geocoder deploy prompts for the Maps API key** (hidden input). On a
code-only redeploy, leave it blank to keep the key already on the function:

```
Google Maps API key (blank = keep the key already deployed): [Enter]
```

See the [README](../README.md) for retrieving the key if you need to re-enter it.

> Deploys in Cloud Shell often print `Regional Access Boundary HTTP request
> failed...` lines with a mangled email. **This is a harmless gcloud display
> bug** — ignore it. Only the final line matters: `Deployed: https://...` means
> success; a line starting `ERROR:` means a real failure.

---

## The schedulers

All three scheduled jobs are (re)created by one idempotent script:

```bash
bash create_scheduler.sh
```

| Job | Schedule (UTC) | Calls |
|---|---|---|
| `retail-store-matching-daily` | `0 12 * * *` | matching `?mode=incremental` |
| `retail-store-matching-weekly` | `0 12 * * 0` | matching `?mode=full` |
| `store-geocoder-daily` | `0 14 * * *` | geocoder (full backlog) |
| `pipeline-monitor-daily` | `0 15 * * *` | monitor (freshness backstop) |

Fire a job by hand:

```bash
gcloud scheduler jobs run JOB_NAME --project=$PROJECT --location=$REGION
```

Check whether the last run succeeded:

```bash
gcloud scheduler jobs describe JOB_NAME --project=$PROJECT --location=$REGION \
  --format="yaml(lastAttemptTime, status)"
```

`status: {}` (empty) means success. A `status.code` of `7` (PERMISSION_DENIED)
or `16` (UNAUTHENTICATED) means the job's service account can't invoke the
function — see [Common errors](#common-errors-and-fixes).

---

## Watching logs

Recent log lines for a function:

```bash
gcloud functions logs read FUNCTION_NAME --project=$PROJECT --region=$REGION --limit=30
```

Live tail while a run is in progress (functions are gen2 = Cloud Run under the
hood, so per-row progress prints here, not in your curl terminal):

```bash
gcloud beta run services logs tail FUNCTION_NAME --project=$PROJECT --region=$REGION
```

---

## Running the tests

The pytest suite covers the pure logic (normalisation, matching layers +
false-positive regressions, geocoder parsing, store_id) with no cloud
dependencies. It runs in CI on every push (`.github/workflows/tests.yml`), and
you can run it locally:

```bash
pip install -r requirements-dev.txt
pytest
```

## Monitoring

Failures are logged to two BigQuery tables, not straight to Slack. The
data-monitoring Dataform project reads `pipeline_errors` with a time-windowed
assertion and drives the Slack alert (see `docs/dataform/`):

- `pipeline_errors` — append-only log of failed checks (function + infrastructure).
- `pipeline_runs` — a heartbeat per successful run; `fn-pipeline-monitor` reads
  it to catch a scheduler that never fired.

Check recent errors by hand any time (same window the assertion uses):

```bash
bq query --nouse_legacy_sql \
'SELECT occurred_at, component, check_name, severity, message
 FROM `product-analytics-389809.retail_stores.pipeline_errors`
 WHERE occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
 ORDER BY occurred_at DESC LIMIT 50'
```

Fire the freshness monitor by hand:

```bash
gcloud scheduler jobs run pipeline-monitor-daily --project=$PROJECT --location=$REGION
```

## Common errors and fixes

**`401 Unauthorized` / `403 Forbidden` on curl**
The token is missing, expired, or got corrupted by gcloud noise. Re-capture it
with the `grep 'eyJ...'` one-liner in [Invoking a function](#invoking-a-function-manually)
and confirm `echo "${TOKEN:0:20}"` prints `eyJ...`. If blank, run `gcloud auth login`.

**`Regional Access Boundary HTTP request failed... Account not found`**
Harmless gcloud display glitch (note the mangled email). Ignore it and read the
command's final result line.

**`400 Unrecognized name: <column>; Did you mean <other>?`**
The function's SQL and the Dataform table have drifted. The table needs
rebuilding (or the model needs fixing) — run the relevant Dataform model so the
physical table matches the model source, then re-fire the function. The
`assert_dsd_int_distributor_account_universe` assertion guards the columns the
matching function depends on.

**`TypeError: Object of type Decimal is not JSON serializable`**
A `NUMERIC` column reached the JSON write path. Fix is to `CAST(col AS FLOAT64)`
in the function's query (already done for the distributor metric columns).

**Scheduler job fails with code 7/16**
The job's service account lacks invoke rights on the function. Granting
`run.invoker` requires `run.services.setIamPolicy`, which is restricted in this
project — the geocoder job works around it by authenticating as the default
compute service account (see `create_scheduler.sh`). If a new job hits this,
either reuse that SA or ask a project admin to run:

```bash
gcloud functions add-invoker-policy-binding FUNCTION_NAME \
  --project=$PROJECT --region=$REGION \
  --member="serviceAccount:product-analytics-389809@appspot.gserviceaccount.com"
```

**Deploy ends with `Permission 'run.services.setIamPolicy' denied`**
The deploy succeeded; only the "make public" step failed. Functions are
authenticated-only by design (`--no-allow-unauthenticated`), so this is
expected — callers use identity tokens and schedulers use OIDC.
