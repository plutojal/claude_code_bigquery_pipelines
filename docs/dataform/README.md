# Dataform reference files

**These files do NOT run from this repo.** They are reference copies of models
that belong in the **separate data-health / data-monitoring Dataform project**.
Copy them into that project (matching its folder conventions), then run/deploy
them there. They're kept here so the definitions aren't lost and so this repo
documents the full monitoring picture.

They read tables that the Cloud Functions in this repo write (`pipeline_errors`,
`pipeline_runs`, `account_universe`) — Dataform can't build those, so it
**declares** them as external sources.

## What's here

| File | Type | Purpose |
|---|---|---|
| `declare_pipeline_errors.sqlx` | declaration | Source: the append-only error log. |
| `declare_pipeline_runs.sqlx` | declaration | Source: per-run heartbeats. |
| `declare_account_universe.sqlx` | declaration | Source: the matcher's output table. |
| `data_health_summary.sqlx` | view | One row per component: last run, staleness, 24h error count. Point a dashboard at it. |
| `assert_no_recent_pipeline_errors.sqlx` | assertion | Fails if any `ERROR` rows in the last N hours → drives the Slack alert. |
| `assert_pipeline_runs_fresh.sqlx` | assertion | Fails if a component hasn't run within its threshold (independent freshness backstop). |
| `assert_account_universe_healthy.sqlx` | assertion | Fails if the output table is empty, has duplicate `store_id`s, or matched nothing. |

## How alerting works

`pipeline_errors` is **append-only** (never refreshed), so assertions are
**time-windowed** — they fail only while there are recent errors and self-clear
once errors stop. There is deliberately no `notified` flag (a read-only
assertion can't set one). A failing assertion drives this Dataform project's
Slack notification integration.

## Keep in sync

- **Freshness thresholds** (the `max_age_hours` values) must match the schedules
  in this repo's `create_scheduler.sh`.
- **Assertion lookback window** (`LOOKBACK_HOURS` in
  `assert_no_recent_pipeline_errors.sqlx`) should match how often the Dataform
  project runs: 24 if daily, ~2 if hourly.
- Add new pipeline components (new functions) to the `expected` CTE in
  `data_health_summary.sqlx` and `assert_pipeline_runs_fresh.sqlx`.
