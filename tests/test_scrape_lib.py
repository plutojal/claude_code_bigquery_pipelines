"""Tests for the shared CSV/store_id logic (functions/.../_lib.py)."""

from pathlib import Path

from _modules import scrape_lib

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "test_data" / "sample_stores.csv"


# --- store_id -------------------------------------------------------------

def test_make_store_id_is_deterministic():
    a = scrape_lib.make_store_id("CVS Pharmacy", "123", "Main Street", "Los Angeles", "CA")
    b = scrape_lib.make_store_id("CVS Pharmacy", "123", "Main Street", "Los Angeles", "CA")
    assert a == b


def test_make_store_id_normalises_abbreviations():
    # "St" and "Street" must hash to the same id (road abbreviation expansion)
    st = scrape_lib.make_store_id("Joe's", "10", "Main St", "Newark", "NJ")
    street = scrape_lib.make_store_id("Joe's", "10", "Main Street", "Newark", "NJ")
    assert st == street


def test_make_store_id_differs_on_different_stores():
    a = scrape_lib.make_store_id("CVS", "123", "Main St", "Newark", "NJ")
    b = scrape_lib.make_store_id("CVS", "456", "Main St", "Newark", "NJ")
    assert a != b


# --- parse_bool -----------------------------------------------------------

def test_parse_bool_truthy():
    for v in ("true", "1", "yes", "Y", "TRUE"):
        assert scrape_lib.parse_bool(v) is True


def test_parse_bool_falsy():
    for v in ("false", "0", "no", "n", ""):
        assert scrape_lib.parse_bool(v) is False


def test_parse_bool_unknown_is_none():
    assert scrape_lib.parse_bool("maybe") is None


# --- load_csv -------------------------------------------------------------

def test_load_csv_parses_sample():
    rows = scrape_lib.load_csv(str(SAMPLE_CSV), source_file="sample_stores.csv")
    assert len(rows) >= 1
    row = rows[0]
    # every row gets an id, a source_file tag, and typed quality signals
    assert row["store_id"]
    assert row["source_file"] == "sample_stores.csv"
    assert isinstance(row["is_chain"], bool)
    assert isinstance(row["rating"], float)
    assert isinstance(row["review_count"], int)
    assert row["ingested_at"]
