"""Tests for price providers with mocked HTTP requests.

Covers:
- ESIOS API: success, failure, empty, malformed responses
- REData API: success, failure, empty responses
- Failover: ESIOS fails → REData fallback
- Timeouts, HTTP errors, connection errors
- PriceProvider class
- Edge cases: missing keys, null values, bad datetimes
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Dict, Optional
import json

import pytest
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from auto_charge.prices import (
    PriceProvider,
    _fetch_from_esios,
    _fetch_from_redata,
    _parse_esios_response,
    _parse_redata_response,
    ESIOS_BASE_URL,
    REDATA_BASE_URL,
)


# =============================================================================
# ESIOS parsing (pure logic, no HTTP)
# =============================================================================

class TestEsiosParsingAdvanced:
    """Advanced ESIOS response parsing edge cases."""

    def test_esios_mixed_hours_returns_all(self):
        """ESIOS returns all 24 hours correctly."""
        data = {
            "indicator": {
                "values": [
                    {"value": float(h * 10 + 50), "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                    for h in range(24)
                ]
            }
        }
        prices = _parse_esios_response(data)
        assert len(prices) == 24, f"Should have 24 prices, got {len(prices)}"

    def test_esios_negative_price(self):
        """Negative prices (possible in PVPC) should be kept."""
        data = {
            "indicator": {
                "values": [
                    {"value": -10.0, "datetime": "2026-06-19T04:00:00+02:00"},
                ]
            }
        }
        prices = _parse_esios_response(data)
        # -10 EUR/MWh = -1.0 cents/kWh
        assert 4 in prices, "Hour 4 should be in prices"
        assert prices[4] == -1.0, f"Hour 4 should be -1.0, got {prices.get(4)}"

    def test_esios_handles_missing_indicator_key(self):
        """Missing 'indicator' key should not crash."""
        prices = _parse_esios_response({"other": {}})
        assert prices == {}, "Should return empty dict"

    def test_esios_handles_empty_values_list(self):
        """Empty values list should return empty dict."""
        data = {"indicator": {"values": []}}
        prices = _parse_esios_response(data)
        assert prices == {}, "Should return empty dict"


# =============================================================================
# REData parsing advanced
# =============================================================================

class TestRedataParsingAdvanced:
    """Advanced REData response parsing edge cases."""

    def test_redata_mixed_hours(self):
        """REData returns all 24 hours correctly."""
        data = {
            "data": {
                "attributes": {
                    "values": [
                        {"value": float(h * 10 + 50), "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                        for h in range(24)
                    ]
                }
            }
        }
        prices = _parse_redata_response(data)
        assert len(prices) == 24, f"Should have 24 prices, got {len(prices)}"

    def test_redata_none_value_skipped(self):
        """REData entry with None value should be skipped."""
        data = {
            "data": {
                "attributes": {
                    "values": [
                        {"value": None, "datetime": "2026-06-19T01:00:00+02:00"},
                        {"value": 80.0, "datetime": "2026-06-19T02:00:00+02:00"},
                    ]
                }
            }
        }
        prices = _parse_redata_response(data)
        assert 1 not in prices, "Hour with None should be skipped"
        assert 2 in prices, "Hour with valid value should be included"

    def test_redata_empty_fallback(self):
        """REData fallback to included when data.attributes.values is missing."""
        data = {
            "data": {"attributes": {}},  # No 'values' key
            "included": [
                {
                    "attributes": {
                        "values": [
                            {"value": 60.0, "datetime": "2026-06-19T01:00:00+02:00"},
                        ]
                    }
                }
            ]
        }
        prices = _parse_redata_response(data)
        assert 1 in prices, "Should parse from included fallback"

    def test_redata_handles_missing_data_key(self):
        """Missing 'data' key should not crash."""
        prices = _parse_redata_response({"other": {}})
        assert prices == {}, "Should return empty dict"

    def test_redata_handles_bad_datetime(self):
        """Bad datetime string should skip entry."""
        data = {
            "data": {
                "attributes": {
                    "values": [
                        {"value": 50.0, "datetime": "not-a-date"},
                        {"value": 60.0, "datetime": "2026-06-19T02:00:00+02:00"},
                    ]
                }
            }
        }
        prices = _parse_redata_response(data)
        # Should skip first entry, parse second
        assert 2 in prices, "Should parse the valid entry"


# =============================================================================
# _fetch_from_esios with mocked HTTP
# =============================================================================

class TestFetchFromEsios:
    """Test ESIOS HTTP fetching with mocked requests."""

    VALID_ESIOS_RESPONSE = {
        "indicator": {
            "values": [
                {"value": float(h * 10 + 50), "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                for h in range(24)
            ]
        }
    }

    def test_esios_success(self):
        """ESIOS fetch returns prices successfully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.VALID_ESIOS_RESPONSE

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_esios("2026-06-19", "test_token")

        assert prices is not None, "Should return prices"
        assert len(prices) == 24, f"Should have 24 prices, got {len(prices)}"

    def test_esios_no_token_returns_none(self):
        """No ESIOS token → returns None immediately."""
        prices = _fetch_from_esios("2026-06-19", "")
        assert prices is None, "Should return None when no token"

    def test_esios_http_error(self):
        """ESIOS HTTP error → returns None."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_esios("2026-06-19", "test_token")

        assert prices is None, "Should return None on HTTP error"

    def test_esios_connection_error(self):
        """ESIOS connection error → returns None."""
        with patch("auto_charge.prices.requests.get", side_effect=requests.ConnectionError("No route to host")):
            prices = _fetch_from_esios("2026-06-19", "test_token")

        assert prices is None, "Should return None on connection error"

    def test_esios_timeout(self):
        """ESIOS timeout → returns None."""
        with patch("auto_charge.prices.requests.get", side_effect=requests.Timeout("Timed out")):
            prices = _fetch_from_esios("2026-06-19", "test_token")

        assert prices is None, "Should return None on timeout"

    def test_esios_request_exception(self):
        """ESIOS general RequestException → returns None."""
        with patch("auto_charge.prices.requests.get", side_effect=requests.RequestException("SSL error")):
            prices = _fetch_from_esios("2026-06-19", "test_token")

        assert prices is None, "Should return None on RequestException"

    def test_esios_empty_response(self):
        """ESIOS returns empty data → returns None."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"indicator": {"values": []}}

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_esios("2026-06-19", "test_token")

        assert prices is None, "Empty response should return None"

    def test_esios_few_hours(self):
        """ESIOS returns < 20 hours → returns None (data incomplete)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "indicator": {
                "values": [
                    {"value": 50.0, "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                    for h in range(5)  # Only 5 hours
                ]
            }
        }

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_esios("2026-06-19", "test_token")

        assert prices is None, "Less than 20 hours should return None"

    def test_esios_passes_correct_params(self):
        """ESIOS receives correct URL, params and headers."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.VALID_ESIOS_RESPONSE

        with patch("auto_charge.prices.requests.get", return_value=mock_resp) as mock_get:
            _fetch_from_esios("2026-06-19", "the_secret_token")

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert "params" in call_kwargs
        assert call_kwargs["params"]["start_date"] == "2026-06-19T00:00:00"
        assert call_kwargs["params"]["end_date"] == "2026-06-19T23:59:59"
        assert call_kwargs["headers"]["x-api-key"] == "the_secret_token"
        assert call_kwargs["timeout"] == 15


# =============================================================================
# _fetch_from_redata with mocked HTTP
# =============================================================================

class TestFetchFromRedata:
    """Test REData HTTP fetching with mocked requests."""

    VALID_REDATA_RESPONSE = {
        "data": {
            "attributes": {
                "values": [
                    {"value": float(h * 10 + 50), "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                    for h in range(24)
                ]
            }
        }
    }

    def test_redata_success(self):
        """REData fetch returns prices successfully."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.VALID_REDATA_RESPONSE

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_redata("2026-06-19")

        assert prices is not None, "Should return prices"
        assert len(prices) == 24, f"Should have 24 prices, got {len(prices)}"

    def test_redata_http_error(self):
        """REData HTTP error → returns None."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_redata("2026-06-19")

        assert prices is None, "Should return None on HTTP error"

    def test_redata_connection_error(self):
        """REData connection error → returns None."""
        with patch("auto_charge.prices.requests.get", side_effect=requests.ConnectionError("Connection refused")):
            prices = _fetch_from_redata("2026-06-19")

        assert prices is None, "Should return None on connection error"

    def test_redata_timeout(self):
        """REData timeout → returns None."""
        with patch("auto_charge.prices.requests.get", side_effect=requests.Timeout("Timed out")):
            prices = _fetch_from_redata("2026-06-19")

        assert prices is None, "Should return None on timeout"

    def test_redata_empty_response(self):
        """REData returns empty data → returns None."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"attributes": {"values": []}}}

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_redata("2026-06-19")

        assert prices is None, "Empty response should return None"

    def test_redata_few_hours(self):
        """REData returns < 20 hours → returns None."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "attributes": {
                    "values": [
                        {"value": 50.0, "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                        for h in range(3)
                    ]
                }
            }
        }

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = _fetch_from_redata("2026-06-19")

        assert prices is None, "Less than 20 hours should return None"

    def test_redata_passes_correct_params(self):
        """REData receives correct headers and params."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.VALID_REDATA_RESPONSE

        with patch("auto_charge.prices.requests.get", return_value=mock_resp) as mock_get:
            _fetch_from_redata("2026-06-19")

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["params"]["start_date"] == "2026-06-19T00:00"
        assert call_kwargs["params"]["time_trunc"] == "hour"
        assert call_kwargs["headers"]["Accept"] == "application/json"
        assert call_kwargs["timeout"] == 15


# =============================================================================
# PriceProvider (combined with failover)
# =============================================================================

class TestPriceProvider:
    """Test PriceProvider failover logic with mocked HTTP."""

    VALID_ESIOS = {
        "indicator": {
            "values": [
                {"value": 50.0, "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                for h in range(24)
            ]
        }
    }

    VALID_REDATA = {
        "data": {
            "attributes": {
                "values": [
                    {"value": 60.0, "datetime": f"2026-06-19T{h:02d}:00:00+02:00"}
                    for h in range(24)
                ]
            }
        }
    }

    def _make_config(self, esios_token="test_token"):
        """Create a minimal mock config for PriceProvider."""
        mock_cfg = MagicMock()
        mock_cfg.esios_token = esios_token
        return mock_cfg

    def test_provider_esios_then_redata_success(self):
        """Provider returns ESIOS prices (primary)."""
        cfg = self._make_config(esios_token="token123")
        provider = PriceProvider(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.VALID_ESIOS

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = provider.fetch_daily_prices("2026-06-19")

        assert prices is not None
        assert len(prices) == 24
        assert provider.last_source == "esios"

    def test_provider_esios_fails_redata_fallback(self):
        """ESIOS fails → falls back to REData."""
        cfg = self._make_config(esios_token="token123")
        provider = PriceProvider(cfg)

        esios_resp = MagicMock()
        esios_resp.raise_for_status.side_effect = requests.HTTPError("403")

        redata_resp = MagicMock()
        redata_resp.status_code = 200
        redata_resp.json.return_value = self.VALID_REDATA

        def side_effect(url, *args, **kwargs):
            if "esios" in url:
                return esios_resp
            return redata_resp

        with patch("auto_charge.prices.requests.get", side_effect=side_effect):
            prices = provider.fetch_daily_prices("2026-06-19")

        assert prices is not None
        assert len(prices) == 24
        assert provider.last_source == "redata"

    def test_provider_esios_fails_redata_fails(self):
        """Both providers fail → empty dict."""
        cfg = self._make_config(esios_token="token123")
        provider = PriceProvider(cfg)

        with patch("auto_charge.prices.requests.get", side_effect=requests.RequestException("All failed")):
            prices = provider.fetch_daily_prices("2026-06-19")

        assert prices == {}, "Should return empty dict when all providers fail"
        assert provider.last_source == "none"

    def test_provider_no_esios_token_redata_fallback(self):
        """No ESIOS token → skip to REData directly."""
        cfg = self._make_config(esios_token="")
        provider = PriceProvider(cfg)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.VALID_REDATA

        with patch("auto_charge.prices.requests.get", return_value=mock_resp):
            prices = provider.fetch_daily_prices("2026-06-19")

        assert prices is not None
        assert len(prices) == 24
        assert provider.last_source == "redata"

    def test_provider_no_esios_token_redata_fails(self):
        """No ESIOS token and REData fails → empty dict."""
        cfg = self._make_config(esios_token="")
        provider = PriceProvider(cfg)

        with patch("auto_charge.prices.requests.get", side_effect=requests.ConnectionError("No connection")):
            prices = provider.fetch_daily_prices("2026-06-19")

        assert prices == {}, "Should return empty dict"
        assert provider.last_source == "none"

    def test_provider_empty_esios_redata_fallback(self):
        """ESIOS returns empty → falls back to REData."""
        cfg = self._make_config(esios_token="token123")
        provider = PriceProvider(cfg)

        esios_resp = MagicMock()
        esios_resp.status_code = 200
        esios_resp.json.return_value = {"indicator": {"values": []}}

        redata_resp = MagicMock()
        redata_resp.status_code = 200
        redata_resp.json.return_value = self.VALID_REDATA

        def side_effect(url, *args, **kwargs):
            if "esios" in url:
                return esios_resp
            return redata_resp

        with patch("auto_charge.prices.requests.get", side_effect=side_effect):
            prices = provider.fetch_daily_prices("2026-06-19")

        assert prices is not None
        assert provider.last_source == "redata"
