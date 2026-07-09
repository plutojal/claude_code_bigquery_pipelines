# CLAUDE.md

Guidance for Claude Code (and humans) working in this repo. Read this first,
then `docs/ARCHITECTURE.md` for the full end-to-end flow and `docs/RUNBOOK.md`
for day-to-day commands.

## What this is

BigQuery pipelines for the retail-store **account universe**: ingest scraped
store listings, match them to distributor accounts, and enrich with location
coordinates. Three Cloud Functions + a monitor, feeding BigQuery tables that
Dataform turns into dashboard models.

Flow (see ARCHITECTURE for the diagram):
`bucket CSV → scrape processor → stores_normalized → [Dataform int_*] →
matching → account_universe → [Dataform mart_*] → geocoder → store_geocodes`

## Three systems — know which owns what

| System | Owns | In this repo? |
|---|---|---|
| **This repo** | Cloud Function code, table schemas, deploy/scheduler scripts, tests | yes |
| **Dataform** (separate repo) | `int_*` / `mart_*` models and assertions | no |
| **GCP Console** | running functions, scheduler jobs, the bucket, the Maps API key | no (created by this repo's scripts) |

`docs/dataform/` holds **reference copies** of Dataform files that belong in the
separate Dataform project — they are not run from here.

## Layout

- `functions/<name>/main.py` — one deployable Cloud Function each; deploys from
  its own folder, so each folder is **self-contained**.
- `functions/*/error_log.py` — shared logging helper (`log_error`, `log_run`),
  an **identical copy in every function folder**. Change one → change all.
- `functions/fn_retail_store_scrape_processor/_lib.py` — CSV/`store_id` logic,
  shared with `insert_normalized.py`.
- `schemas/*.json` — BigQuery table definitions (drive `setup.sh` and `bq` calls).
- `sql/tables/*.sql` — human-readable DDL kept in sync with the JSON schemas.
- `deploy_fn_*.sh` — per-function deploy; `create_scheduler.sh` — all scheduler
  jobs (idempotent); `setup.sh` — one-time infra (bucket, dataset, tables).
- `tests/` — pytest suite (pure logic, no cloud deps); CI in `.github/workflows/`.
- Root `*.py` (`insert_normalized.py`, `load_zip_lookup.py`, `backfill_zips.py`)
  — manual helper scripts, run by a human, not on a schedule.

## Constants

Every function hardcodes `PROJECT = "product-analytics-389809"` and
`DATASET = "retail_stores"`. Region is `us-central1`, BQ location `US`.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

No cloud access needed — BigQuery/HTTP are mocked. Add a test with any logic
change to the matcher; the regression cases for known false positives live in
`tests/test_matching_layers.py`.

## Deploying

Each function has its own script; deploy after pulling. Functions are
**authenticated-only** (`--no-allow-unauthenticated`) — never re-add public
access (the org blocks it and it needs `setIamPolicy`, which we lack). Callers
use identity tokens; schedulers use OIDC.

```bash
bash deploy_fn_store_geocoder.sh    # prompts (hidden) for the Maps API key; blank = keep existing
bash create_scheduler.sh            # (re)creates all scheduler jobs
```

## Gotchas (all learned the hard way — don't rediscover them)

- **`gcloud` in Cloud Shell prints `Regional Access Boundary ... Account not
  found` with a mangled email.** Harmless display bug. Read the final result
  line only.
- **Identity-token calls:** capture the token to a var and check it starts with
  `eyJ` — inline `$(gcloud auth print-identity-token)` can capture gcloud noise
  and corrupt the header. Tokens expire hourly.
- **Nested RECORD schema changes** (e.g. `account_universe.distributor_matches`)
  can't use `ALTER TABLE`. Adding a field → `bq update --schema`; removing one →
  drop & recreate (safe: a `full` matching run rebuilds the table).
- **`NUMERIC` columns return Python `Decimal`, which breaks `json.dumps`.** Wrap
  distributor metrics in `CAST(... AS FLOAT64)` in the query. Keep that pattern.
- **The matcher's SQL and Dataform's `int_distributor_account_universe` must
  agree on column names.** They drifted once and the weekly run 500'd. A
  Dataform assertion guards those columns — if it fails, fix the *model*, don't
  rename to match a mistake.
- **Scheduler → function invoke** needs `run.invoker`, which requires
  `setIamPolicy` (blocked here). Workaround in `create_scheduler.sh`: the
  geocoder and monitor jobs authenticate as the **default compute service
  account**, which can invoke via its project role.
- **Maps API key is not in the repo** — it's an env var on the deployed geocoder,
  retrievable from GCP any time (see README). Secret Manager was blocked by IAM.

## Monitoring model

Failures are **appended** to `pipeline_errors` via `log_error(...)`; every
successful run writes a heartbeat to `pipeline_runs` via `log_run(...)`.
`fn-pipeline-monitor` (daily) flags a component whose heartbeat is missing or
>26h old. The **data-monitoring Dataform project** reads `pipeline_errors` with a
**time-windowed assertion** (fail if any `ERROR` rows in the last N hours) and
drives Slack. `pipeline_errors` is append-only — there is deliberately **no
`notified` flag** (a read-only assertion can't set one; windowing self-clears
instead). Reference assertion + declaration are in `docs/dataform/`.

## Working conventions

- Match the surrounding code style; keep comment density consistent.
- Commit/push only when asked. Don't commit data files (`*.csv`) — they're
  gitignored / left untracked.
- When changing a table's shape: update the JSON schema, the `sql/tables` DDL,
  and every function that reads/writes it — they must stay in lockstep.
