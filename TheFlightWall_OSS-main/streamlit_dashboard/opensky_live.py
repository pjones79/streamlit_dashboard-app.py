"""OpenSky Network: fetch live aircraft positions (anonymous public API)."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
ADSBDB_CALLSIGN_URL = "https://api.adsbdb.com/v0/callsign/"

# Light IATA → ICAO map for common US/international carriers (extend as needed).
IATA_TO_ICAO: dict[str, str] = {
    "UA": "UAL",
    "DL": "DAL",
    "AA": "AAL",
    "WN": "SWA",
    "AS": "ASA",
    "B6": "JBU",
    "F9": "FFT",
    "NK": "NKS",
    "HA": "HAL",
    "AC": "ACA",
    "AF": "AFR",
    "LH": "DLH",
    "BA": "BAW",
    "VS": "VIR",
    "QF": "QFA",
    "FR": "RYR",
    "U2": "EZY",
    "SK": "SAS",
    "KL": "KLM",
    "IB": "IBE",
    "LX": "SWR",
}


@dataclass
class AircraftPosition:
    icao24: str
    callsign: str
    lat: float
    lon: float
    baro_alt_m: float | None
    velocity_ms: float | None
    heading_deg: float | None
    on_ground: bool
    origin_country: str


@dataclass(frozen=True)
class FlightRoute:
    """Published route for a callsign (from ADSBDB — approximate / schedule-based)."""

    origin_iata: str
    origin_icao: str
    dest_iata: str
    dest_icao: str
    origin_lat: float
    origin_lon: float
    dest_lat: float
    dest_lon: float


_cache_states: list | None = None
_cache_at: float = 0.0
_lookup_pos_cache: dict[str, tuple[float, AircraftPosition | None]] = {}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters (WGS84 sphere)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def fetch_route_adsbdb(callsign: str) -> FlightRoute | None:
    """
    Resolve origin / destination airports + coordinates for a callsign via ADSBDB.
    """
    raw = str(callsign).strip()
    if not raw:
        return None
    safe = quote(_normalize_cs(raw), safe="")
    try:
        r = requests.get(f"{ADSBDB_CALLSIGN_URL}{safe}", timeout=15)
    except (requests.RequestException, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        payload = r.json()
    except ValueError:
        return None
    fr = (payload.get("response") or {}).get("flightroute")
    if not fr:
        return None
    o = fr.get("origin") or {}
    d = fr.get("destination") or {}
    try:
        return FlightRoute(
            origin_iata=str(o.get("iata_code") or "").strip().upper(),
            origin_icao=str(o.get("icao_code") or "").strip().upper(),
            dest_iata=str(d.get("iata_code") or "").strip().upper(),
            dest_icao=str(d.get("icao_code") or "").strip().upper(),
            origin_lat=float(o["latitude"]),
            origin_lon=float(o["longitude"]),
            dest_lat=float(d["latitude"]),
            dest_lon=float(d["longitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _normalize_cs(s: str) -> str:
    return "".join(s.split()).upper()


def callsign_candidates(flight_number: str, explicit_callsign: str | None) -> list[str]:
    """Build likely ADS-B callsign prefixes from a marketing flight number."""
    if explicit_callsign:
        c = _normalize_cs(explicit_callsign)
        return list(dict.fromkeys([c])) if c else []

    raw = str(flight_number).strip()
    compact = _normalize_cs(raw)
    out: list[str] = []
    if compact:
        out.append(compact)
    # Short IATA+flight e.g. "VS3" / "BA1" → ICAO + digits (IATA is 2 letters, flight is numeric)
    if len(compact) >= 3 and compact[:2].isalpha() and compact[2:].isdigit():
        iata, digits = compact[:2], compact[2:]
        icao = IATA_TO_ICAO.get(iata)
        if icao:
            out.append(f"{icao}{digits}")
    # ICAO-style "UAL182", "VIR3" (3-letter airline designator + digits)
    if len(compact) >= 4 and compact[:3].isalpha() and compact[3:].isdigit():
        out.append(compact)
    return list(dict.fromkeys([x for x in out if x]))


def _cs_matches(opensky_cs: str | None, candidates: list[str]) -> bool:
    if not opensky_cs or not candidates:
        return False
    n = _normalize_cs(opensky_cs)
    if not n:
        return False
    for c in candidates:
        cc = _normalize_cs(c)
        if not cc:
            continue
        if n.startswith(cc) or cc.startswith(n):
            return True
        if cc in n or n in cc:
            return True
    return False


def fetch_states(ttl_seconds: float = 25.0) -> list | None:
    """Return OpenSky state vectors (raw arrays); cached to reduce rate limit pressure."""
    global _cache_states, _cache_at
    now = time.monotonic()
    if _cache_states is not None and (now - _cache_at) < ttl_seconds:
        return _cache_states

    try:
        r = requests.get(OPENSKY_STATES_URL, timeout=35)
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError):
        return _cache_states

    states = payload.get("states") if isinstance(payload, dict) else None
    if not states:
        _cache_states = _cache_states or []
        _cache_at = now
        return _cache_states

    _cache_states = states
    _cache_at = now
    return _cache_states


def lookup_aircraft(
    flight_number: str,
    *,
    explicit_callsign: str | None = None,
    icao24: str | None = None,
    states: list | None = None,
) -> AircraftPosition | None:
    """
    Find first matching state for ICAO24 (best) or callsign candidates.
    `states` may be pre-fetched; otherwise uses cache.
    """
    data = states if states is not None else fetch_states()
    if not data:
        return None

    want_hex = (icao24 or "").strip().lower().replace("-", "")
    cands = callsign_candidates(flight_number, explicit_callsign)

    for row in data:
        if not row or len(row) < 17:
            continue
        hex_id = str(row[0] or "").strip().lower()
        cs = row[1]
        lon, lat = row[5], row[6]
        if lat is None or lon is None:
            continue
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue

        if want_hex:
            if hex_id != want_hex:
                continue
        elif not _cs_matches(cs, cands):
            continue

        baro = row[7]
        v = row[9]
        hdg = row[10]
        og = row[8]
        country = row[2] or ""
        try:
            baro_f = float(baro) if baro is not None else None
        except (TypeError, ValueError):
            baro_f = None
        try:
            v_f = float(v) if v is not None else None
        except (TypeError, ValueError):
            v_f = None
        try:
            hdg_f = float(hdg) if hdg is not None else None
        except (TypeError, ValueError):
            hdg_f = None

        return AircraftPosition(
            icao24=hex_id.upper(),
            callsign=_normalize_cs(str(cs or "")) or "—",
            lat=lat_f,
            lon=lon_f,
            baro_alt_m=baro_f,
            velocity_ms=v_f,
            heading_deg=hdg_f,
            on_ground=bool(og),
            origin_country=str(country or ""),
        )

    return None


def lookup_aircraft_cached(
    flight_number: str,
    *,
    explicit_callsign: str | None = None,
    icao24: str | None = None,
) -> AircraftPosition | None:
    """Like ``lookup_aircraft`` but reuses results for ~24s (safe for frequent Streamlit reruns)."""
    key = f"{str(flight_number).strip()}|{explicit_callsign!s}|{icao24!s}"
    now = time.monotonic()
    hit = _lookup_pos_cache.get(key)
    if hit is not None and (now - hit[0]) < 24.0:
        return hit[1]
    pos = lookup_aircraft(
        flight_number,
        explicit_callsign=explicit_callsign,
        icao24=icao24,
    )
    _lookup_pos_cache[key] = (now, pos)
    return pos
