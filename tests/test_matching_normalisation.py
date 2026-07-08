"""Tests for the matching function's normalisation helpers."""

from _modules import matching


# --- _normalize_name ------------------------------------------------------

def test_ampersand_and_word_and_collapse():
    # "Wine & Spirits" and "Wine and Spirits" must normalise identically
    assert matching._normalize_name("Wine & Spirits") == matching._normalize_name("Wine and Spirits")


def test_possessive_stripped():
    # The apostrophe possessive is normalised away, so "George's" and "Georges"
    # converge (both land on "george" after plural collapse).
    assert matching._normalize_name("George's Wine") == matching._normalize_name("Georges Wine")
    assert "george" in matching._normalize_name("George's Wine").split()


def test_plural_collapse():
    assert matching._normalize_name("Liquors") == matching._normalize_name("Liquor")
    assert matching._normalize_name("Wines") == matching._normalize_name("Wine")


def test_double_s_not_stripped():
    # only a trailing single 's' collapses — "Bass" must stay "bass"
    assert matching._normalize_name("Bass") == "bass"


def test_suffix_removed():
    # company suffixes are dropped so they don't skew matching
    assert "llc" not in matching._normalize_name("Amanti Vino LLC").split()


# --- _normalize_addr ------------------------------------------------------

def test_addr_abbreviations_expand():
    assert matching._normalize_addr("123 Main St") == "123 main street"
    assert matching._normalize_addr("10 N Broad Ave") == "10 north broad avenue"


# --- _name_key ------------------------------------------------------------

def test_name_key_strips_stopwords():
    # generic store-type words are removed; nothing meaningful left → ""
    assert matching._name_key(matching._normalize_name("Wine Shop")) == ""
    # distinctive token survives
    assert matching._name_key(matching._normalize_name("Amanti Vino")) == "amanti vino"


# --- _is_cannabis_store ---------------------------------------------------

def test_cannabis_detection():
    assert matching._is_cannabis_store("Green Leaf Dispensary") is True
    assert matching._is_cannabis_store("Joe's Liquor") is False


# --- _extract_house_num ---------------------------------------------------

def test_house_num_extraction():
    assert matching._extract_house_num("123 main street") == "123"
    assert matching._extract_house_num("main street") is None
