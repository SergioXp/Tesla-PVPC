"""Price providers for Spanish PVPC electricity prices.

Implements a failover chain:
1. ESIOS API (api.esios.ree.es) — requires token, most reliable
2. REData API (apidatos.ree.es) — public, no token required, official open data
"""

from datetime import datetime, timezone
from typing import Dict, Optional

import requests

from auto_charge.config import Config
from auto_charge.utils import get_spain_tz, logger

# =============================================================================
# ESIOS provider (requires token)
# =============================================================================

ESIOS_BASE_URL = "https://api.esios.ree.es"
PVPC_INDICATOR_ID = 10391  # PVPC hourly price


def _fetch_from_esios(date_str: str, token: str) -> Optional[Dict[int, float]]:
    """Try to fetch prices from the official ESIOS API (requires token)."""
    if not token:
        logger.info("ESIOS: no token configured, skipping.")
        return None

    url = f"{ESIOS_BASE_URL}/indicators/{PVPC_INDICATOR_ID}"
    params = {"start_date": f"{date_str}T00:00:00", "end_date": f"{date_str}T23:59:59"}
    headers = {
        "x-api-key": token,
        "Accept": "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
    }

    logger.info(f"ESIOS: fetching prices for {date_str}...")
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"ESIOS: request failed: {e}")
        return None

    data = resp.json()
    prices = _parse_esios_response(data)
    if not prices:
        logger.warning("ESIOS: returned empty price data.")
        return None
    if len(prices) < 20:
        logger.warning(f"ESIOS: only got {len(prices)} hours (expected 24).")
        return None

    logger.info(f"ESIOS: got {len(prices)} hourly prices.")
    return prices


def _parse_esios_response(data: dict) -> Dict[int, float]:
    """Parse ESIOS JSON: values in EUR/MWh → cents/kWh."""
    prices: Dict[int, float] = {}
    spain_tz = get_spain_tz()
    indicator = data.get("indicator", {})
    values = indicator.get("values", [])
    for entry in values:
        price_eur_mwh = entry.get("value")
        if price_eur_mwh is None:
            continue
        price_cents = float(price_eur_mwh) / 10.0  # EUR/MWh → cents/kWh
        dt_str = entry.get("datetime", "")
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
        prices[dt.astimezone(spain_tz).hour] = price_cents
    return prices


# =============================================================================
# REData provider (public, no token)
# =============================================================================

REDATA_BASE_URL = "https://apidatos.ree.es"


def _fetch_from_redata(date_str: str) -> Optional[Dict[int, float]]:
    """Fetch prices from the public REData API (no token needed)."""
    url = f"{REDATA_BASE_URL}/es/datos/mercados/precios-mercados-tiempo-real"
    params = {
        "start_date": f"{date_str}T00:00",
        "end_date": f"{date_str}T23:59",
        "time_trunc": "hour",
    }
    headers = {"Accept": "application/json"}

    logger.info(f"REData: fetching prices for {date_str}...")
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"REData: request failed: {e}")
        return None

    data = resp.json()
    prices = _parse_redata_response(data)
    if not prices:
        logger.warning("REData: returned empty price data.")
        return None
    if len(prices) < 20:
        logger.warning(f"REData: only got {len(prices)} hours (expected 24).")
        return None

    logger.info(f"REData: got {len(prices)} hourly prices.")
    return prices


def _parse_redata_response(data: dict) -> Dict[int, float]:
    """Parse REData JSON:API response into {hour: cents/kWh}.

    The REData API returns prices in `data.attributes.values`.
    Values are the OMIE day-ahead wholesale prices in EUR/MWh.
    These are NOT the final PVPC price (which includes tolls + charges),
    but the hourly price pattern is very similar — good enough for
    finding the cheapest charging hours.
    """
    prices: Dict[int, float] = {}
    spain_tz = get_spain_tz()

    # Primary data is in data.attributes.values
    root_data = data.get("data", {})
    attrs = root_data.get("attributes", {})
    values = attrs.get("values", [])

    if not values:
        # Fallback: check included array (older API format)
        included = data.get("included", [])
        for entry in included:
            vals = entry.get("attributes", {}).get("values", [])
            if vals:
                values = vals
                break

    for val_entry in values:
        raw_value = val_entry.get("value")
        dt_str = val_entry.get("datetime", "")
        if raw_value is None or not dt_str:
            continue

        price_cents = float(raw_value) / 10.0  # EUR/MWh → cents/kWh
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        prices[dt.astimezone(spain_tz).hour] = price_cents

    return prices


# =============================================================================
# Combined provider with failover
# =============================================================================


class PriceProvider:
    """Fetches PVPC prices with automatic failover.

    Tries providers in order:
    1. ESIOS API (requires token configured in config)
    2. REData API (public, no token required)
    Falls back silently if the primary provider fails.
    """

    def __init__(self, config: Config):
        self._token = config.esios_token
        self._last_source: str = "none"

    def fetch_daily_prices(self, date_str: str) -> Dict[int, float]:
        """
        Fetch hourly PVPC prices for a given date.
        Returns dict: {hour (0-23, Spanish time): price_cents_per_kWh}
        """
        prices: Optional[Dict[int, float]] = None
        last_error = ""

        # 1. Try ESIOS first (if token available)
        if self._token:
            prices = _fetch_from_esios(date_str, self._token)
            if prices:
                self._last_source = "esios"
                return prices
            last_error = "ESIOS failed"

        # 2. Fallback to REData (public API, no token)
        logger.info(f"Fallback: trying REData public API for {date_str}...")
        prices = _fetch_from_redata(date_str)
        if prices:
            self._last_source = "redata"
            logger.info("✅ Using REData public data (wholesale prices proxy, no token required).")
            return prices
        last_error += " | REData also failed"

        logger.error(f"All price providers failed: {last_error}")
        self._last_source = "none"
        return {}

    @property
    def last_source(self) -> str:
        """Name of the last provider that successfully returned data."""
        return self._last_source
