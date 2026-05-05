"""
Load US ZIP code data into BigQuery retail_stores.zip_lookup.

Uses the free simplemaps US ZIP codes basic CSV:
  https://simplemaps.com/data/us-zips  →  download uszips.csv

Usage:
  python load_zip_lookup.py uszips.csv
"""

import csv
import sys
from google.cloud import bigquery

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
TABLE   = "zip_lookup"
TABLE_REF = f"{PROJECT}.{DATASET}.{TABLE}"

SCHEMA = [
    bigquery.SchemaField("zip",              "STRING",  mode="REQUIRED"),
    bigquery.SchemaField("city",             "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("state",            "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("county",           "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("latitude",         "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("longitude",        "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("pop_density_sqmi", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("population",       "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("area_type",        "STRING",  mode="NULLABLE"),
]


def _area_type(density: float) -> str:
    if density >= 10_000:
        return "urban"
    if density >= 1_000:
        return "suburban"
    return "rural"


def _parse_row(row: dict) -> dict | None:
    try:
        lat  = float(row["lat"])
        lng  = float(row["lng"])
        dens = float(row.get("density") or 0)
        pop  = int(float(row.get("population") or 0))
    except (ValueError, KeyError):
        return None

    return {
        "zip":              row["zip"].strip().zfill(5),
        "city":             row.get("city", "").strip() or None,
        "state":            row.get("state_id", "").strip().upper() or None,
        "county":           row.get("county_name", "").strip() or None,
        "latitude":         lat,
        "longitude":        lng,
        "pop_density_sqmi": dens,
        "population":       pop,
        "area_type":        _area_type(dens),
    }


def load(csv_path: str) -> None:
    print(f"Reading {csv_path}...")
    rows: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            parsed = _parse_row(raw)
            if parsed:
                rows.append(parsed)

    print(f"Parsed {len(rows):,} ZIP codes. Loading to {TABLE_REF}...")
    client = bigquery.Client(project=PROJECT)
    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_json(rows, TABLE_REF, job_config=job_config)
    job.result()
    print(f"Done — {len(rows):,} rows loaded into {TABLE_REF}.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python load_zip_lookup.py <path/to/uszips.csv>")
        sys.exit(1)
    load(sys.argv[1])
