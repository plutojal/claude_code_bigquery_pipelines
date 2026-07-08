"""Shared error/test-result logger — appends failures to retail_stores.pipeline_errors.

A SEPARATE Slack-notifier function (outside this codebase) polls this table for
rows where notified = FALSE, posts them to Slack, and marks them notified. Keeping
the Slack integration external means this repo holds no webhook URL or secret.

SYNC: this file is duplicated verbatim in every function folder
(fn_retail_store_matching, fn_store_geocoder, fn_retail_store_scrape_processor)
because each Cloud Function deploys from its own source dir. If you change one,
change all copies.
"""

import json
import uuid
from datetime import datetime, timezone

from google.cloud import bigquery

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
ERROR_TABLE = "pipeline_errors"


def log_error(
    component: str,
    check_name: str,
    message: str,
    *,
    severity: str = "ERROR",
    test_type: str = "function",
    context: dict | None = None,
    run_id: str | None = None,
    client: "bigquery.Client | None" = None,
) -> bool:
    """Append one failed-check row to pipeline_errors.

    Never raises — recording a failure must not crash the caller. Returns True
    on success, False if the write itself failed (also printed to the log).

    Args:
        component:  what raised it, e.g. "fn-retail-store-matching".
        check_name: short slug of the check, e.g. "zero_stores_matched".
        message:    human-readable detail for the Slack message.
        severity:   "ERROR" or "WARNING".
        test_type:  "function" (data check) or "infrastructure" (run health).
        context:    optional dict of structured detail (counts, ids, …).
        run_id:     correlates rows from the same run.
        client:     reuse an existing BigQuery client if you have one.
    """
    client = client or bigquery.Client(project=PROJECT)
    row = {
        "error_id": uuid.uuid4().hex,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "component": component,
        "test_type": test_type,
        "check_name": check_name,
        "severity": severity,
        "message": message,
        "context": json.dumps(context) if context is not None else None,
        "run_id": run_id,
        "notified": False,
    }
    try:
        errors = client.insert_rows_json(f"{PROJECT}.{DATASET}.{ERROR_TABLE}", [row])
        if errors:
            print(f"pipeline_errors insert failed: {errors}")
            return False
        print(f"pipeline_errors: logged {severity} {component}/{check_name}")
        return True
    except Exception as exc:  # noqa: BLE001 — logging must never break the caller
        print(f"pipeline_errors insert raised: {exc}")
        return False
