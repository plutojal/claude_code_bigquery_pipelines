"""Tests for the matching layers in _find_match, including regression cases
for the known false positives the guards were added to prevent.

Each test builds one or more distributor Records, indexes them, then runs a
candidate store through _find_match and asserts (layer, status).
"""

from _modules import matching


def _rec(name, address="", city="", state="NJ", zip_code="", source_id="1"):
    return matching.Record(
        source_id=source_id, original_name=name, name=name,
        address=address, city=city, state=state, zip_code=zip_code,
    )


def _match(candidate, records):
    addr_exact, name_exact, by_state, by_zip = matching._build_index(records)
    state_bucket = by_state.get(candidate.state.upper(), []) + by_state.get("", [])
    zip_bucket = by_zip.get(candidate.zip_code, []) if candidate.zip_code else []
    return matching._find_match(candidate, addr_exact, name_exact, state_bucket, zip_bucket, run_api=False)


# --- positive paths -------------------------------------------------------

def test_exact_address_match():
    dist = _rec("Foo Wines", address="10 Main St", city="Newark", state="NJ")
    cand = _rec("Totally Different", address="10 Main St", city="Newark", state="NJ")
    _, layer, conf, status = _match(cand, [dist])
    assert layer == "exact_address"
    assert status == "confirmed"
    assert conf == 100.0


def test_exact_name_match_same_city():
    dist = _rec("Amanti Vino", address="30 Church St", city="Montclair", state="NJ")
    cand = _rec("Amanti Vino", address="99 Other Rd", city="Montclair", state="NJ")
    _, layer, _, status = _match(cand, [dist])
    assert layer == "exact_name"
    assert status == "confirmed"


# --- regression: exact_name blocked on city conflict ----------------------

def test_exact_name_blocked_by_city_conflict():
    # Same name, different (conflicting) city → must NOT confirm via exact_name
    dist = _rec("Amanti Vino", address="30 Church St", city="Montclair", state="NJ")
    cand = _rec("Amanti Vino", address="60 South St", city="Morristown", state="NJ")
    _, layer, _, status = _match(cand, [dist])
    assert layer != "exact_name"
    assert status == "unmatched"


# --- regression: null-city exact_name gated by zip ------------------------
# Names reduce to an empty name_key (all stopwords) so ONLY exact_name could
# fire — isolating the zip gate. Cellar-97 / Wine-Outlet class of false positive.

def test_exact_name_blocked_when_no_city_and_zip_conflicts():
    dist = _rec("Wine Liquors", address="1 A St", city="Union", state="NJ", zip_code="07083")
    cand = _rec("Wine Liquors", address="2 B St", city="", state="NJ", zip_code="08805")
    _, layer, _, status = _match(cand, [dist])
    assert status == "unmatched"


def test_exact_name_allowed_when_no_city_and_zip_agrees():
    dist = _rec("Wine Liquors", address="1 A St", city="Union", state="NJ", zip_code="07083")
    cand = _rec("Wine Liquors", address="2 B St", city="", state="NJ", zip_code="07083")
    _, layer, _, status = _match(cand, [dist])
    assert layer == "exact_name"
    assert status == "confirmed"


# --- regression: house-number veto in fuzzy_name_zip ----------------------
# Names differ (so exact_name misses) but share a name_key and zip. Blackwood
# Wellness / Canal's class of false positive.

def test_fuzzy_name_zip_vetoed_by_house_number_conflict():
    dist = _rec("Blackwood Cellars", address="125 S Black Horse Pike", zip_code="08012", state="NJ")
    cand = _rec("Blackwood Cellar Store", address="816 N Black Horse Pike", zip_code="08012", state="NJ")
    _, layer, _, status = _match(cand, [dist])
    assert status == "unmatched"


def test_fuzzy_name_zip_matches_same_house_number():
    dist = _rec("Blackwood Cellars", address="125 S Black Horse Pike", zip_code="08012", state="NJ")
    cand = _rec("Blackwood Cellar Store", address="125 N Black Horse Pike", zip_code="08012", state="NJ")
    _, layer, _, status = _match(cand, [dist])
    assert layer == "fuzzy_name_zip"
    assert status in ("confirmed", "flagged")


# --- regression: no-geo-anchor raises the fuzzy threshold -----------------
# "Liberty" vs "Libertee" name_keys score 80. With a geographic anchor that's a
# flagged match; with NO city and NO zip the threshold rises to 85 → no match.
# Wine-Rack / Hacks-Liquor class of false positive.

def test_fuzzy_no_geo_anchor_below_threshold_no_match():
    dist = _rec("Liberty Liquors", address="Palmer Square", city="", state="NJ", zip_code="")
    cand = _rec("Libertee Liquors", address="Quaker Bridge", city="", state="NJ", zip_code="")
    _, layer, _, status = _match(cand, [dist])
    assert status == "unmatched"


def test_fuzzy_with_city_anchor_flags_same_score():
    dist = _rec("Liberty Liquors", address="Palmer Square", city="Trenton", state="NJ")
    cand = _rec("Libertee Liquors", address="Quaker Bridge", city="Trenton", state="NJ")
    _, layer, _, status = _match(cand, [dist])
    assert layer == "fuzzy_name_city"
    assert status == "flagged"


# --- cannabis stores are never distributor customers ----------------------

def test_cannabis_store_skips_fuzzy():
    dist = _rec("Green Leaf Liquors", address="Palmer Square", city="Trenton", state="NJ")
    cand = _rec("Green Leaf Dispensary", address="Quaker Bridge", city="Trenton", state="NJ")
    _, layer, _, status = _match(cand, [dist])
    assert status == "unmatched"
