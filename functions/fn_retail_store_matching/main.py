"""Daily matching function — enriches stores_normalized with Salesforce and distributor matches."""

import re
import string
import time
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
TARGET_STATES = {
    "CA", "TX", "FL", "NY", "IL",
    "WA", "PA", "OH", "GA", "NC",
}

FUZZY_AUTO = 85
FUZZY_FLAG = 65

ABBREV = {
    "st": "street", "ave": "avenue", "blvd": "boulevard", "dr": "drive",
    "rd": "road", "ln": "lane", "ct": "court", "pl": "place",
    "hwy": "highway", "pkwy": "parkway",
    "n": "north", "s": "south", "e": "east", "w": "west",
    "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest",
    "ste": "suite", "apt": "apartment",
}

_SUFFIX_RE = re.compile(
    r"\s*\b(llc|l\.l\.c\.|inc\.?|corp\.?|ltd\.?|co\.?|company|group|holdings|incorporated)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Source configs
# ---------------------------------------------------------------------------

SALESFORCE_CONFIG = {
    "table": f"{PROJECT}.{DATASET}.v_salesforce_clean",
    "id_col": "Id",
    "original_name_col": "original_name",
    "name_col": "clean_name",
    "address_col": "clean_address",
    "city_col": "''",
    "state_col": "BillingState",
    "zip_col": "''",
}

# Add new distributors here — no other code changes needed.
DISTRIBUTORS = [
    {
        "name": "sarene",
        "table": f"{PROJECT}.{DATASET}.v_sarene_clean",
        "id_col": "sarene_id",
        "original_name_col": "original_name",
        "name_col": "clean_name",
        "address_col": "clean_address",
        "city_col": "''",
        "state_col": "IFNULL(parsed_state, '')",
        "zip_col": "''",
    },
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Record:
    source_id: str
    original_name: str
    name: str
    address: str
    city: str
    state: str
    zip_code: str
    norm_addr: str = field(init=False)
    norm_name: str = field(init=False)

    def __post_init__(self):
        self.norm_addr = _normalize_addr(f"{self.address} {self.city} {self.state}")
        self.norm_name = _normalize_name(self.name)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_addr(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip().translate(str.maketrans("", "", string.punctuation))
    return " ".join(ABBREV.get(t, t) for t in text.split())


def _normalize_name(text: str) -> str:
    if not text:
        return ""
    text = _SUFFIX_RE.sub("", text)
    text = text.lower().strip().translate(str.maketrans("", "", string.punctuation))
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Matching layers
# ---------------------------------------------------------------------------

def _find_match(
    candidate: Record,
    addr_exact: dict[str, Record],
    name_exact: dict[str, Record],
    state_bucket: list[Record],
    run_api: bool,
) -> tuple[Optional[Record], str, Optional[float], str]:
    # Layer 1: exact normalised address
    hit = addr_exact.get(candidate.norm_addr)
    if hit:
        return hit, "exact_address", 100.0, "confirmed"

    # Layer 2: exact normalised name (state-gated)
    if candidate.norm_name:
        hit = name_exact.get(candidate.norm_name)
        if hit and (not hit.state or hit.state.upper() == candidate.state.upper()):
            return hit, "exact_name", 100.0, "confirmed"

    # Layer 3: fuzzy — best of address or name similarity
    best_rec, best_score, best_status, best_layer = None, 0.0, "no_match", "fuzzy_address"
    for rec in state_bucket:
        addr_score = fuzz.token_sort_ratio(candidate.norm_addr, rec.norm_addr)
        name_score = (
            fuzz.token_sort_ratio(candidate.norm_name, rec.norm_name)
            if candidate.norm_name and rec.norm_name
            else 0
        )
        score = max(addr_score, name_score)
        layer = "fuzzy_name" if name_score > addr_score else "fuzzy_address"
        if score >= FUZZY_AUTO:
            status = "confirmed"
        elif score >= FUZZY_FLAG:
            status = "flagged"
        else:
            continue
        if score > best_score:
            best_rec, best_score, best_status, best_layer = rec, score, status, layer
            if status == "confirmed":
                break
    if best_rec:
        return best_rec, best_layer, best_score, best_status

    if not run_api:
        return None, "none", None, "unmatched"

    # Layers 4 & 5 — API (stubbed, pending API access)
    for rec in state_bucket:
        if _geocode_match(candidate, rec):
            return rec, "geocoding", None, "confirmed"

    place_id, _, _ = _places_lookup(candidate)
    if place_id:
        return None, "places", None, "confirmed"

    return None, "none", None, "unmatched"


def _geocode_match(a: Record, b: Record) -> bool:
    # TODO: enable after Google Address Validation API access is granted.
    return False


def _places_lookup(r: Record) -> tuple[Optional[str], Optional[float], Optional[float]]:
    # TODO: enable after Google Maps Places API access is granted.
    return None, None, None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_records(client: bigquery.Client, cfg: dict) -> list[Record]:
    state_col = cfg["state_col"]
    original_name_col = cfg.get("original_name_col", cfg["name_col"])
    # Only add WHERE clause for plain column names, not literals ('') or expressions (IFNULL(...))
    is_plain_col = "'" not in state_col and "(" not in state_col
    where = f"WHERE {state_col} IS NOT NULL" if is_plain_col else ""
    query = f"""
        SELECT
            CAST({cfg['id_col']} AS STRING) AS source_id,
            {original_name_col}             AS original_name,
            {cfg['name_col']}               AS name,
            {cfg['address_col']}            AS address,
            {cfg['city_col']}               AS city,
            {state_col}                     AS state,
            {cfg['zip_col']}                AS zip_code
        FROM `{cfg['table']}`
        {where}
    """
    records = []
    for row in client.query(query).result():
        records.append(Record(
            source_id=row.source_id or "",
            original_name=row.original_name or "",
            name=row.name or "",
            address=row.address or "",
            city=row.city or "",
            state=row.state or "",
            zip_code=row.zip_code or "",
        ))
    return records


def _load_stores(client: bigquery.Client) -> list[dict]:
    query = f"""
        SELECT
            store_id, brand, store_name, address, phone, email,
            parsed_country, parsed_city, house_number, road,
            zip, state, is_chain, chain_name
        FROM `{PROJECT}.{DATASET}.stores_normalized`
    """
    return [dict(row) for row in client.query(query).result()]


def _build_index(
    records: list[Record],
) -> tuple[dict[str, Record], dict[str, Record], dict[str, list[Record]]]:
    addr_exact = {r.norm_addr: r for r in records if r.norm_addr}
    name_exact = {r.norm_name: r for r in records if r.norm_name}
    by_state: dict[str, list[Record]] = {}
    for r in records:
        by_state.setdefault(r.state.upper(), []).append(r)
    return addr_exact, name_exact, by_state


def _store_to_candidate(store: dict) -> Record:
    return Record(
        source_id=store["store_id"],
        original_name=store.get("store_name") or "",
        name=store.get("store_name") or "",
        address=store.get("address") or "",
        city=store.get("parsed_city") or "",
        state=store.get("state") or "",
        zip_code=store.get("zip") or "",
    )


# ---------------------------------------------------------------------------
# Main matching loop
# ---------------------------------------------------------------------------

def _run_matching(client: bigquery.Client) -> None:
    t_start = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    table_ref = f"{PROJECT}.{DATASET}.{UNIFIED_TABLE}"
    staging_ref = f"{PROJECT}.{DATASET}.unified_businesses_staging_{run_id}"

    print("Loading stores_normalized...")
    stores = _load_stores(client)
    print(f"Loaded {len(stores)} stores.")

    print("Loading Salesforce (v_salesforce_clean)...")
    sf_records = _load_records(client, SALESFORCE_CONFIG)
    sf_addr_exact, sf_name_exact, sf_by_state = _build_index(sf_records)
    print(f"Loaded {len(sf_records)} Salesforce records.")

    dist_indexes = []
    for dist_cfg in DISTRIBUTORS:
        print(f"Loading distributor: {dist_cfg['name']} (v_{dist_cfg['name']}_clean)...")
        records = _load_records(client, dist_cfg)
        addr_exact, name_exact, by_state = _build_index(records)
        dist_indexes.append((dist_cfg["name"], records, addr_exact, name_exact, by_state))
        print(f"Loaded {len(records)} records from {dist_cfg['name']}.")

    print(f"All sources loaded in {time.time() - t_start:.1f}s. Matching {len(stores)} stores...")

    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()
    total = len(stores)
    sf_matched_count = 0
    all_rows: list[dict] = []

    for i, store in enumerate(stores, 1):
        candidate = _store_to_candidate(store)
        state = candidate.state.upper()
        run_api = state in TARGET_STATES

        sf_rec, sf_layer, sf_conf, sf_status = _find_match(
            candidate, sf_addr_exact, sf_name_exact, sf_by_state.get(state, []), run_api
        )
        sf_matched = sf_status in ("confirmed", "flagged")
        if sf_matched:
            sf_matched_count += 1

        dist_matches = []
        for dist_name, _, dist_addr_exact, dist_name_exact, dist_by_state in dist_indexes:
            bucket = dist_by_state.get(state, []) + dist_by_state.get("", [])
            rec, layer, conf, status = _find_match(
                candidate, dist_addr_exact, dist_name_exact, bucket, run_api
            )
            matched = status in ("confirmed", "flagged")
            dist_matches.append({
                "distributor_name": dist_name,
                "matched": matched,
                "customer_id":      rec.source_id if rec else None,
                "customer_name":    rec.original_name if rec else None,
                "match_layer":      layer if matched else None,
                "match_confidence": conf if matched else None,
                "match_status":     status if matched else None,
            })

        all_rows.append({
            "store_id":        store["store_id"],
            "brand":           store.get("brand"),
            "store_name":      store.get("store_name"),
            "address":         store.get("address"),
            "phone":           store.get("phone"),
            "email":           store.get("email"),
            "parsed_country":  store.get("parsed_country"),
            "parsed_city":     store.get("parsed_city"),
            "house_number":    store.get("house_number"),
            "road":            store.get("road"),
            "zip":             store.get("zip"),
            "state":           store.get("state"),
            "is_chain":        store.get("is_chain"),
            "chain_name":      store.get("chain_name"),
            "sf_matched":          sf_matched,
            "sf_account_id":       sf_rec.source_id if sf_rec else None,
            "sf_account_name":     sf_rec.original_name if sf_rec else None,
            "sf_match_layer":      sf_layer if sf_matched else None,
            "sf_match_confidence": sf_conf if sf_matched else None,
            "sf_match_status":     sf_status if sf_matched else None,
            "distributor_matches": dist_matches,
            "on_salesforce":       sf_matched,
            "on_any_distributor":  any(d["matched"] for d in dist_matches),
            "place_id": None,
            "lat":      None,
            "lng":      None,
            "matched_at": now,
            "run_date":   today,
        })

        if i % BATCH_SIZE == 0 or i == total:
            print(f"[{i}/{total}] matched | sf_matched={sf_matched_count} | {time.time() - t_start:.1f}s")

    print(f"Loading {total} rows to staging table {staging_ref}...")
    source_table = client.get_table(table_ref)
    job_config = bigquery.LoadJobConfig(
        schema=source_table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_json(all_rows, staging_ref, job_config=job_config)
    job.result()
    print(f"Staging load complete in {time.time() - t_start:.1f}s. Running MERGE...")

    merge_sql = f"""
        MERGE `{table_ref}` AS target
        USING `{staging_ref}` AS source
        ON target.store_id = source.store_id
        WHEN MATCHED THEN UPDATE SET
            brand = source.brand,
            store_name = source.store_name,
            address = source.address,
            phone = source.phone,
            email = source.email,
            parsed_country = source.parsed_country,
            parsed_city = source.parsed_city,
            house_number = source.house_number,
            road = source.road,
            zip = source.zip,
            state = source.state,
            is_chain = source.is_chain,
            chain_name = source.chain_name,
            sf_matched = source.sf_matched,
            sf_account_id = source.sf_account_id,
            sf_account_name = source.sf_account_name,
            sf_match_layer = source.sf_match_layer,
            sf_match_confidence = source.sf_match_confidence,
            sf_match_status = source.sf_match_status,
            distributor_matches = source.distributor_matches,
            on_salesforce = source.on_salesforce,
            on_any_distributor = source.on_any_distributor,
            place_id = source.place_id,
            lat = source.lat,
            lng = source.lng,
            matched_at = source.matched_at,
            run_date = source.run_date
        WHEN NOT MATCHED BY TARGET THEN INSERT ROW
        WHEN NOT MATCHED BY SOURCE THEN DELETE
    """
    client.query(merge_sql).result()

    client.delete_table(staging_ref)
    elapsed = time.time() - t_start
    print(f"Done — MERGE complete. {total} stores processed in {elapsed:.1f}s")
    print(f"Salesforce matched: {sf_matched_count}/{total}")
    for dist_name, dist_records, _, _, _ in dist_indexes:
        print(f"Distributor '{dist_name}': {len(dist_records)} records loaded")


# ---------------------------------------------------------------------------
# Entry point — HTTP trigger (called by Cloud Scheduler)
# ---------------------------------------------------------------------------

@functions_framework.http
def fn_retail_store_matching(request):
    client = bigquery.Client(project=PROJECT)
    _run_matching(client)
    return "OK", 200
