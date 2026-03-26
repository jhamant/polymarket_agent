"""
Evidence connectors for specialist research agents.

Each connector follows the same pattern as web_fetch.py: stdlib urllib only,
no third-party deps, returns a structured dict with a consistent envelope,
and has a write_*_result() wrapper that writes JSON to a path.

Shared envelope shape:
{
    "connector": str,
    "fetched_at": ISO UTC str,
    "status": "ok" | "error",
    "params": dict,
    "data": dict | None,
    "error": str | None,
}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

_NOAA_USER_AGENT = "polymarket-agent/1.0 (research-bot)"
_DEFAULT_TIMEOUT = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_envelope(connector: str, params: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "connector": connector,
        "fetched_at": _now_iso(),
        "status": "error",
        "params": params,
        "data": None,
        "error": error,
    }


def _ok_envelope(connector: str, params: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "connector": connector,
        "fetched_at": _now_iso(),
        "status": "ok",
        "params": params,
        "data": data,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Connector 1: CoinGecko crypto prices
# ---------------------------------------------------------------------------

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"


def fetch_crypto_price(
    symbol: str,
    vs_currency: str,
    date: str | None = None,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """
    Fetch crypto price data from CoinGecko.

    date: YYYY-MM-DD. When provided, fetches historical price.
    When None, fetches current price with market cap and 24h volume.
    """
    params: dict[str, Any] = {"symbol": symbol, "vs_currency": vs_currency, "date": date}

    if date is not None:
        # CoinGecko history endpoint requires DD-MM-YYYY
        parts = date.split("-")
        cg_date = f"{parts[2]}-{parts[1]}-{parts[0]}" if len(parts) == 3 else date
        url = f"{_COINGECKO_BASE}/coins/{symbol}/history?date={cg_date}&localization=false"
    else:
        qs = urlencode(
            {
                "ids": symbol,
                "vs_currencies": vs_currency,
                "include_market_cap": "true",
                "include_24hr_vol": "true",
            }
        )
        url = f"{_COINGECKO_BASE}/simple/price?{qs}"

    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as resp:
            payload = json.load(resp)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return _error_envelope("coingecko", params, str(exc))

    price = None
    market_cap = None
    volume_24h = None

    if date is not None:
        market_data = payload.get("market_data", {})
        current_price = market_data.get("current_price", {})
        price = current_price.get(vs_currency)
        mc = market_data.get("market_cap", {})
        market_cap = mc.get(vs_currency)
        vol = market_data.get("total_volume", {})
        volume_24h = vol.get(vs_currency)
    else:
        coin_data = payload.get(symbol, {})
        price = coin_data.get(vs_currency)
        market_cap = coin_data.get(f"{vs_currency}_market_cap")
        volume_24h = coin_data.get(f"{vs_currency}_24h_vol")

    data: dict[str, Any] = {
        "symbol": symbol,
        "vs_currency": vs_currency,
        "date": date,
        "price": price,
        "source": "coingecko",
    }
    if market_cap is not None:
        data["market_cap"] = market_cap
    if volume_24h is not None:
        data["volume_24h"] = volume_24h

    return _ok_envelope("coingecko", params, data)


def write_crypto_result(
    symbol: str,
    vs_currency: str,
    date: str | None,
    output_path: Path,
) -> dict[str, Any]:
    result = fetch_crypto_price(symbol, vs_currency, date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Connector 2: ESPN sports data (unofficial API)
# ---------------------------------------------------------------------------

_ESPN_BASE = "http://site.api.espn.com/apis/site/v2/sports"


def fetch_sports_data(
    sport: str,
    league: str,
    query_type: str,
    team_slug: str | None = None,
    date: str | None = None,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """
    Fetch sports data from the unofficial ESPN API.

    query_type: "scoreboard", "team_record", or "team_schedule"
    """
    params: dict[str, Any] = {
        "sport": sport,
        "league": league,
        "query_type": query_type,
        "team_slug": team_slug,
        "date": date,
    }
    base = f"{_ESPN_BASE}/{sport}/{league}"

    if query_type == "scoreboard":
        if not date:
            return _error_envelope("espn", params, "date is required for scoreboard query_type")
        url = f"{base}/scoreboard?dates={date}"
    elif query_type == "team_record":
        if not team_slug:
            return _error_envelope("espn", params, "team_slug is required for team_record query_type")
        url = f"{base}/teams/{team_slug}/record"
    elif query_type == "team_schedule":
        if not team_slug:
            return _error_envelope("espn", params, "team_slug is required for team_schedule query_type")
        url = f"{base}/teams/{team_slug}/schedule"
    else:
        return _error_envelope("espn", params, f"Unknown query_type: {query_type}")

    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as resp:
            payload = json.load(resp)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return _error_envelope("espn", params, str(exc))

    data: dict[str, Any] = dict(payload)
    data["url"] = url
    data["query_type"] = query_type

    return _ok_envelope("espn", params, data)


def write_sports_result(
    sport: str,
    league: str,
    query_type: str,
    team_slug: str | None,
    date: str | None,
    output_path: Path,
) -> dict[str, Any]:
    result = fetch_sports_data(sport, league, query_type, team_slug, date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Connector 3: FRED macroeconomic data
# ---------------------------------------------------------------------------

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def fetch_macro_series(
    series_id: str,
    observation_date: str | None = None,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """
    Fetch macroeconomic time series data from FRED.

    Reads FRED_API_KEY from environment. Returns error if not set.
    observation_date: YYYY-MM-DD. When None, fetches the most recent observation.
    """
    params: dict[str, Any] = {"series_id": series_id, "observation_date": observation_date}

    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return _error_envelope("fred", params, "FRED_API_KEY environment variable not set")

    query_params: dict[str, Any] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    if observation_date is not None:
        query_params["observation_start"] = observation_date
        query_params["observation_end"] = observation_date
    else:
        query_params["limit"] = 1
        query_params["sort_order"] = "desc"

    url = f"{_FRED_BASE}?{urlencode(query_params)}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as resp:
            payload = json.load(resp)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return _error_envelope("fred", params, str(exc))

    observations = payload.get("observations", [])
    value = None
    if observations:
        value = observations[0].get("value")

    data: dict[str, Any] = {
        "series_id": series_id,
        "observation_date": observation_date,
        "value": value,
        "source": "FRED",
    }

    return _ok_envelope("fred", params, data)


def write_macro_result(
    series_id: str,
    observation_date: str | None,
    output_path: Path,
) -> dict[str, Any]:
    result = fetch_macro_series(series_id, observation_date)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Connector 4: CourtListener legal case search
# ---------------------------------------------------------------------------

_COURTLISTENER_BASE = "https://www.courtlistener.com/api/rest/v3/"


def fetch_legal_cases(
    query: str,
    court: str | None = None,
    result_type: str = "dockets",
    max_results: int = 5,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    """
    Search for legal cases on CourtListener.

    result_type: "dockets" or "opinions"
    court: optional court filter (e.g., "scotus")
    """
    params: dict[str, Any] = {
        "query": query,
        "court": court,
        "result_type": result_type,
        "max_results": max_results,
    }

    encoded_query = quote_plus(query)
    if result_type == "dockets":
        url = f"{_COURTLISTENER_BASE}dockets/?q={encoded_query}&order_by=score+desc&format=json"
    elif result_type == "opinions":
        url = f"{_COURTLISTENER_BASE}opinions/?q={encoded_query}&order_by=score+desc&format=json"
    else:
        return _error_envelope("courtlistener", params, f"Unknown result_type: {result_type}")

    if court:
        url += f"&court={quote_plus(court)}"

    # CourtListener requires a free API token. Register at https://www.courtlistener.com/register/
    # and set COURTLISTENER_API_TOKEN in your environment.
    api_token = os.environ.get("COURTLISTENER_API_TOKEN", "")
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_token:
        headers["Authorization"] = f"Token {api_token}"

    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as resp:
            payload = json.load(resp)
    except HTTPError as exc:
        if exc.code == 401:
            return _error_envelope(
                "courtlistener",
                params,
                "CourtListener requires a free API token. "
                "Register at https://www.courtlistener.com/register/ "
                "and set COURTLISTENER_API_TOKEN in your environment.",
            )
        return _error_envelope("courtlistener", params, str(exc))
    except (URLError, TimeoutError, OSError) as exc:
        return _error_envelope("courtlistener", params, str(exc))

    count = payload.get("count", 0)
    raw_results = payload.get("results", [])[:max_results]

    results = []
    for item in raw_results:
        if result_type == "dockets":
            results.append(
                {
                    "case_name": item.get("case_name", ""),
                    "docket_number": item.get("docket_number", ""),
                    "court": item.get("court", ""),
                    "date_filed": item.get("date_filed", ""),
                    "absolute_url": item.get("absolute_url", ""),
                }
            )
        else:
            results.append(
                {
                    "case_name": item.get("case_name", ""),
                    "citation": item.get("citation", []),
                    "court": item.get("court", ""),
                    "date_filed": item.get("date_filed", ""),
                    "absolute_url": item.get("absolute_url", ""),
                }
            )

    data: dict[str, Any] = {
        "query": query,
        "court": court,
        "result_type": result_type,
        "count": count,
        "results": results,
    }

    return _ok_envelope("courtlistener", params, data)


def write_legal_result(
    query: str,
    court: str | None,
    result_type: str,
    max_results: int,
    output_path: Path,
) -> dict[str, Any]:
    result = fetch_legal_cases(query, court, result_type, max_results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Connector 5: NOAA NWS weather data
# ---------------------------------------------------------------------------

_NOAA_BASE = "https://api.weather.gov"


def _noaa_request(url: str, timeout: int) -> tuple[dict[str, Any] | None, str | None]:
    """Make a NOAA API request. Returns (payload, error_str)."""
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _NOAA_USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as resp:
            return json.load(resp), None
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return None, str(exc)


def fetch_weather_data(
    query_type: str,
    area: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    """
    Fetch weather data from NOAA NWS.

    query_type: "alerts" (requires area) or "forecast" (requires lat and lon)
    """
    params: dict[str, Any] = {
        "query_type": query_type,
        "area": area,
        "lat": lat,
        "lon": lon,
    }

    if query_type == "alerts":
        if not area:
            return _error_envelope("noaa", params, "area is required for alerts query_type")
        url = f"{_NOAA_BASE}/alerts/active?area={quote_plus(area)}"
        payload, err = _noaa_request(url, timeout)
        if err:
            return _error_envelope("noaa", params, err)

        raw_features = (payload or {}).get("features", [])
        alerts = []
        for feature in raw_features:
            props = feature.get("properties", {})
            alerts.append(
                {
                    "event": props.get("event", ""),
                    "area_desc": props.get("areaDesc", ""),
                    "effective": props.get("effective", ""),
                    "expires": props.get("expires", ""),
                    "severity": props.get("severity", ""),
                    "certainty": props.get("certainty", ""),
                    "headline": props.get("headline", ""),
                }
            )

        data: dict[str, Any] = {
            "query_type": query_type,
            "area": area,
            "alert_count": len(alerts),
            "alerts": alerts,
        }
        return _ok_envelope("noaa", params, data)

    elif query_type == "forecast":
        if lat is None or lon is None:
            return _error_envelope("noaa", params, "lat and lon are required for forecast query_type")
        points_url = f"{_NOAA_BASE}/points/{lat},{lon}"
        points_payload, err = _noaa_request(points_url, timeout)
        if err:
            return _error_envelope("noaa", params, f"points step failed: {err}")

        forecast_url = (points_payload or {}).get("properties", {}).get("forecast")
        if not forecast_url:
            return _error_envelope("noaa", params, "points step did not return a forecast URL")

        forecast_payload, err = _noaa_request(forecast_url, timeout)
        if err:
            return _error_envelope("noaa", params, f"forecast step failed: {err}")

        raw_periods = (forecast_payload or {}).get("properties", {}).get("periods", [])
        periods = []
        for period in raw_periods:
            periods.append(
                {
                    "name": period.get("name", ""),
                    "temperature": period.get("temperature"),
                    "temperatureUnit": period.get("temperatureUnit", ""),
                    "windSpeed": period.get("windSpeed", ""),
                    "shortForecast": period.get("shortForecast", ""),
                }
            )

        data = {
            "query_type": query_type,
            "lat": lat,
            "lon": lon,
            "points_url": points_url,
            "forecast_url": forecast_url,
            "periods": periods,
        }
        return _ok_envelope("noaa", params, data)

    else:
        return _error_envelope("noaa", params, f"Unknown query_type: {query_type}")


def write_weather_result(
    query_type: str,
    area: str | None,
    lat: float | None,
    lon: float | None,
    output_path: Path,
) -> dict[str, Any]:
    result = fetch_weather_data(query_type, area, lat, lon)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
