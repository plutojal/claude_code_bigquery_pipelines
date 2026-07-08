"""Tests for the geocoder's response parsing — no real HTTP calls."""

import pytest

from _modules import geocoder


# --- _extract_zip ---------------------------------------------------------

def test_extract_zip_from_components():
    result = {"address_components": [
        {"types": ["route"], "short_name": "Main St"},
        {"types": ["postal_code"], "short_name": "07105"},
    ]}
    assert geocoder._extract_zip(result) == "07105"


def test_extract_zip_truncates_to_five():
    result = {"address_components": [{"types": ["postal_code"], "short_name": "07105-1234"}]}
    assert geocoder._extract_zip(result) == "07105"


def test_extract_zip_missing_returns_none():
    result = {"address_components": [{"types": ["route"], "short_name": "Main St"}]}
    assert geocoder._extract_zip(result) is None


# --- _geocode_address (mocked HTTP) ---------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(geocoder.time, "sleep", lambda *_: None)


def _patch_response(monkeypatch, payload):
    monkeypatch.setattr(geocoder.requests, "get", lambda *a, **k: _FakeResp(payload))


def test_geocode_ok(monkeypatch):
    _patch_response(monkeypatch, {
        "status": "OK",
        "results": [{
            "geometry": {"location": {"lat": 40.7, "lng": -74.1}, "location_type": "ROOFTOP"},
            "formatted_address": "10 Main St, Newark, NJ 07105, USA",
            "address_components": [{"types": ["postal_code"], "short_name": "07105"}],
        }],
    })
    result, status = geocoder._geocode_address("10 Main St, Newark, NJ", "key")
    assert status == "OK"
    assert result["lat"] == 40.7
    assert result["lng"] == -74.1
    assert result["zip"] == "07105"
    assert result["location_type"] == "ROOFTOP"


def test_geocode_zero_results_is_permanent(monkeypatch):
    _patch_response(monkeypatch, {"status": "ZERO_RESULTS", "results": []})
    result, status = geocoder._geocode_address("nowhere", "key")
    assert result is None
    assert status == "ZERO_RESULTS"


def test_geocode_invalid_request_is_permanent(monkeypatch):
    _patch_response(monkeypatch, {"status": "INVALID_REQUEST"})
    result, status = geocoder._geocode_address("", "key")
    assert result is None
    assert status == "INVALID_REQUEST"


def test_geocode_request_denied_is_transient(monkeypatch):
    _patch_response(monkeypatch, {"status": "REQUEST_DENIED", "error_message": "bad key"})
    result, status = geocoder._geocode_address("10 Main St", "key")
    assert result is None
    assert status == "TRANSIENT"
