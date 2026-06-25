"""Daily matching function — enriches stores_normalized with distributor matches."""

import re
import string
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterator, Optional

import functions_framework
from google.cloud import bigquery
from rapidfuzz import fuzz

PROJECT = "product-analytics-389809"
DATASET = "retail_stores"
UNIFIED_TABLE = "account_universe"
CHUNK_SIZE = 10_000  # rows per processing + staging-write batch

# API layers (4 & 5) only run for these states — controls cost.
TARGET_STATES = {
    "CA", "TX", "FL", "NY", "IL",
    "WA", "PA", "OH", "GA", "NC",
}

FUZZY_AUTO = 85
FUZZY_FLAG = 75

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

# FIX 1: Expand ampersand to "and" before normalisation so "Wine & Spirits" and
# "Wine and Spirits" collapse to the same token sequence.
_AMP_RE = re.compile(r"\s*&\s*")

# FIX 2: Strip possessive apostrophes so "George's" → "georges" and "George S"
# (after punctuation strip) also → "georges" via the same path.
_POSSESSIVE_RE = re.compile(r"'s\b", re.IGNORECASE)

# Cannabis/dispensary keywords — stores matching these are never alcohol distributor customers
_CANNABIS_KEYWORDS = frozenset({"dispensary", "dispensaries", "cannabis", "marijuana"})

# Generic store-type words that inflate fuzzy name scores when shared across unrelated stores
_NAME_STOPWORDS = frozenset({
    "smoke", "shop", "shops", "wellness", "dispensary", "cannabis", "cbd", "hemp",
    "liquor", "liquors", "wine", "wines", "beer", "beers", "spirits", "beverage",
    "store", "stores", "mart", "market", "markets", "pharmacy", "drug",
    "health", "natural", "naturals", "supply", "depot", "center", "centres",
    "centre", "place", "station", "lounge", "cafe", "bar", "grill",
    "restaurant", "boutique", "gallery", "deli", "grocery", "food",
    "tobacco", "vape", "vapor", "convenience", "discount", "outlet", "express",
})

# ---------------------------------------------------------------------------
# Source config
# ---------------------------------------------------------------------------

# All distributors are sourced from this single Dataform-managed table.
# Adding a new distributor to int_distributor_account_universe automatically
# picks it up here with no code changes required.
DISTRIBUTOR_TABLE = f"{PROJECT}.{DATASET}.int_distributor_account_universe"

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
    case_volume: Optional[float] = field(default=None)
    revenue: Optional[float] = field(default=None)
    revenue_per_case: Optional[float] = field(default=None)
    norm_addr: str = field(init=False)
    norm_name: str = field(init=False)
    norm_name_key: str = field(init=False)
    norm_city: str = field(init=False)
    house_num: Optional[str] = field(init=False)

    def __post_init__(self):
        self.norm_addr = _normalize_addr(f"{self.address} {self.city} {self.state}")
        self.norm_name = _normalize_name(self.name)
        self.norm_name_key = _name_key(self.norm_name)
        self.norm_city = _normalize_city(self.city)
        self.house_num = _extract_house_num(self.norm_addr)


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
    # FIX 1: normalise & → and before stripping punctuation
    text = _AMP_RE.sub(" and ", text)
    # FIX 2: strip possessive 's so "George's" and "George S" both become "georges"
    text = _POSSESSIVE_RE.sub("s", text)
    text = text.lower().strip().translate(str.maketrans("", "", string.punctuation))
    # FIX 3: collapse plural → singular for common suffixes so "Liquors" == "Liquor",
    # "Wines" == "Wine", "Spirits" == "Spirit", "Shops" == "Shop" etc.
    # We strip a trailing 's' only from tokens longer than 3 chars that are NOT
    # already in _NAME_STOPWORDS (stopwords are matched pre-strip anyway).
    tokens = []
    for tok in text.split():
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.append(tok)
    return " ".join(tokens)


def _name_key(norm_name: str) -> str:
    """Strips generic store-type words; returns '' when nothing meaningful remains."""
    tokens = [t for t in norm_name.split() if t not in _NAME_STOPWORDS]
    return " ".join(tokens)


def _is_cannabis_store(name: str) -> bool:
    return bool(set(name.lower().split()) & _CANNABIS_KEYWORDS)


def _normalize_city(text: str) -> str:
    if not text:
        return ""
    return text.lower().strip()


def _extract_house_num(norm_addr: str) -> Optional[str]:
    """Returns the leading house number token if purely numeric, else None."""
    if not norm_addr:
        return None
    first = norm_addr.split()[0]
    return first if first.isdigit() else None


# ---------------------------------------------------------------------------
# Matching layers
# ---------------------------------------------------------------------------

def _find_match(
    candidate: Record,
    addr_exact: dict[str, Record],
    name_exact: dict[str, Record],
    state_bucket: list[Record],
    zip_bucket: list[Record],      # FIX 4: new zip-bucket parameter
    run_api: bool,
) -> tuple[Optional[Record], str, Optional[float], str]:
    # Layer 1: exact normalised address
    hit = addr_exact.get(candidate.norm_addr)
    if hit:
        return hit, "exact_address", 100.0, "confirmed"

    # Layer 2: exact normalised name (state-gated, city-gated when available)
    if candidate.norm_name:
        hit = name_exact.get(candidate.norm_name)
        if hit and (not hit.state or hit.state.upper() == candidate.state.upper()):
            city_conflict = (
                candidate.norm_city and hit.norm_city
                and fuzz.ratio(candidate.norm_city, hit.norm_city) < 80
            )
            if not city_conflict:
                # When candidate has no city, require zip agreement if both sides have one
                zip_conflict = (
                    not candidate.norm_city
                    and candidate.zip_code and hit.zip_code
                    and candidate.zip_code != hit.zip_code
                )
                if not zip_conflict:
                    return hit, "exact_name", 100.0, "confirmed"

    # FIX 4 — Layer 2b: fuzzy name match within the same zip code.
    # Zip is a much tighter geographic constraint than state, so we use a
    # lower name-score threshold (FUZZY_FLAG) to catch truncated / variant names
    # like "Ringwood Wine & Liquor" vs "Ringwood Wine & Liquors" or
    # "Stew Leonard's Paramus - Wine Store" vs "Stew Leonards Paramus - Wine Store".
    if candidate.zip_code and zip_bucket:
        best_zip_rec, best_zip_score = None, 0.0
        ck = candidate.norm_name_key
        for rec in zip_bucket:
            if not ck or not rec.norm_name_key:
                continue
            if (candidate.house_num and rec.house_num
                    and candidate.house_num != rec.house_num):
                continue
            score = fuzz.token_sort_ratio(ck, rec.norm_name_key)
            if score > best_zip_score:
                best_zip_score = score
                best_zip_rec = rec
        if best_zip_rec and best_zip_score >= FUZZY_FLAG:
            status = "confirmed" if best_zip_score >= FUZZY_AUTO else "flagged"
            return best_zip_rec, "fuzzy_name_zip", float(best_zip_score), status

    # Layer 3: fuzzy — city-gated when both sides have a city, else name+address
    best_rec, best_score, best_status, best_layer = None, 0.0, "no_match", "fuzzy_name"
    # No city and no zip → zero geographic anchor; require confirmed-level confidence for flagged
    no_geo_anchor = not candidate.norm_city and not candidate.zip_code
    # Fix: cannabis/dispensary stores are never liquor distributor customers — skip fuzzy entirely
    if not _is_cannabis_store(candidate.name):
        for rec in state_bucket:
            both_have_city = bool(candidate.norm_city and rec.norm_city)

            if both_have_city:
                # Fix: raised from 80 → 86 to block near-miss cities (Clinton ≠ Clifton)
                if fuzz.ratio(candidate.norm_city, rec.norm_city) < 86:
                    continue  # different city → different store
                ck, rk = candidate.norm_name_key, rec.norm_name_key
                # Fix: skip when name key is purely city tokens — no meaningful discriminator
                # (e.g. "Glen Rock Smoke Shop" and "Ringwood Discount Liquor" both reduce to city)
                if ck and not (set(ck.split()) - set(candidate.norm_city.split())):
                    continue
                name_score = fuzz.token_sort_ratio(ck, rk) if ck and rk else 0
                score = name_score
                layer = "fuzzy_name_city"
            else:
                # No city on one or both sides — hard-skip on house-number conflict
                if (candidate.house_num and rec.house_num
                        and candidate.house_num != rec.house_num):
                    continue
                addr_score = fuzz.token_sort_ratio(candidate.norm_addr, rec.norm_addr)
                ck, rk = candidate.norm_name_key, rec.norm_name_key
                name_score = fuzz.token_sort_ratio(ck, rk) if ck and rk else 0
                score = max(addr_score, name_score)
                layer = "fuzzy_name" if name_score >= addr_score else "fuzzy_address"
            if score >= FUZZY_AUTO:
                status = "confirmed"
            elif score >= (FUZZY_AUTO if no_geo_anchor else FUZZY_FLAG):
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

def _store_filter(mode: str) -> str:
    """Returns a WHERE clause that restricts which stores need processing."""
    if mode == "incremental":
        # Only stores not yet in unified_businesses
        return f"WHERE store_id NOT IN (SELECT store_id FROM `{PROJECT}.{DATASET}.{UNIFIED_TABLE}`)"
    return ""


def _count_stores(client: bigquery.Client, mode: str) -> int:
    sql = f"SELECT COUNT(DISTINCT store_id) AS cnt FROM `{PROJECT}.{DATASET}.int_matcher_input` {_store_filter(mode)}"
    return next(client.query(sql).result()).cnt


def _stream_stores(client: bigquery.Client, mode: str) -> Iterator[dict]:
    """Yields store dicts from the Dataform-prepared matcher input universe.

    int_matcher_input is built by Dataform from stg_stores_current (deduped
    stores_normalized, latest scrape per store_id) joined to
    stg_hemp_eligible_retailers (active Class 44 licensees), unioned with
    Class 44 licenses that didn't match any scraped store (synthetic store_ids
    prefixed 'nj-abc:'). Cannabis stores are NOT filtered here — _find_match's
    Layer 3 skip handles them, preserving the exact-match path.
    """
    query = f"""
        SELECT store_id, brand, store_name, address, phone, email,
               parsed_country, parsed_city, house_number, road,
               zip, state, is_chain, chain_name, rating, review_count,
               nj_abc_license_number
        FROM `{PROJECT}.{DATASET}.int_matcher_input`
        {_store_filter(mode)}
    """
    for row in client.query(query).result(page_size=CHUNK_SIZE):
        yield dict(row)


def _load_all_distributor_records(client: bigquery.Client) -> dict[str, list[Record]]:
    """Load every row from DISTRIBUTOR_TABLE and group by distributor_name.

    Returns a dict mapping distributor_name → list[Record].  Any distributor
    added to int_distributor_account_universe in Dataform is automatically
    included here without touching this file.
    """
    query = f"""
        SELECT
            distributor                     AS distributor_name,
            CAST(account_id AS STRING)      AS source_id,
            original_name                   AS original_name,
            clean_name                      AS name,
            clean_address                   AS address,
            IFNULL(city, '')                AS city,
            IFNULL(state, '')               AS state,
            IFNULL(zip_code, '')            AS zip_code,
            CAST(case_volume AS FLOAT64)      AS case_volume,
            CAST(revenue AS FLOAT64)         AS revenue,
            CAST(revenue_per_case AS FLOAT64) AS revenue_per_case
        FROM `{DISTRIBUTOR_TABLE}`
    """
    by_dist: dict[str, list[Record]] = {}
    for row in client.query(query).result():
        rec = Record(
            source_id=row.source_id or "",
            original_name=row.original_name or "",
            name=row.name or "",
            address=row.address or "",
            city=row.city or "",
            state=row.state or "",
            zip_code=row.zip_code or "",
            case_volume=row.case_volume,
            revenue=row.revenue,
            revenue_per_case=row.revenue_per_case,
        )
        by_dist.setdefault(row.distributor_name, []).append(rec)
    return by_dist


def _build_index(
    records: list[Record],
) -> tuple[dict[str, Record], dict[str, Record], dict[str, list[Record]], dict[str, list[Record]]]:
    # FIX 4: added zip index as fourth return value
    addr_exact = {r.norm_addr: r for r in records if r.norm_addr}
    name_exact = {r.norm_name: r for r in records if r.norm_name}
    by_state: dict[str, list[Record]] = {}
    by_zip: dict[str, list[Record]] = {}
    for r in records:
        by_state.setdefault(r.state.upper(), []).append(r)
        if r.zip_code:
            by_zip.setdefault(r.zip_code, []).append(r)
    return addr_exact, name_exact, by_state, by_zip


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
# Staging write + MERGE
# ---------------------------------------------------------------------------

def _write_chunk(
    client: bigquery.Client,
    staging_ref: str,
    rows: list[dict],
    schema: list,
    truncate: bool,
) -> None:
    disposition = (
        bigquery.WriteDisposition.WRITE_TRUNCATE
        if truncate
        else bigquery.WriteDisposition.WRITE_APPEND
    )
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition=disposition)
    client.load_table_from_json(rows, staging_ref, job_config=job_config).result()


def _run_merge(client: bigquery.Client, table_ref: str, staging_ref: str, mode: str) -> None:
    if mode == "incremental":
        # Insert new stores only — existing rows untouched
        sql = f"""
            MERGE `{table_ref}` AS target
            USING `{staging_ref}` AS source
            ON target.store_id = source.store_id
            WHEN NOT MATCHED BY TARGET THEN INSERT ROW
        """
    else:
        # Full refresh: update existing, insert new, remove deleted stores
        sql = f"""
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
                rating = source.rating,
                review_count = source.review_count,
                nj_abc_license_number = source.nj_abc_license_number,
                distributor_matches = source.distributor_matches,
                sarene_flag = source.sarene_flag,
                place_id = source.place_id,
                lat = source.lat,
                lng = source.lng,
                matched_at = source.matched_at,
                run_date = source.run_date
            WHEN NOT MATCHED BY TARGET THEN INSERT ROW
            WHEN NOT MATCHED BY SOURCE THEN DELETE
        """
    client.query(sql).result()


# ---------------------------------------------------------------------------
# Main matching loop
# ---------------------------------------------------------------------------

def _run_matching(client: bigquery.Client, mode: str) -> None:
    t_start = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    table_ref = f"{PROJECT}.{DATASET}.{UNIFIED_TABLE}"
    staging_ref = f"{PROJECT}.{DATASET}.account_universe_staging_{run_id}"

    print(f"Mode: {mode}")
    total = _count_stores(client, mode)
    if total == 0:
        print("No stores to process — account_universe is already up to date.")
        return
    print(f"{total} stores to process.")

    print(f"Loading distributors from {DISTRIBUTOR_TABLE}...")
    all_dist_records = _load_all_distributor_records(client)
    dist_indexes = []
    for dist_name, records in all_dist_records.items():
        addr_exact, name_exact, by_state, by_zip = _build_index(records)
        dist_indexes.append((dist_name, records, addr_exact, name_exact, by_state, by_zip))
        print(f"  {dist_name}: {len(records)} records loaded.")

    print(f"Indexes ready in {time.time() - t_start:.1f}s. Matching {total} stores...")

    source_schema = client.get_table(table_ref).schema
    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()

    chunk: list[dict] = []
    first_write = True
    processed = 0

    for store in _stream_stores(client, mode):
        processed += 1
        candidate = _store_to_candidate(store)
        state = candidate.state.upper()
        run_api = state in TARGET_STATES

        dist_matches = []
        for dist_name, _, dist_addr_exact, dist_name_exact, dist_by_state, dist_by_zip in dist_indexes:  # FIX 4
            bucket = dist_by_state.get(state, []) + dist_by_state.get("", [])
            zip_bucket = dist_by_zip.get(candidate.zip_code, []) if candidate.zip_code else []  # FIX 4
            rec, layer, conf, status = _find_match(
                candidate, dist_addr_exact, dist_name_exact, bucket, zip_bucket, run_api  # FIX 4
            )
            matched = status in ("confirmed", "flagged")
            dist_matches.append({
                "distributor_name":  dist_name,
                "matched":           matched,
                "customer_id":       rec.source_id if rec else None,
                "customer_name":     rec.original_name if rec else None,
                "match_layer":       layer if matched else None,
                "match_confidence":  conf if matched else None,
                "match_status":      status if matched else None,
                "case_volume":       rec.case_volume if rec else None,
                "revenue":           rec.revenue if rec else None,
                "revenue_per_case":  rec.revenue_per_case if rec else None,
            })

        chunk.append({
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
            "rating":          store.get("rating"),
            "review_count":    store.get("review_count"),
            "nj_abc_license_number": store.get("nj_abc_license_number"),
            "distributor_matches": dist_matches,
            "sarene_flag":         any(d["matched"] for d in dist_matches),
            "place_id": None,
            "lat":      None,
            "lng":      None,
            "matched_at": now,
            "run_date":   today,
        })

        if len(chunk) >= CHUNK_SIZE:
            _write_chunk(client, staging_ref, chunk, source_schema, truncate=first_write)
            first_write = False
            chunk = []
            print(f"[{processed}/{total}] written to staging | {time.time() - t_start:.1f}s")

    # Flush the final partial chunk
    if chunk:
        _write_chunk(client, staging_ref, chunk, source_schema, truncate=first_write)
        print(f"[{processed}/{total}] written to staging | {time.time() - t_start:.1f}s")

    print(f"Running MERGE ({mode})...")
    _run_merge(client, table_ref, staging_ref, mode)
    client.delete_table(staging_ref)

    elapsed = time.time() - t_start
    print(f"Done — {processed} stores processed in {elapsed:.1f}s")
    for dist_name, dist_records, _, _, _, _ in dist_indexes:  # FIX 4
        print(f"Distributor '{dist_name}': {len(dist_records)} records loaded")


# ---------------------------------------------------------------------------
# Entry point — HTTP trigger (called by Cloud Scheduler)
# ---------------------------------------------------------------------------

@functions_framework.http
def fn_retail_store_matching(request):
    mode = request.args.get("mode", "incremental")
    if mode not in ("incremental", "full"):
        return f"Invalid mode '{mode}'. Use 'incremental' or 'full'.", 400
    client = bigquery.Client(project=PROJECT)
    _run_matching(client, mode)
    return f"OK ({mode})", 200
