# Architecture & Handover Guide

This document explains the **whole pipeline end to end** — starting from a
scrape file landing in a bucket, through matching and geocoding, to the final
dashboard tables — and then describes **every file and folder** in this repo so
a new team member can find their way around.

If you read one thing, read this. The [RUNBOOK](RUNBOOK.md) has the commands;
this explains *why* they exist and how the pieces fit together.

---

## 1. The big picture

The pipeline answers one question: **"Which retail stores are already customers
of our distributors, and where are they?"** It does this by combining scraped
store listings with distributor account/sales data, then enriching with
location coordinates.

```
                                         ┌─────────────────────────────┐
   scraper (external, not in this repo)  │  Google Cloud Storage bucket │
   drops a normalized CSV  ───────────►  │  .../normalized/*.csv        │
                                         └──────────────┬──────────────┘
                                                        │ (file finalized = trigger)
                                                        ▼
                                    ┌──────────────────────────────────┐
                                    │ fn-retail-store-scrape-processor  │  Cloud Function
                                    │ parses CSV, builds store_id,      │
                                    │ upserts rows                      │
                                    └──────────────┬───────────────────┘
                                                   ▼
                                     ┌────────────────────────────┐
                                     │ BigQuery: stores_normalized │  raw scraped stores
                                     └──────────────┬─────────────┘
                                                    │
                            ┌───────────────────────┴───────────────────────┐
                            │   DATAFORM (separate repo) builds int_* models │
                            │   int_matcher_input   (stores to match)        │
                            │   int_distributor_account_universe (accounts)  │
                            └───────────────────────┬───────────────────────┘
                                                    ▼
                                    ┌──────────────────────────────────┐
                                    │ fn-retail-store-matching          │  Cloud Function
                                    │ fuzzy-matches stores → accounts   │  (daily + weekly)
                                    └──────────────┬───────────────────┘
                                                   ▼
                                     ┌────────────────────────────┐
                                     │ BigQuery: account_universe  │  stores + match flags
                                     └──────────────┬─────────────┘
                                                    │
                            ┌───────────────────────┴───────────────────────┐
                            │   DATAFORM builds mart_* models                │
                            │   mart_stores / mart_store_locator (dashboard) │
                            └───────────────────────┬───────────────────────┘
                                                    │  (rows missing lat/lng)
                                                    ▼
                                    ┌──────────────────────────────────┐
                                    │ fn-store-geocoder                 │  Cloud Function
                                    │ Google Maps geocode → lat/lng/zip │  (daily)
                                    └──────────────┬───────────────────┘
                                                   ▼
                                     ┌────────────────────────────┐
                                     │ BigQuery: store_geocodes    │  lookup table
                                     └──────────────┬─────────────┘
                                                    │  (mart_stores COALESCEs this back in)
                                                    ▼
                                          dashboards / reporting
```

**Three systems are involved — know which is which:**

| System | What lives there | This repo? |
|---|---|---|
| **This git repo** | Cloud Function code, table schemas, deploy & scheduler scripts | ✅ yes |
| **Dataform** (separate repo) | The `int_*` and `mart_*` SQL models + assertions | ❌ no |
| **GCP Console** | The running functions, scheduler jobs, the bucket, the Maps API key | ❌ no (but created by this repo's scripts) |

---

## 2. The flow, stage by stage

### Stage 0 — A scrape file lands in the bucket

The scraper (a separate system, not in this repo) writes a CSV to
`gs://product-analytics-389809-retail-stores/normalized/`. The CSV columns are
the store fields — see `test_data/sample_stores.csv` for the exact header
(brand, store_name, address, house_number, road, parsed_city, zip, state,
is_chain, rating, review_count, …).

**We wait for these files** — everything downstream is triggered by their
arrival. No file, no run.

### Stage 1 — Scrape processor normalises the file

The instant a file is finalized in the bucket, GCS fires an event that triggers
**`fn-retail-store-scrape-processor`**. It:

1. Downloads the CSV.
2. Parses each row and computes a **`store_id`** — an MD5 hash of the
   normalised name + house number + road + city + state (see
   `_lib.py:make_store_id`). This is the stable identity of a store: the same
   physical store always hashes to the same id, so re-scrapes update rather
   than duplicate.
3. Upserts (MERGE on `store_id`) into the **`stores_normalized`** BigQuery table.

> It only reacts to files under the `normalized/` prefix — anything dropped
> elsewhere in the bucket is ignored.

### Stage 2 — Dataform prepares the matching inputs

Dataform (separate repo) builds two intermediate tables the matcher reads:

- **`int_matcher_input`** — the deduplicated, latest-scrape-per-store view of
  `stores_normalized`, unioned with NJ ABC hemp-license records. This is the
  list of *stores to match*.
- **`int_distributor_account_universe`** — one row per distributor account
  (currently Sarene), with `case_volume`, `revenue`, `revenue_per_case` sales
  metrics. This is the list of *accounts to match against*. New distributors
  are added here by UNION-ing in more staging tables — no function code change.

### Stage 3 — Matching function links stores to accounts

**`fn-retail-store-matching`** runs on a schedule (not on file arrival). For
every store it walks through matching layers, cheapest/most-certain first:

1. **exact address** → 2. **exact name** (same state/city) → 3. **fuzzy name
   within same zip** → 4. **fuzzy name/address within state**.

Each store gets a `distributor_matches` record (matched yes/no, confidence,
which layer, and the account's sales metrics). Results are written to
**`account_universe`**. It has two modes:

- `incremental` (daily) — only stores not yet in `account_universe`.
- `full` (weekly) — re-matches everything against fresh distributor data.

The matching logic has hard-won false-positive guards (city gating, house-number
vetoes, cannabis-store exclusion) — see comments in `main.py`.

### Stage 4 — Dataform builds the dashboard models

Dataform builds **`mart_stores`** / **`mart_store_locator`** from
`account_universe`, joining `zip_lookup` for zip-centroid coordinates and
`int_sarene_order_summary` for order history. This is what the dashboards read.

### Stage 5 — Geocoder fills missing coordinates

Many stores have no lat/lng (incomplete zips in the source). **`fn-store-geocoder`**
runs daily: it finds `mart_stores` rows where `latitude IS NULL`, calls the
Google Maps Geocoding API, and writes lat/lng/zip to **`store_geocodes`**.
`mart_stores` then `COALESCE`s those coordinates back in. It writes one row at a
time (resumable) and negative-caches addresses Google can't resolve so they're
never retried.

---

## 3. Every file and folder

### Cloud Functions — `functions/`

Each subfolder is one deployable Cloud Function (its own `main.py` +
`requirements.txt`, deployed by the matching `deploy_*.sh` script).

| Path | What it is |
|---|---|
| `functions/fn_retail_store_scrape_processor/main.py` | **Stage 1.** GCS-triggered. Downloads a dropped CSV and upserts it into `stores_normalized`. |
| `functions/fn_retail_store_scrape_processor/_lib.py` | Shared CSV parsing + the `make_store_id()` hashing logic. Imported by both the function above **and** `insert_normalized.py`, so the store_id rule is defined in exactly one place. |
| `functions/fn_retail_store_scrape_processor/requirements.txt` | Python deps for that function. |
| `functions/fn_retail_store_matching/main.py` | **Stage 3.** The matcher. Normalisation helpers, the matching layers, false-positive guards, distributor loading, and the staging+MERGE writer. The biggest/most important file in the repo. |
| `functions/fn_retail_store_matching/requirements.txt` | Deps (adds `rapidfuzz` for fuzzy matching). |
| `functions/fn_store_geocoder/main.py` | **Stage 5.** The geocoder. Calls Google Maps, streams results row-by-row into `store_geocodes`, negative-caches failures. |
| `functions/fn_store_geocoder/requirements.txt` | Deps (adds `requests`). |

### Table schemas — `schemas/`

BigQuery table definitions in JSON (used by `setup.sh` and `bq` commands to
create/update tables).

| Path | Table it defines |
|---|---|
| `schemas/stores_normalized.json` | `stores_normalized` — raw scraped stores (Stage 1 output). |
| `schemas/account_universe.json` | `account_universe` — matched stores with `distributor_matches` (Stage 3 output). This is the schema you edit when match output columns change. |
| `schemas/store_geocodes.json` | `store_geocodes` — geocode lookup (Stage 5 output). |
| `schemas/zip_lookup.json` | `zip_lookup` — US zip → lat/lng/pop-density reference data. |
| `schemas/unified_businesses.json` | **LEGACY / unused.** An old table replaced by `account_universe`. Kept only for history; safe to ignore. |

### Deploy & infra scripts (repo root)

| Path | What it does |
|---|---|
| `setup.sh` | One-time (idempotent) infra: creates the GCS bucket, the BigQuery dataset, and all base tables from the schemas. Run once when standing up the project. |
| `deploy_fn_retail_store_scrape_processor.sh` | Deploys the scrape processor (wired to the bucket's file-finalized trigger). |
| `deploy_fn_retail_store_matching.sh` | Deploys the matching function (HTTP trigger). |
| `deploy_fn_store_geocoder.sh` | Deploys the geocoder. Prompts (hidden) for the Maps API key and sets it as an env var. |
| `create_scheduler.sh` | Creates/updates all three Cloud Scheduler jobs. Idempotent — safe to re-run. |

### One-off / helper scripts (repo root)

These are run manually by a human, not on a schedule.

| Path | What it does |
|---|---|
| `insert_normalized.py` | Manually load a scrape CSV into `stores_normalized` from your machine — the same logic as the scrape processor, for backfills or testing. Usage: `python insert_normalized.py path/to/file.csv`. |
| `load_zip_lookup.py` | One-time load of the US zip reference data into `zip_lookup`. Usage: `python load_zip_lookup.py uszips.csv` (download from simplemaps.com/data/us-zips). |
| `backfill_zips.py` | Backfills missing zips/coords in `stores_normalized` using the free US Census geocoder (no API key). Predates the Google geocoder; kept for bulk zip backfills. |

### Legacy SQL — `sql/`

| Path | What it is |
|---|---|
| `sql/tables/account_universe.sql` | The `account_universe` DDL in SQL form. Kept in sync with `schemas/account_universe.json` as human-readable reference; the JSON schema is what the tooling actually uses. |

> Older `sql/views/v_*.sql` files were **deleted** when those views moved to
> Dataform. If you're looking for view logic, it's in the Dataform repo now.

### Other

| Path | What it is |
|---|---|
| `requirements.txt` | Deps for the root-level helper scripts (not the functions — those have their own). |
| `test_data/sample_stores.csv` | Example scrape CSV showing the exact expected column format. Good for testing `insert_normalized.py` or the scrape processor. |
| `README.md` | Project intro + Maps API key retrieval. |
| `docs/RUNBOOK.md` | Day-to-day operational commands and error fixes. |
| `docs/ARCHITECTURE.md` | This document. |

---

## 4. The BigQuery tables (glossary)

All in dataset `product-analytics-389809.retail_stores`.

| Table | Owner | Contents |
|---|---|---|
| `stores_normalized` | this repo (scrape processor) | Raw scraped stores, one row per `store_id`. |
| `int_matcher_input` | Dataform | Stores prepped for matching. |
| `int_distributor_account_universe` | Dataform | Distributor accounts + sales metrics. |
| `account_universe` | this repo (matching fn) | Stores + `distributor_matches` + flags. |
| `store_geocodes` | this repo (geocoder fn) | Geocoded lat/lng/zip lookup. |
| `zip_lookup` | this repo (`load_zip_lookup.py`) | US zip reference data. |
| `mart_stores` / `mart_store_locator` | Dataform | Final dashboard models. |

---

## 4a. Error logging & Slack alerting

Monitoring is split in two on purpose:

- **This repo writes failures to a table.** Each function calls
  `log_error(...)` (from `error_log.py`, an identical copy in every function
  folder) when a check fails — either a **function** test (a data-quality check
  inside the function, e.g. "zero stores matched") or an **infrastructure**
  test (run/scheduler/deploy health). The row lands in
  `retail_stores.pipeline_errors` with `notified = FALSE`.

- **A separate function, outside this codebase, sends Slack messages.** It polls
  `pipeline_errors` for `notified = FALSE`, posts each to the #data-pipelines
  channel, and sets `notified = TRUE`. Keeping it external means **no Slack
  webhook or secret lives in this repo.**

The contract between the two is just the table. The Slack notifier does roughly:

```sql
-- read the backlog
SELECT * FROM `product-analytics-389809.retail_stores.pipeline_errors`
WHERE notified = FALSE OR notified IS NULL
ORDER BY occurred_at;

-- after posting, mark them done
UPDATE `product-analytics-389809.retail_stores.pipeline_errors`
SET notified = TRUE
WHERE error_id IN (...posted ids...);
```

To log a failure from inside a function:

```python
from error_log import log_error

log_error(
    component="fn-retail-store-matching",
    check_name="zero_stores_matched",
    message="Full run matched 0 stores — distributor load likely empty.",
    severity="ERROR",
    test_type="function",
    context={"mode": "full", "stores": 5891, "matched": 0},
)
```

`log_error` never raises — a logging failure must not crash the pipeline.

## 5. Notes for the next maintainer

Things that will bite you if you don't know them:

- **Schema changes to `account_universe`'s nested `distributor_matches` can't
  use `ALTER TABLE`.** Adding a field needs `bq update --schema`; removing a
  field needs dropping & recreating the table (BigQuery won't remove nested
  fields via update). A recreate is safe — the matching function rebuilds the
  whole table on a `full` run.

- **The matching function and `int_distributor_account_universe` must agree on
  column names.** They drifted once (`case_volume` vs `net_case_volume`) and the
  weekly run 500'd. The Dataform assertion
  `assert_dsd_int_distributor_account_universe` now guards those columns — if it
  fails, fix the *model*, don't rename columns to match a mistake.

- **`NUMERIC` columns come back as Python `Decimal` and break JSON writes.** The
  matching query wraps distributor metrics in `CAST(... AS FLOAT64)` for exactly
  this reason. Keep that pattern for any new numeric column.

- **Functions are authenticated-only.** No public access (org policy blocks it
  anyway). Humans call with `gcloud auth print-identity-token`; schedulers use
  OIDC. The geocoder scheduler authenticates as the **default compute service
  account** because granting `run.invoker` needs `setIamPolicy`, which is
  restricted in this project. See `create_scheduler.sh`.

- **The Maps API key is not in the repo.** It's stored as an env var on the
  deployed geocoder function and retrievable from GCP any time (see README).
  Secret Manager was the intended home but IAM permissions blocked it.

- **`Regional Access Boundary ... Account not found` in Cloud Shell is a
  harmless gcloud display bug** (note the mangled email). It does not mean your
  command failed — read the final result line.

- **What still needs doing** (scoped but not built): Slack alerting on function
  / scheduler failures, unit tests for the matching logic, and broader Dataform
  assertions. These are the biggest risks to an unattended pipeline — a silent
  failure currently surfaces only when a dashboard looks wrong.
