"""
Flight data with failover: FlightAware AeroAPI (primary) → OpenSky (backup).

Loads ``AEROAPI_KEY`` from the environment; use a ``.env`` file in the working
directory together with ``python-dotenv`` (see requirements.txt).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

import opensky_live

AEROAPI_BASE = "https://aeroapi.flightaware.com/aeroapi"
ENV_AEROAPI_KEY = "AEROAPI_KEY"


def _ensure_dotenv() -> None:
    if load_dotenv is not None:
        here = Path(__file__).resolve().parent
        load_dotenv(here / ".env", encoding="utf-8-sig")
        load_dotenv(encoding="utf-8-sig")


def _aeroapi_idents_prefer_icao(flight_number: str) -> list[str]:
    """Order idents with ICAO-style designators first (recommended by AeroAPI)."""
    cands = opensky_live.callsign_candidates(str(flight_number).strip(), None)
    icao_style = [
        c
        for c in cands
        if len(c) >= 4 and c[:3].isalpha() and c[3:].isdigit()
    ]
    tail = [c for c in cands if c not in icao_style]
    return list(dict.fromkeys(icao_style + tail))


def _aeroapi_request_flights(ident: str, api_key: str) -> dict[str, Any] | None:
    from urllib.parse import quote

    path = quote(ident, safe="")
    url = f"{AEROAPI_BASE}/flights/{path}"
    headers = {
        "x-apikey": api_key,
        "Accept": "application/json; charset=UTF-8",
    }
    params = {"ident_type": "designator"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
    except (requests.RequestException, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        payload = r.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _pick_active_or_first_flight(flights: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not flights:
        return None
    for row in flights:
        st = (row.get("status") or "").lower()
        if any(
            k in st
            for k in (
                "en route",
                "enroute",
                "scheduled",
                "departed",
                "taxing",
                "taxiing",
                "active",
                "gate",
                "boarding",
            )
        ):
            return row
    return flights[0]


def _merge_aeroapi_into_data(flight: dict[str, Any], data: dict[str, Any]) -> None:
    data["estimated_out"] = flight.get("estimated_out")
    data["actual_out"] = flight.get("actual_out")
    data["status"] = flight.get("status")
    data["gate_origin"] = flight.get("gate_origin")
    data["gate_destination"] = flight.get("gate_destination")
    data["terminal_origin"] = flight.get("terminal_origin")
    data["terminal_destination"] = flight.get("terminal_destination")
    data["gate"] = {
        "origin": flight.get("gate_origin"),
        "destination": flight.get("gate_destination"),
    }
    data["terminal"] = {
        "origin": flight.get("terminal_origin"),
        "destination": flight.get("terminal_destination"),
    }
    data["ident_icao"] = flight.get("ident_icao")
    data["ident_iata"] = flight.get("ident_iata")
    data["fa_flight_id"] = flight.get("fa_flight_id")


def _opensky_failover(flight_number: str) -> dict[str, Any] | None:
    pos = opensky_live.lookup_aircraft(str(flight_number).strip())
    if pos is None:
        return None
    return {
        "latitude": pos.lat,
        "longitude": pos.lon,
        "altitude_meters": pos.baro_alt_m,
        "callsign": pos.callsign,
        "icao24": pos.icao24,
        "velocity_ms": pos.velocity_ms,
        "heading_deg": pos.heading_deg,
        "on_ground": pos.on_ground,
    }


def fetch_flight_data(
    flight_number: str,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Return a unified dict (JSON-serializable) with the best available fields.

    Primary: AeroAPI ``GET /flights/{ident}`` (``estimated_out``, ``actual_out``,
    gates, terminals, ``status``).

    Backup: if AeroAPI is unavailable, lacks an API key, or returns no flights,
    OpenSky state vectors for lat/lon/altitude via callsign matching.

    Environment: ``AEROAPI_KEY`` (optional-loaded from ``.env`` via python-dotenv).
    """
    _ensure_dotenv()
    query = str(flight_number).strip()
    key = (api_key if api_key is not None else os.getenv(ENV_AEROAPI_KEY, "")).strip()

    result: dict[str, Any] = {
        "query": query,
        "primary_source": None,
        "aeroapi": {
            "ok": False,
            "idents_tried": [],
            "error": None,
        },
        "opensky": {"ok": False, "used_as_failover": False},
        "data": {},
    }

    if not query:
        result["aeroapi"]["error"] = "empty_flight_number"
        return result

    aero_rows: dict[str, Any] | None = None
    winning_ident: str | None = None

    if not key:
        result["aeroapi"]["error"] = "missing_AEROAPI_KEY"
    else:
        for ident in _aeroapi_idents_prefer_icao(query):
            result["aeroapi"]["idents_tried"].append(ident)
            payload = _aeroapi_request_flights(ident, key)
            if not payload:
                continue
            flights = payload.get("flights")
            if not isinstance(flights, list):
                continue
            picked = _pick_active_or_first_flight(flights)
            if picked:
                aero_rows = picked
                winning_ident = ident
                break

        if key and aero_rows is None and not result["aeroapi"]["error"]:
            result["aeroapi"]["error"] = "no_flight_data_for_idents"

    if aero_rows is not None:
        result["aeroapi"]["ok"] = True
        result["aeroapi"]["ident_matched"] = winning_ident
        result["primary_source"] = "aeroapi"
        _merge_aeroapi_into_data(aero_rows, result["data"])

    need_failover = aero_rows is None
    if need_failover:
        sky = _opensky_failover(query)
        if sky:
            result["opensky"]["ok"] = True
            result["opensky"]["used_as_failover"] = True
            result["primary_source"] = "opensky"
            result["data"].update(sky)

    return result


def fetch_flight_data_json(
    flight_number: str,
    *,
    api_key: str | None = None,
    indent: int | None = 2,
) -> str:
    """Same as ``fetch_flight_data`` but JSON-encoded (for logging or HTTP)."""

    def _default(o: Any) -> str:
        return str(o)

    return json.dumps(
        fetch_flight_data(flight_number, api_key=api_key),
        indent=indent,
        default=_default,
    )


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "VS3"
    print(fetch_flight_data_json(q))
