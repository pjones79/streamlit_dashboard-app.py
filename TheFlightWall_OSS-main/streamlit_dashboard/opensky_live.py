"""OpenSky Network + ADSBDB: live ADS-B positions and lightweight route/history helpers."""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from urllib.parse import quote

OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
OPENSKY_FLIGHTS_AIRCRAFT = "https://opensky-network.org/api/flights/aircraft"
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
    time_position: int | None = None
    last_contact: int | None = None
    geo_alt_m: float | None = None
    vertical_rate_ms: float | None = None


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


_CALLSIGN_FLIGHT_CORE_RE = re.compile(r"^([A-Z]{2,3}\d+)")


def _callsign_flight_core(cs: str) -> str | None:
    """Strip schedule suffix letters so ``VS47`` matches ``VS47GH``, ``VIR47GH``, etc."""
    n = _normalize_cs(cs)
    m = _CALLSIGN_FLIGHT_CORE_RE.match(n)
    return m.group(1) if m else None


def _flight_core_variants(core: str | None) -> set[str]:
    """Both IATA and ICAO marketing cores (e.g. ``VS47`` + ``VIR47``)."""
    if not core:
        return set()
    out: set[str] = {core}
    if len(core) >= 3 and core[:2].isalpha() and core[2:].isdigit():
        iata, digits = core[:2], core[2:]
        icao = IATA_TO_ICAO.get(iata)
        if icao:
            out.add(f"{icao}{digits}")
    if len(core) >= 4 and core[:3].isalpha() and core[3:].isdigit():
        icao3, digits = core[:3], core[3:]
        for iata, icaov in IATA_TO_ICAO.items():
            if icaov == icao3:
                out.add(f"{iata}{digits}")
                break
    return out


def explorer_detail_url(icao24: str) -> str:
    """OpenSky web UI for one aircraft (hex, lowercase)."""
    h = _icao24_normalized(icao24)
    return f"https://opensky-network.org/network/explorer/detail?icao24={h}" if h else "https://opensky-network.org/network/explorer"


def _normalize_cs(s: str) -> str:
    return "".join(s.split()).upper()


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


def callsign_candidates(flight_number: str, explicit_callsign: str | None) -> list[str]:
    """Build likely ADS-B callsign prefixes from a marketing flight number plus optional roster hint."""
    raw = str(flight_number).strip()
    compact = _normalize_cs(raw)
    out: list[str] = []
    if compact:
        out.append(compact)
    if len(compact) >= 3 and compact[:2].isalpha() and compact[2:].isdigit():
        iata, digits = compact[:2], compact[2:]
        icao = IATA_TO_ICAO.get(iata)
        if icao:
            out.append(f"{icao}{digits}")
    if len(compact) >= 4 and compact[:3].isalpha() and compact[3:].isdigit():
        out.append(compact)

    ex = _normalize_cs(str(explicit_callsign or "").strip())
    if ex:
        out.insert(0, ex)

    return list(dict.fromkeys([x for x in out if x]))


def _cs_matches(opensky_cs: str | None, candidates: list[str]) -> bool:
    if not opensky_cs or not candidates:
        return False
    n = _normalize_cs(opensky_cs)
    if not n:
        return False
    n_core = _callsign_flight_core(n)
    n_vars = _flight_core_variants(n_core) if n_core else set()

    for c in candidates:
        cc = _normalize_cs(c)
        if not cc:
            continue
        if n.startswith(cc) or cc.startswith(n):
            return True
        if cc in n or n in cc:
            return True
        c_core = _callsign_flight_core(cc)
        c_vars = _flight_core_variants(c_core) if c_core else set()
        if n_vars and c_vars and (n_vars & c_vars):
            return True
    return False


def fetch_states(ttl_seconds: float = 25.0) -> list | None:
    """Return OpenSky state vectors (raw arrays); cached to reduce rate limit pressure.

    Returns ``None`` only when every fetch attempt fails and there is no prior cache
    (distinct from ``[]``, which means "feed responded but no state rows").
    """
    global _cache_states, _cache_at
    now = time.monotonic()
    if _cache_states is not None and (now - _cache_at) < ttl_seconds:
        return _cache_states

    headers = {
        "User-Agent": "TheFlightWall-OSS/1.0 (opensky live; +https://github.com/)",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }
    for attempt in range(3):
        try:
            r = requests.get(
                OPENSKY_STATES_URL,
                timeout=(12, 90),
                headers=headers,
                trust_env=False,
            )
            if r.status_code == 429:
                time.sleep(min(8.0, 1.5 * (2**attempt)))
                continue
            if r.status_code >= 500:
                time.sleep(0.4 * (attempt + 1))
                continue
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError):
            time.sleep(0.35 * (attempt + 1))
            continue

        states = payload.get("states") if isinstance(payload, dict) else None
        if not states:
            _cache_states = _cache_states or []
            _cache_at = time.monotonic()
            return _cache_states

        _cache_states = states
        _cache_at = time.monotonic()
        return _cache_states

    return _cache_states


def _icao24_normalized(h: str | None) -> str:
    hx = "".join(c for c in str(h or "").lower() if c in "0123456789abcdef")
    return hx if len(hx) == 6 else ""


def fetch_aircraft_flights(icao24: str, begin: int, end: int) -> list[dict[str, Any]]:
    """Historical flight segments for ICAO24 in [begin, end] UNIX seconds (may be anonymised/airport codes empty)."""
    h = _icao24_normalized(icao24)
    if not h or end <= begin:
        return []
    try:
        r = requests.get(
            OPENSKY_FLIGHTS_AIRCRAFT,
            params={"icao24": h, "begin": int(begin), "end": int(end)},
            timeout=(12, 60),
            headers={
                "User-Agent": "TheFlightWall-OSS/1.0 (opensky live; +https://github.com/)",
                "Accept": "application/json",
            },
            trust_env=False,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except (requests.RequestException, ValueError):
        return []
    return data if isinstance(data, list) else []


def _unix_day_bounds(d: date) -> tuple[int, int]:
    z = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return int(z.timestamp()), int((z + timedelta(days=1)).timestamp())


def pick_flight_overlap_day(flights: list[dict[str, Any]], day: date | None) -> dict[str, Any] | None:
    """Prefer a segment overlapping ``day`` UTC; else newest by ``lastSeen``."""
    if not flights:
        return None
    if day is None:
        return max(flights, key=lambda f: int(f.get("lastSeen") or f.get("firstSeen") or 0))

    lo, hi = _unix_day_bounds(day)
    best: dict[str, Any] | None = None
    best_ls = -1
    for f in flights:
        fs = int(f.get("firstSeen") or 0)
        ls = int(f.get("lastSeen") or 0)
        if fs >= hi or ls <= lo:
            continue
        if ls > best_ls:
            best_ls = ls
            best = f
    if best is not None:
        return best
    return max(flights, key=lambda f: int(f.get("lastSeen") or 0))


def _airport_route_label(iata: str, icao_c: str) -> str:
    a = str(iata or "").strip().upper()
    b = str(icao_c or "").strip().upper()
    if a and b and a != b:
        return f"{a} / {b}"
    return a or b or "—"


def _format_unix_utc(ts: int | None) -> str:
    if ts is None or ts <= 0:
        return "—"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return "—"
    h12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    return dt.strftime("%d/%m/%Y ") + f"{h12}:{dt.minute:02d}{ampm} UTC"


def alt_ft_summary(pos: AircraftPosition) -> str:
    ft_src = None
    if pos.geo_alt_m is not None:
        try:
            ft_src = float(pos.geo_alt_m) * 3.28084
        except (TypeError, ValueError):
            pass
    if ft_src is None and pos.baro_alt_m is not None:
        try:
            ft_src = float(pos.baro_alt_m) * 3.28084
        except (TypeError, ValueError):
            pass
    if ft_src is None:
        return "—"
    return f"{ft_src:,.0f} ft"


def speed_mph_summary(ms: float | None) -> str:
    if ms is None:
        return "—"
    try:
        return f"{float(ms) * 2.2369362920544:,.0f} mph"
    except (TypeError, ValueError):
        return "—"


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

    want_hex = _icao24_normalized(icao24 or "")
    cands = callsign_candidates(flight_number, explicit_callsign)

    for row in data:
        if not row or len(row) < 11:
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

        tp = row[3] if len(row) > 3 else None
        lc = row[4] if len(row) > 4 else None
        baro = row[7]
        og = row[8]
        v = row[9]
        hdg = row[10]
        vr = row[11] if len(row) > 11 else None
        geo = row[13] if len(row) > 13 else None
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
        try:
            tp_i = int(tp) if tp is not None else None
        except (TypeError, ValueError):
            tp_i = None
        try:
            lc_i = int(lc) if lc is not None else None
        except (TypeError, ValueError):
            lc_i = None
        try:
            geo_f = float(geo) if geo is not None else None
        except (TypeError, ValueError):
            geo_f = None
        try:
            vr_f = float(vr) if vr is not None else None
        except (TypeError, ValueError):
            vr_f = None

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
            time_position=tp_i,
            last_contact=lc_i,
            geo_alt_m=geo_f,
            vertical_rate_ms=vr_f,
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


def _fraction_along_od(pos: AircraftPosition, route: FlightRoute) -> float:
    hop = haversine_m(route.origin_lat, route.origin_lon, pos.lat, pos.lon)
    hpd = haversine_m(pos.lat, pos.lon, route.dest_lat, route.dest_lon)
    denom = hop + hpd
    if denom <= 1.0:
        return 0.0 if pos.on_ground else 0.5
    return min(max(hop / denom, 0.0), 1.0)


def empty_journey_metrics() -> dict[str, str]:
    """Placeholder journey cells when OpenSky has no fix."""
    return {
        "flight_time": "—",
        "elapsed": "—",
        "remaining": "—",
        "miles_flown": "—",
        "miles_to_go": "—",
        "arr_gate": "—",
    }


def journey_metrics_strings(
    pos: AircraftPosition,
    route: FlightRoute | None,
    frac_hint: float,
) -> dict[str, str]:
    """Compat keys consumed by dashboard ``_journey_block_html``."""
    elapsed = ""
    if pos.last_contact:
        elapsed = _format_unix_utc(pos.last_contact)
    elif pos.time_position:
        elapsed = _format_unix_utc(pos.time_position)

    vfpm = ""
    if pos.vertical_rate_ms is not None:
        try:
            ftmin = float(pos.vertical_rate_ms) * 196.850394
            vfpm = f"{ftmin:+,.0f} ft/min (baro/Vx)"
        except (TypeError, ValueError):
            vfpm = "—"

    miles_flown = "—"
    miles_to_go = "—"
    if route is not None:
        d_od = haversine_m(route.origin_lat, route.origin_lon, route.dest_lat, route.dest_lon) / 1609.34
        d_op = haversine_m(route.origin_lat, route.origin_lon, pos.lat, pos.lon) / 1609.34
        d_pd = haversine_m(pos.lat, pos.lon, route.dest_lat, route.dest_lon) / 1609.34
        miles_flown = f"{d_op:,.0f} mi (approx)"
        miles_to_go = f"{d_pd:,.0f} mi (approx)"
        od_line = f"Great circle segment ≈ {d_od:,.0f} mi"
    else:
        od_line = "Route unknown"

    pct = frac_hint if 0.0 <= frac_hint <= 1.0 else 0.5
    return {
        "flight_time": od_line,
        "elapsed": f"Last ADS-B: {elapsed}",
        "remaining": vfpm or "—",
        "miles_flown": miles_flown,
        "miles_to_go": miles_to_go,
        "arr_gate": f"Prog. bar ≈ {pct * 100:.0f}% (route approx.)",
    }


def summarize_board_dict(
    pos: AircraftPosition,
    route_adb: FlightRoute | None,
    flight_rec: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Flat dict for ``_render_live_flight_board`` (ident, lat/lon, altitude, speed, route text).
    """
    o_text = dest_text = "—"
    if route_adb is not None:
        o_text = (
            _airport_route_label(route_adb.origin_iata, route_adb.origin_icao)
            .replace("/", " · ")
            if route_adb
            else "—"
        )
        dest_text = _airport_route_label(route_adb.dest_iata, route_adb.dest_icao).replace("/", " · ")
        if isinstance(flight_rec, dict):
            dep_ai = str(flight_rec.get("estDepartureAirport") or "").strip().upper()
            arr_ai = str(flight_rec.get("estArrivalAirport") or "").strip().upper()
            if len(dep_ai) == 4 and dep_ai.isalpha():
                o_text = f"{dep_ai} · OpenSky est."
            elif len(dep_ai) == 3:
                o_text = f"{dep_ai} · OpenSky est."
            if len(arr_ai) == 4 and arr_ai.isalpha():
                dest_text = f"{arr_ai} · OpenSky est."
            elif len(arr_ai) == 3:
                dest_text = f"{arr_ai} · OpenSky est."

    spd = speed_mph_summary(pos.velocity_ms)
    hdg = "—"
    if pos.heading_deg is not None:
        try:
            hdg = f"{float(pos.heading_deg):,.0f}"
        except (TypeError, ValueError):
            hdg = "—"

    return {
        "data_source_tag": "OpenSky + ADSBDB",
        "ident": pos.callsign,
        "icao24": pos.icao24,
        "origin_text": o_text,
        "destination_text": dest_text,
        "position_lat": pos.lat,
        "position_lon": pos.lon,
        "registration": pos.icao24,
        "aircraft_type": f"{pos.origin_country or 'ADS-B'} · ICAO24 {pos.icao24}",
        "live_altitude": alt_ft_summary(pos),
        "filed_altitude": "Baro/geo from ADS-B",
        "groundspeed_mph": spd,
        "heading_deg": hdg,
        "gate_origin": str(flight_rec.get("estDepartureAirport") or "—") if isinstance(flight_rec, dict) else "—",
        "gate_destination": str(flight_rec.get("estArrivalAirport") or "—") if isinstance(flight_rec, dict) else "—",
    }


def build_dashboard_bundle(
    flight_number: str,
    *,
    explicit_callsign: str | None,
    icao24_hint: str | None,
    search_date: date | None,
) -> dict[str, Any]:
    """
    Full OpenSky-backed bundle for ``_bundle_for_dashboard``.

    Combines OpenSky ``states``, optional ``flights/aircraft`` history,
    ADSBDB callsign routing (approximate schedule), roster ICAO hints.
    """
    fn_in = str(flight_number).strip()
    empty = {
        "source": "opensky",
        "err": "no_flight",
        "flight": None,
        "board": None,
        "position": None,
    }
    if not fn_in:
        return empty

    states = fetch_states(24.0)
    if states is None:
        return {
            "source": "opensky",
            "err": "opensky_feed_unavailable",
            "flight": None,
            "board": None,
            "position": None,
        }

    pos = lookup_aircraft(
        fn_in,
        explicit_callsign=explicit_callsign,
        icao24=icao24_hint,
        states=states,
    )
    if pos is None:
        return {
            "source": "opensky",
            "err": "not_in_airspace",
            "flight": {"ident": fn_in.replace(" ", "")},
            "board": None,
            "position": None,
        }

    adb_cs = (
        pos.callsign
        if pos.callsign and pos.callsign != "—"
        else str(explicit_callsign or "").strip() or fn_in
    )
    route_adb = fetch_route_adsbdb(adb_cs)
    now = int(time.time())
    flights_hist = fetch_aircraft_flights(pos.icao24, now - int(86400 * 5), now)
    fl_rec = pick_flight_overlap_day(flights_hist, search_date)

    board = summarize_board_dict(pos, route_adb, fl_rec)
    frac = _fraction_along_od(pos, route_adb) if route_adb is not None else (0.0 if pos.on_ground else min(max(pos.baro_alt_m or 0, 3000) / 12000.0, 1.0) if pos.baro_alt_m else 0.35)

    dep_route = arr_route = "—"
    dep_when = arr_when = "—"
    if route_adb is not None:
        dep_route = _airport_route_label(route_adb.origin_iata, route_adb.origin_icao).replace("/", " · ")
        arr_route = _airport_route_label(route_adb.dest_iata, route_adb.dest_icao).replace("/", " · ")
    if isinstance(fl_rec, dict):
        ds = _format_unix_utc(int(fl_rec.get("firstSeen") or 0) or None)
        as_ = _format_unix_utc(int(fl_rec.get("lastSeen") or 0) or None)
        if ds != "—":
            dep_when = f"{ds} (OpenSky segment start)"
        if as_ != "—":
            arr_when = f"{as_} (OpenSky segment end)"

    ver = alt_ft_summary(pos)
    spd = speed_mph_summary(pos.velocity_ms)
    status = (
        f"Live ADS-B · {ver} · {spd}"
        + (" · on ground" if pos.on_ground else "")
        + " · Data: OpenSky + ADSBDB (no airline fees)"
    )

    pseudo_row: dict[str, Any] = {
        "ident": fn_in.replace(" ", ""),
        "ident_iata": fn_in.replace(" ", "") if fn_in.upper().startswith("VS") else "",
        "operator": "OpenSky Network (ADS-B)",
    }

    return {
        "source": "opensky",
        "err": None,
        "flight": pseudo_row,
        "board": board,
        "position": None,
        "opensky_pos": pos,
        "opensky_route_adb": route_adb,
        "opensky_fraction": frac,
        "opensky_status": status,
        "opensky_dep_route": dep_route,
        "opensky_arr_route": arr_route,
        "opensky_dep_when": dep_when,
        "opensky_arr_when": arr_when,
    }
