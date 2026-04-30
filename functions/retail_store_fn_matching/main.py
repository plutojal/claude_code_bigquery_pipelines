"""Daily matching function — links scrape, Sarene, and Salesforce records into unified_businesses."""

import string
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import functions_framework
from google.cloud import bigquery
from rapidfuzz import fuzz

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
UNIFIED_TABLE = "unified_businesses"
BATCH_SIZE = 1000

# API layers (4 & 5) only run for these states — controls cost.
# Expand as budget allows.
TARGET_STATES = {
    "CA", "TX", "FL", "NY", "IL",
    "WA", "PA", "OH", "GA", "NC",
}

FUZZY_AUTO = 85  # auto-confirm at or above this score
FUZZY_FLAG = 65  # flag for review between FUZZY_FLAG and FUZZY_AUTO

ABBREV = {
    "st": "street", "ave": "avenue", "blvd": "boulevard", "dr": "drive",
    "rd": "road", "ln": "lane", "ct": "court", "pl": "place",
    "hwy": "highway", "pkwy": "parkway",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "ste": "suite", "apt": "apartment",
}

SOURCE_CONFIG = {
    "scrape": {
        "table": f"{PROJECT}.{DATASET}.stores_normalized",
        "id_col": "source_file",
        "name_col": "store_name",
        "address_col": "address",
        "city_col": "parsed_city",
        "state_col": "state",
        "zip_col": "zip",
    },
    "sarene": {
        "table": f"{PROJECT}.{DATASET}.v_sarene_comparison_daily",
        # TODO: confirm column names once Sarene schema is reviewed
        "id_col": "id",
        "name_col": "business_name",
        "address_col": "address",
        "city_col": "city",
        "state_col": "state",
        "zip_col": "zip",
    },
    "salesforce": {
        "table": f"{PROJECT}.{DATASET}.v_salesforce_accounts",
        "id_col": "Id",
        "name_col": "Name",
        "address_col": "BillingStreet",
        "city_col": "BillingCity",
        "state_col": "BillingState",
        "zip_col": "BillingPostalCode",
    },
}


@dataclass
class Record:
    source: str
    source_id: str
    name: str
    address: str
    city: str
    state: str
    zip_code: str
    norm: str = field(init=False)

    def __post_init__(self):
        self.norm = _normalize(f"{self.address} {self.city} {self.state}")


@dataclass
class Match:
    business_id: str
    layer: str
    confidence: Optional[float]
    status: str  # confirmed | flagged | unmatched
    scrape: Optional[Record] = None
    sarene: Optional[Record] = None
    sf: Optional[Record] = None
    place_id: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    matched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_date: str = field(default_factory=lambda: date.today().isoformat())


# ---------------------------------------------------------------------------
# Layer 1 — normalisation
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip().translate(str.maketrans("", "", string.punctuation))
    return " ".join(ABBREV.get(t, t) for t in text.split())


# ---------------------------------------------------------------------------
# Layer 3 — fuzzy
# ---------------------------------------------------------------------------

def _fuzzy(a: Record, b: Record) -> tuple[float, str]:
    score = fuzz.token_sort_ratio(a.norm, b.norm)
    if score >= FUZZY_AUTO:
        return score, "confirmed"
    if score >= FUZZY_FLAG:
        return score, "flagged"
    return score, "no_match"


# ---------------------------------------------------------------------------
# Layer 4 — Geocoding API (stubbed)
# ---------------------------------------------------------------------------

def _geocode_match(a: Record, b: Record) -> bool:
    # TODO: enable after Google Address Validation API access is granted.
    # Steps:
    #   1. Call addressvalidation.googleapis.com for both records
    #   2. Extract standardizedAddress from each response
    #   3. Re-run _normalize() on both and compare
    # import googlemaps
    # gmaps = googlemaps.Client(key=os.environ["GOOGLE_MAPS_API_KEY"])
    # ...
    return False


# ---------------------------------------------------------------------------
# Layer 5 — Places API (stubbed)
# ---------------------------------------------------------------------------

def _places_lookup(r: Record) -> tuple[Optional[str], Optional[float], Optional[float]]:
    # TODO: enable after Google Maps Places API access is granted.
    # Steps:
    #   1. Call maps.googleapis.com/maps/api/place/findplacefromtext
    #   2. Return (place_id, lat, lng) from the top candidate
    # import googlemaps
    # gmaps = googlemaps.Client(key=os.environ["GOOGLE_MAPS_API_KEY"])
    # ...
    return None, None, None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_source(client: bigquery.Client, source: str) -> list[Record]:
    cfg = SOURCE_CONFIG[source]
    query = f"""
        SELECT
            CAST({cfg['id_col']}    AS STRING) AS source_id,
            {cfg['name_col']}                  AS name,
            {cfg['address_col']}               AS address,
            {cfg['city_col']}                  AS city,
            {cfg['state_col']}                 AS state,
            {cfg['zip_col']}                   AS zip_code
        FROM `{cfg['table']}`
        WHERE {cfg['state_col']} IS NOT NULL
    """
    records = []
    for row in client.query(query).result():
        records.append(Record(
            source=source,
            source_id=row.source_id or "",
            name=row.name or "",
            address=row.address or "",
            city=row.city or "",
            state=row.state or "",
            zip_code=row.zip_code or "",
        ))
    print(f"Loaded {len(records)} records from {source}.")
    return records


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _find_match(
    candidate: Record,
    exact_index: dict[str, Record],
    state_bucket: list[Record],
    run_api: bool,
) -> tuple[Optional[Record], str, Optional[float], str, Optional[str], Optional[float], Optional[float]]:
    """Run candidate through layers 2-5. Returns (record, layer, confidence, status, place_id, lat, lng)."""

    # Layer 2: exact match on normalized address
    hit = exact_index.get(candidate.norm)
    if hit:
        return hit, "exact", 100.0, "confirmed", None, None, None

    # Layer 3: fuzzy within same-state bucket
    best_rec, best_score, best_status = None, 0.0, "no_match"
    for rec in state_bucket:
        score, status = _fuzzy(candidate, rec)
        if status != "no_match" and score > best_score:
            best_rec, best_score, best_status = rec, score, status
            if status == "confirmed":
                break
    if best_rec:
        return best_rec, "fuzzy", best_score, best_status, None, None, None

    if not run_api:
        return None, "none", None, "unmatched", None, None, None

    # Layer 4: geocoding API (stubbed — only reaches here for TARGET_STATES)
    for rec in state_bucket:
        if _geocode_match(candidate, rec):
            return rec, "geocoding", None, "confirmed", None, None, None

    # Layer 5: places API (stubbed — only reaches here for TARGET_STATES)
    place_id, lat, lng = _places_lookup(candidate)
    if place_id:
        return None, "places", None, "confirmed", place_id, lat, lng

    return None, "none", None, "unmatched", None, None, None


def _run_matching(client: bigquery.Client) -> None:
    scrape_records = _load_source(client, "scrape")
    sarene_records = _load_source(client, "sarene")
    sf_records = _load_source(client, "salesforce")

    # Exact-match lookup dicts (layer 2)
    sarene_exact = {r.norm: r for r in sarene_records if r.norm}
    sf_exact = {r.norm: r for r in sf_records if r.norm}

    # State-level buckets for fuzzy/API layers
    sarene_by_state: dict[str, list[Record]] = {}
    for r in sarene_records:
        sarene_by_state.setdefault(r.state.upper(), []).append(r)

    sf_by_state: dict[str, list[Record]] = {}
    for r in sf_records:
        sf_by_state.setdefault(r.state.upper(), []).append(r)

    matches: list[Match] = []

    for scrape in scrape_records:
        state = scrape.state.upper()
        run_api = state in TARGET_STATES

        sarene_rec, s_layer, s_conf, s_status, s_pid, s_lat, s_lng = _find_match(
            scrape, sarene_exact, sarene_by_state.get(state, []), run_api
        )
        sf_rec, sf_layer, sf_conf, sf_status, sf_pid, sf_lat, sf_lng = _find_match(
            scrape, sf_exact, sf_by_state.get(state, []), run_api
        )

        # Pick the stronger of the two match results for record-level metadata
        if s_status == "confirmed" or (s_status == "flagged" and sf_status != "confirmed"):
            layer, conf, status = s_layer, s_conf, s_status
            place_id, lat, lng = s_pid, s_lat, s_lng
        elif sf_status in ("confirmed", "flagged"):
            layer, conf, status = sf_layer, sf_conf, sf_status
            place_id, lat, lng = sf_pid, sf_lat, sf_lng
        else:
            layer, conf, status = "none", None, "unmatched"
            place_id, lat, lng = None, None, None

        matches.append(Match(
            business_id=str(uuid.uuid4()),
            layer=layer,
            confidence=conf,
            status=status,
            scrape=scrape,
            sarene=sarene_rec,
            sf=sf_rec,
            place_id=place_id,
            lat=lat,
            lng=lng,
        ))

    _write_results(client, matches)


def _rec_fields(r: Optional[Record], prefix: str) -> dict:
    if not r:
        return {f"{prefix}_{k}": None for k in ("id", "name", "address", "city", "state", "zip")}
    return {
        f"{prefix}_id": r.source_id,
        f"{prefix}_name": r.name,
        f"{prefix}_address": r.address,
        f"{prefix}_city": r.city,
        f"{prefix}_state": r.state,
        f"{prefix}_zip": r.zip_code,
    }


def _to_row(m: Match) -> dict:
    return {
        "business_id": m.business_id,
        "match_layer": m.layer,
        "match_confidence": m.confidence,
        "match_status": m.status,
        **_rec_fields(m.scrape, "scrape"),
        **_rec_fields(m.sarene, "sarene"),
        **_rec_fields(m.sf, "sf"),
        "place_id": m.place_id,
        "lat": m.lat,
        "lng": m.lng,
        "matched_at": m.matched_at,
        "run_date": m.run_date,
    }


def _write_results(client: bigquery.Client, matches: list[Match]) -> None:
    table_ref = f"{PROJECT}.{DATASET}.{UNIFIED_TABLE}"
    client.query(f"TRUNCATE TABLE `{table_ref}`").result()
    rows = [_to_row(m) for m in matches]
    total = len(rows)
    for start in range(0, total, BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        errors = client.insert_rows_json(table_ref, batch)
        if errors:
            raise RuntimeError(f"BigQuery insert errors at row {start}: {errors}")
        print(f"{min(start + BATCH_SIZE, total)}/{total} unified records written.")
    print(f"Done — {total} businesses written to {table_ref}.")


# ---------------------------------------------------------------------------
# Entry point — HTTP trigger (called by Cloud Scheduler)
# ---------------------------------------------------------------------------

@functions_framework.http
def retail_store_fn_matching(request):
    client = bigquery.Client(project=PROJECT)
    _run_matching(client)
    return "OK", 200
