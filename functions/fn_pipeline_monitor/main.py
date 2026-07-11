"""Scheduled infrastructure monitor — writes health failures to pipeline_errors.

This catches the failures the pipeline functions CAN'T self-report: a scheduler
that silently never fires, so the function never runs and never logs an error.
It reads the run-heartbeat table (pipeline_runs) each function writes on a
successful run; if a component's newest heartbeat is missing or older than its
threshold, it logs to pipeline_errors — where the external Slack notifier picks
it up like any other alert.

Runs on its own daily schedule (see create_scheduler.sh).
"""

import functions_framework
from google.cloud import bigquery

from error_log import log_error, log_run

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
RUNS_TABLE = "pipeline_runs"

# (component, max_age_hours) — how recently each component must have run.
# Both matching (daily 12:00 UTC) and the geocoder (daily 14:00 UTC) run daily,
# so 26h gives one missed run of slack before alerting.
FRESHNESS_CHECKS = [
    ("fn-retail-store-matching", 26),
    ("fn-store-geocoder", 26),
]


def _check_freshness(client: bigquery.Client) -> int:
    issues = 0
    for component, max_age in FRESHNESS_CHECKS:
        sql = f"""
            SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(finished_at), HOUR) AS age_hours
            FROM `{PROJECT}.{DATASET}.{RUNS_TABLE}`
            WHERE component = @component AND status = 'success'
        """
        job = client.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("component", "STRING", component)]
        ))
        age = next(job.result()).age_hours

        if age is None:
            log_error(
                component, "no_successful_run",
                f"No successful run of {component} on record — scheduler may never have fired.",
                severity="ERROR", test_type="infrastructure",
                context={"component": component}, client=client,
            )
            issues += 1
        elif age > max_age:
            log_error(
                component, "stale_run",
                f"{component} last ran {age}h ago (> {max_age}h) — scheduler may not be firing.",
                severity="ERROR", test_type="infrastructure",
                context={"component": component, "age_hours": age, "max_age_hours": max_age},
                client=client,
            )
            issues += 1
        else:
            print(f"{component}: last run {age}h ago — OK.")
    return issues


@functions_framework.http
def fn_pipeline_monitor(request):
    client = bigquery.Client(project=PROJECT)
    try:
        issues = _check_freshness(client)
    except Exception as exc:  # noqa: BLE001 — record infra failure, then re-raise
        log_error(
            "fn-pipeline-monitor", "unhandled_exception", str(exc),
            severity="ERROR", test_type="infrastructure", client=client,
        )
        raise
    # The monitor heartbeats too, so assert_pipeline_runs_fresh can catch the
    # watcher itself dying — the one failure the monitor can't self-report.
    log_run("fn-pipeline-monitor", "success", client=client)
    return f"OK ({issues} freshness issue(s) logged)", 200
