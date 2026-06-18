"""ESIOS client: fetch hourly electricity prices from Red Eléctrica de España."""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

from auto_charge.config import Config
from auto_charge.utils import get_spain_tz, logger

ESIOS_BASE_URL = "https://api.esios.ree.es"
PVPC_INDICATOR_ID = 10391  # PVPC hourly price


class ESIOSClient:
    """Client for the ESIOS API to fetch Spanish electricity prices."""

    def __init__(self, config: Config):
        self._token = config.esios_token

    def fetch_daily_prices(self, date_str: str) -> Dict[int, float]:
        """
        Fetch hourly PVPC prices for a given date.
        Returns dict: {hour (0-23, Spanish time): price_cents_per_kWh}
        """
        url = f"{ESIOS_BASE_URL}/indicators/{PVPC_INDICATOR_ID}"
        params = {
            "start_date": f"{date_str}T00:00:00",
            "end_date": f"{date_str}T23:59:59",
        }
        headers = {
            "Authorization": f'Bearer {self._token}',
            "Accept": "application/json; application/vnd.esios-api-v1+json",
        }

        logger.info(f"Fetching electricity prices for {date_str}...")
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch ESIOS prices: {e}")
            return {}

        data = resp.json()
        return self._parse_prices(data)

    def _parse_prices(self, data: dict) -> Dict[int, float]:
        """Parse ESIOS JSON response into {hour_spain: price_cents_per_kWh}."""
        prices: Dict[int, float] = {}
        indicator = data.get("indicator", {})
        values = indicator.get("values", [])

        spain_tz = get_spain_tz()

        for entry in values:
            price_eur_mwh = entry.get("value", 0)
            if price_eur_mwh is None:
                continue

            # eSIOS returns price in EUR/MWh → convert to cents/kWh
            # EUR/MWh ÷ 1000 × 100 = cents/kWh → divide by 10
            price_cents = float(price_eur_mwh) / 10.0

            # Parse datetime and convert to Spanish hour
            dt_str = entry.get("datetime", "")
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except ValueError:
                # Try parsing without timezone
                dt = datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)

            local_dt = dt.astimezone(spain_tz)
            prices[local_dt.hour] = price_cents

        logger.info(f"Got {len(prices)} hourly prices for the day.")
        return prices
