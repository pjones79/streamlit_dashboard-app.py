"""
FlightAware AeroAPI helpers (flights, live position).

API key resolution: optional **session** override (Streamlit user-supplied) is preferred;
otherwise ``AEROAPI_KEY`` from the environment (e.g. ``.env`` via python-dotenv).
"""
from __future__ import annotations

import os
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

import opensky_live

BASE = "https://aeroapi.flightaware.com/aeroapi"
ENV_KEY = "AEROAPI_KEY"
_DOTENV_DIR = Path(__file__).resolve().parent


def _ensure_env() -> None:
    if load_dotenv is not None:
        # Prefer .env next to this package (works even if cwd is not streamlit_dashboard)
        load_dotenv(_DOTENV_DIR / ".env", encoding="utf-8-sig")
        load_dotenv(encoding="utf-8-sig")


def get_api_key(*, session_override: str | None = None) -> str:
    """
    Return the AeroAPI key: non-empty ``session_override`` wins (trimmed, quotes stripped),
    else ``AEROAPI_KEY`` from the environment after ``load_dotenv``.
    """
    if session_override is not None:
        u = str(session_override).strip()
        if len(u) >= 2 and ((u[0] == u[-1] == '"') or (u[0] == u[-1] == "'")):
            u = u[1:-1].strip()
        if u:
            return u
    _ensure_env()
    k = os.getenv(ENV_KEY, "").strip()
    if len(k) >= 2 and ((k[0] == k[-1] == '"') or (k[0] == k[-1] == "'")):
        k = k[1:-1].strip()
    return k


def _headers(api_key: str) -> dict[str, str]:
    return {"x-apikey": api_key, "Accept": "application/json; charset=UTF-8"}


def _pick_active_or_first(flights: list[dict[str, Any]]) -> dict[str, Any] | None:
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
                "taxiing",
                "active",
                "gate",
                "boarding",
            )
        ):
            return row
    return flights[0]


def _idents_for_flight(
    flight_number: str,
    extra_first: list[str] | None = None,
) -> list[str]:
    out: list[str] = []
    if extra_first:
        for x in extra_first:
            x = str(x).strip().upper().replace(" ", "")
            if x and x not in out:
                out.append(x)
    icao_first = [
        c
        for c in opensky_live.callsign_candidates(str(flight_number).strip(), None)
        if len(c) >= 4 and c[:3].isalpha() and c[3:].isdigit()
    ]
    rest = [
        c
        for c in opensky_live.callsign_candidates(str(flight_number).strip(), None)
        if c not in icao_first
    ]
    for c in icao_first + rest:
        if c not in out:
            out.append(c)
    return out


def fetch_flights_json(
    ident: str,
    api_key: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any] | None:
    """GET /flights/{ident} with optional scheduled_out date window (ISO dates, end exclusive)."""
    url = f"{BASE}/flights/{quote(ident, safe='')}"
    params: dict[str, str] = {"ident_type": "designator"}
    if start is not None:
        params["start"] = start.isoformat()
    if end is not None:
        params["end"] = end.isoformat()
    elif start is not None:
        params["end"] = (start + timedelta(days=1)).isoformat()
    try:
        r = requests.get(url, headers=_headers(api_key), params=params, timeout=40)
    except (requests.RequestException, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        out = r.json()
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def scheduled_out_utc_date(flight: dict[str, Any]) -> date | None:
    so = flight.get("scheduled_out")
    if not so or not isinstance(so, str):
        return None
    try:
        return datetime.fromisoformat(so.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _airport_code_tokens(ap: dict[str, Any] | None) -> set[str]:
    if not ap or not isinstance(ap, dict):
        return set()
    out: set[str] = set()
    for k in ("code_iata", "code_icao", "code"):
        v = ap.get(k)
        if v is None or v == "":
            continue
        s = str(v).strip().upper()
        if s:
            out.add(s)
    return out


def _route_hint_matches_flight(flight: dict[str, Any], origin_hint: str, dest_hint: str) -> bool:
    """True when flight origin/destination codes include the hinted IATA/ICAO strings."""
    oh = str(origin_hint).strip().upper()
    dh = str(dest_hint).strip().upper()
    if not oh or not dh:
        return True
    o = flight.get("origin") if isinstance(flight.get("origin"), dict) else None
    d = flight.get("destination") if isinstance(flight.get("destination"), dict) else None
    return oh in _airport_code_tokens(o) and dh in _airport_code_tokens(d)


def pick_flight_for_date(
    flights: list[dict[str, Any]],
    target: date,
    *,
    origin_hint: str | None = None,
    dest_hint: str | None = None,
) -> dict[str, Any] | None:
    """
    Pick one row for ``target`` (scheduled_out UTC date). Optional **origin_hint** /
    **dest_hint** (e.g. roster IATA) restrict to that direction when the ident has
    both outbound and return on the same calendar day.
    """
    if not flights:
        return None
    pool: list[dict[str, Any]] = list(flights)
    oh = (origin_hint or "").strip()
    dh = (dest_hint or "").strip()
    if oh and dh:
        routed = [f for f in pool if _route_hint_matches_flight(f, oh, dh)]
        if routed:
            pool = routed
    exact = [f for f in pool if scheduled_out_utc_date(f) == target]
    if exact:
        return exact[0]
    close = [
        f
        for f in pool
        if scheduled_out_utc_date(f)
        in (target - timedelta(days=1), target + timedelta(days=1))
    ]
    if close:
        def _dist(ff: dict[str, Any]) -> int:
            d = scheduled_out_utc_date(ff)
            if d is None:
                return 9999
            return abs((d - target).days)

        return min(close, key=_dist)
    return pool[0]


def find_flight_for_lookup(
    flight_number: str,
    api_key: str,
    *,
    on_date: date | None = None,
    extra_idents_first: list[str] | None = None,
    origin_hint: str | None = None,
    dest_hint: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """
    Try idents; optionally narrow to ``on_date`` (UTC schedule date from AeroAPI).
    Returns (flight_dict, winning_ident, error).
    """
    if not api_key:
        return None, None, "missing_AEROAPI_KEY"
    start = on_date
    end = (on_date + timedelta(days=1)) if on_date else None
    last_err = None
    for ident in _idents_for_flight(flight_number, extra_first=extra_idents_first):
        payload = fetch_flights_json(ident, api_key, start=start, end=end)
        if not payload:
            last_err = "http_or_empty"
            continue
        flights = payload.get("flights")
        if not isinstance(flights, list) or not flights:
            last_err = "no_flights"
            continue
        if on_date is not None:
            row = pick_flight_for_date(
                flights,
                on_date,
                origin_hint=origin_hint,
                dest_hint=dest_hint,
            )
        else:
            row = _pick_active_or_first(flights)
        if row:
            return row, ident, None
    return None, None, last_err or "not_found"


def fetch_position(fa_flight_id: str, api_key: str) -> dict[str, Any] | None:
    url = f"{BASE}/flights/{quote(fa_flight_id, safe='')}/position"
    try:
        r = requests.get(url, headers=_headers(api_key), timeout=35)
    except (requests.RequestException, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        out = r.json()
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def format_airport(ap: dict[str, Any] | None) -> str:
    if not ap:
        return "—"
    code = (ap.get("code_iata") or ap.get("code_icao") or ap.get("code") or "").strip()
    name = (ap.get("name") or "").strip()
    city = (ap.get("city") or "").strip()
    parts = [p for p in (code, name, city) if p]
    return " · ".join(parts) if parts else "—"


def airport_display_code(ap: dict[str, Any] | None) -> str:
    if not ap:
        return ""
    return str(ap.get("code_iata") or ap.get("code_icao") or ap.get("code") or "").strip()


def airport_zoneinfo(ap: dict[str, Any] | None) -> ZoneInfo | None:
    if not ap or not isinstance(ap, dict):
        return None
    tzid = ap.get("timezone")
    if not isinstance(tzid, str) or not tzid.strip():
        return None
    try:
        return ZoneInfo(tzid.strip())
    except Exception:
        return None


def _format_iso_local_body_and_suffix(
    val: Any, airport: dict[str, Any] | None
) -> tuple[str, str] | None:
    """Return ``(date_time_body, station_suffix)`` or ``None`` when value is empty."""
    if val is None or val == "":
        return None
    s = str(val).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        stub = s[:19] if len(s) > 19 else s
        return (stub, "")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = airport_zoneinfo(airport)
    if tz is not None:
        try:
            loc = dt.astimezone(tz)
        except Exception:
            loc = dt.astimezone(timezone.utc)
            suffix = "UTC"
        else:
            suffix = airport_display_code(airport) or "UTC"
    else:
        loc = dt.astimezone(timezone.utc)
        suffix = "UTC"
    h12 = loc.hour % 12 or 12
    ampm = "am" if loc.hour < 12 else "pm"
    body = f"{loc.strftime('%d/%m/%Y')} {h12}:{loc.minute:02d}{ampm}"
    return (body, suffix)


def format_iso_at_airport(val: Any, airport: dict[str, Any] | None) -> str:
    """Format AeroAPI ISO instant in the airport's local date/time (no verbose TZ names)."""
    t = _format_iso_local_body_and_suffix(val, airport)
    if t is None:
        return "—"
    body, suffix = t
    if suffix:
        return f"{body} · {suffix}"
    return body


def format_iso_at_airport_datetime_only(val: Any, airport: dict[str, Any] | None) -> str:
    """Local date/time only (DD/MM/YYYY + 12h), without the trailing station code."""
    t = _format_iso_local_body_and_suffix(val, airport)
    if t is None:
        return "—"
    return t[0]


def airport_route_line(ap: dict[str, Any] | None) -> str:
    """One-line origin/destination label for UI (city + code when both exist)."""
    if not ap:
        return "—"
    code = airport_display_code(ap)
    city = (ap.get("city") or "").strip()
    name = (ap.get("name") or "").strip()
    if city and code:
        return f"{city} ({code})"
    if name and code:
        return f"{name} ({code})"
    return code or city or name or "—"


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def coords_from_last_position(lp: dict[str, Any] | None) -> tuple[float | None, float | None]:
    """Lat/lon from FlightAware ``last_position`` (tolerates common key spellings)."""
    if not lp:
        return None, None
    lat = _float_or_none(lp.get("latitude", lp.get("lat")))
    lon = _float_or_none(lp.get("longitude", lp.get("lon")))
    return lat, lon


_NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"


def _nominatim_reverse_json(lat: float, lon: float) -> dict[str, Any] | Literal["unmapped"] | None:
    """
    Nominatim reverse JSON, or ``"unmapped"`` when the API has no feature (typical for open ocean).

    For many mid-ocean points the response is HTTP 200 with ``{"error":"Unable to geocode"}`` —
    we treat that as unmapped water rather than a hard failure.
    """
    try:
        r = requests.get(
            _NOMINATIM_REVERSE,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "zoom": 9,
                "addressdetails": 1,
            },
            headers={
                "User-Agent": "TheFlightWall-OSS/1.0 (Streamlit flight dashboard; +https://github.com/)",
                "Accept-Language": "en",
            },
            timeout=12,
        )
    except (requests.RequestException, OSError):
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if err is not None:
        err_s = str(err).strip().lower()
        if "unable to geocode" in err_s or "no results" in err_s:
            return "unmapped"
        return None
    return data


def _nominatim_has_settlement(addr: dict[str, Any]) -> bool:
    for land_key in (
        "city",
        "town",
        "village",
        "hamlet",
        "municipality",
        "suburb",
        "neighbourhood",
        "locality",
    ):
        if addr.get(land_key):
            return True
    return False


def _nominatim_is_oceanic(data: dict[str, Any]) -> bool:
    """True when the resolved feature is open water, not a named city/airport on land."""
    addr = data.get("address")
    if not isinstance(addr, dict):
        addr = {}
    if _nominatim_has_settlement(addr):
        return False
    addresstype = (data.get("addresstype") or "").lower()
    if addresstype in ("ocean", "sea", "bay", "strait", "marine"):
        return True
    cls = (data.get("class") or "").lower()
    typ = (data.get("type") or "").lower()
    if cls == "natural" and typ in ("sea", "ocean", "bay", "strait"):
        return True
    if cls in ("waterway", "water") and typ in ("sea", "ocean", "bay", "strait"):
        return True
    if addr.get("ocean"):
        return True
    if addr.get("sea") and not _nominatim_has_settlement(addr):
        return True
    return False


def _nominatim_short_label(data: dict[str, Any]) -> str | None:
    addr = data.get("address")
    if isinstance(addr, dict):
        parts: list[str] = []
        for key in (
            "city",
            "town",
            "village",
            "hamlet",
            "municipality",
            "county",
            "state",
            "region",
        ):
            v = addr.get(key)
            if v:
                s = str(v).strip()
                if s and s not in parts:
                    parts.append(s)
        country = addr.get("country")
        if country:
            s = str(country).strip()
            if s and s not in parts:
                parts.append(s)
        if parts:
            return ", ".join(parts[:4])
    disp = data.get("display_name")
    if isinstance(disp, str) and disp.strip():
        return disp.split(",")[0].strip()[:120]
    return None


def reverse_geocode_osm(lat: float, lon: float) -> str | None:
    """
    Best-effort place name from coordinates via OpenStreetMap Nominatim.

    Use sparingly; see https://operations.osmfoundation.org/policies/nominatim/
    """
    raw = _nominatim_reverse_json(lat, lon)
    if raw is None or raw == "unmapped":
        return None
    if _nominatim_is_oceanic(raw):
        return None
    return _nominatim_short_label(raw)


def format_lat_lon_compass(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.2f}°{ns}, {abs(lon):.2f}°{ew}"


def format_lat_lon_lines(lat: float, lon: float) -> tuple[str, str]:
    """Two display lines: latitude then longitude (4 decimal places)."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return (
        f"Latitude: {abs(lat):.4f}° {ns}",
        f"Longitude: {abs(lon):.4f}° {ew}",
    )


@lru_cache(maxsize=384)
def _cached_nearest_place_subtitle(lat_r_s: str, lon_r_s: str) -> str:
    try:
        la = float(lat_r_s)
        lo = float(lon_r_s)
    except ValueError:
        return "Place lookup unavailable"
    data = _nominatim_reverse_json(la, lo)
    if data == "unmapped":
        return "Currently Oceanic"
    if data is None:
        return "Place lookup unavailable"
    if _nominatim_is_oceanic(data):
        return "Currently Oceanic"
    lbl = _nominatim_short_label(data)
    if lbl:
        return lbl
    return "Place lookup unavailable"


def live_position_lines(lat: Any, lon: Any) -> tuple[str, str, str] | None:
    """
    Return (latitude line, longitude line, nearest place / ocean subtitle) or None.

    The third line is a city/region style label from Nominatim when over land,
    **Currently Oceanic** when over open water, or a short fallback if lookup fails.
    """
    if lat is None or lon is None:
        return None
    try:
        la = float(lat)
        lo = float(lon)
    except (TypeError, ValueError):
        return None
    lat_line, lon_line = format_lat_lon_lines(la, lo)
    la_r = round(la, 2)
    lo_r = round(lo, 2)
    place = _cached_nearest_place_subtitle(f"{la_r:.2f}", f"{lo_r:.2f}")
    return (lat_line, lon_line, place)


def currently_over_label(lat: Any, lon: Any) -> str:
    """
    Single-line summary (backward compatible): place, oceanic label, or availability text.
    """
    trio = live_position_lines(lat, lon)
    if trio is None:
        return "Not available yet (no live position from FlightAware)"
    _, _, place = trio
    return place


def altitude_feet_pretty(val: Any) -> str:
    """FlightAware filed/live altitude is in hundreds of feet."""
    if val is None:
        return "—"
    try:
        x = int(val)
    except (TypeError, ValueError):
        return "—"
    ft = x * 100
    return f"{ft:,} ft"


def knots_to_mph_str(val: Any) -> str:
    if val is None:
        return "—"
    try:
        kt = float(val)
    except (TypeError, ValueError):
        return "—"
    mph = kt * 1.1507794480235424
    return f"{mph:,.0f} mph"


def _parse_iso_to_utc(val: Any) -> datetime | None:
    if val is None or val == "":
        return None
    s = str(val).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pick_departure_iso_for_display(flight: dict[str, Any], position: dict[str, Any] | None) -> Any:
    """Prefer actual gate/ runway / estimates over schedule (for passenger-facing times)."""
    for k in (
        "actual_out",
        "estimated_out",
        "actual_off",
        "estimated_off",
        "scheduled_out",
        "scheduled_off",
    ):
        if flight.get(k):
            return flight.get(k)
    if position and position.get("actual_off"):
        return position.get("actual_off")
    return None


def pick_arrival_iso_for_display(flight: dict[str, Any], position: dict[str, Any] | None) -> Any:
    for k in (
        "actual_in",
        "estimated_in",
        "actual_on",
        "estimated_on",
        "scheduled_in",
        "scheduled_on",
    ):
        if flight.get(k):
            return flight.get(k)
    if position and position.get("actual_on"):
        return position.get("actual_on")
    return None


def pick_departure_instant_utc(flight: dict[str, Any], position: dict[str, Any] | None) -> datetime | None:
    iso = pick_departure_iso_for_display(flight, position)
    return _parse_iso_to_utc(iso)


def pick_arrival_instant_utc(flight: dict[str, Any], position: dict[str, Any] | None) -> datetime | None:
    iso = pick_arrival_iso_for_display(flight, position)
    return _parse_iso_to_utc(iso)


def route_waypoint_endpoints(
    position: dict[str, Any] | None,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Approximate route ends from AeroAPI ``waypoints`` [lat, lon, lat, lon, ...]."""
    if not position:
        return None
    wp = position.get("waypoints")
    if not isinstance(wp, list) or len(wp) < 4:
        return None
    try:
        o = (float(wp[0]), float(wp[1]))
        d = (float(wp[-2]), float(wp[-1]))
        return o, d
    except (TypeError, ValueError, IndexError):
        return None


def great_circle_statute_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles."""
    m = opensky_live.haversine_m(lat1, lon1, lat2, lon2)
    return m * 0.0006213711922373338


def _format_duration_minutes(total_min: float) -> str:
    if total_min < 0 or total_min != total_min:  # nan
        return "—"
    m = int(round(total_min))
    if m < 1:
        return "0m"
    h, r = divmod(m, 60)
    if h:
        return f"{h}h {r}m"
    return f"{r}m"


def journey_metrics_strings(
    flight: dict[str, Any] | None,
    position: dict[str, Any] | None,
    board: dict[str, Any],
) -> dict[str, str]:
    """
    Live block timings and distance summaries for the dashboard card.

    Times use actual/estimated gate and runway fields from FlightAware. Distances prefer
    great-circle segments using route waypoints and live position; otherwise ``route_distance``
    x ``progress_percent`` when available.
    """
    dash = "—"
    out: dict[str, str] = {
        "flight_time": dash,
        "elapsed": dash,
        "remaining": dash,
        "miles_flown": dash,
        "miles_to_go": dash,
        "arr_gate": dash,
    }
    if not flight:
        return out
    out["arr_gate"] = str(flight.get("gate_destination") or dash).strip() or dash

    dep = pick_departure_instant_utc(flight, position)
    arr = pick_arrival_instant_utc(flight, position)
    if dep and arr:
        out["flight_time"] = _format_duration_minutes((arr - dep).total_seconds() / 60.0)

    now = datetime.now(timezone.utc)
    if dep:
        if now >= dep:
            out["elapsed"] = _format_duration_minutes((now - dep).total_seconds() / 60.0)
    if arr:
        if now < arr:
            out["remaining"] = _format_duration_minutes((arr - now).total_seconds() / 60.0)
        else:
            out["remaining"] = "0m"

    plat = board.get("position_lat")
    plon = board.get("position_lon")
    o_lat = board.get("route_o_lat")
    o_lon = board.get("route_o_lon")
    d_lat = board.get("route_d_lat")
    d_lon = board.get("route_d_lon")
    try:
        la = float(plat)  # type: ignore[arg-type]
        lo = float(plon)
        olat = float(o_lat)  # type: ignore[arg-type]
        olon = float(o_lon)
        dlat = float(d_lat)
        dlon = float(d_lon)
    except (TypeError, ValueError):
        la = lo = olat = olon = dlat = dlon = None  # type: ignore[assignment]

    if (
        la is not None
        and lo is not None
        and olat is not None
        and olon is not None
        and dlat is not None
        and dlon is not None
    ):
        flown = great_circle_statute_miles(olat, olon, la, lo)
        togo = great_circle_statute_miles(la, lo, dlat, dlon)
        out["miles_flown"] = f"{flown:,.0f} mi"
        out["miles_to_go"] = f"{togo:,.0f} mi"
    else:
        try:
            rd = int(flight.get("route_distance") or 0)
            pct_raw = flight.get("progress_percent")
            if rd > 0 and pct_raw is not None:
                p = max(0.0, min(100.0, float(pct_raw))) / 100.0
                out["miles_flown"] = f"{rd * p:,.0f} mi"
                out["miles_to_go"] = f"{rd * (1.0 - p):,.0f} mi"
        except (TypeError, ValueError):
            pass

    return out


def summarize_flight_board(
    flight: dict[str, Any],
    position: dict[str, Any] | None,
) -> dict[str, Any]:
    """Flat dict for UI."""
    origin = flight.get("origin") if isinstance(flight.get("origin"), dict) else None
    dest = flight.get("destination") if isinstance(flight.get("destination"), dict) else None
    lp = None
    if position and isinstance(position.get("last_position"), dict):
        lp = position["last_position"]
    plat, plon = coords_from_last_position(lp)
    ends = route_waypoint_endpoints(position)
    o_lat = o_lon = d_lat = d_lon = None
    if ends:
        (o_lat, o_lon), (d_lat, d_lon) = ends
    return {
        "ident": flight.get("ident"),
        "ident_icao": flight.get("ident_icao"),
        "ident_iata": flight.get("ident_iata"),
        "fa_flight_id": flight.get("fa_flight_id"),
        "status": flight.get("status"),
        "operator": flight.get("operator"),
        "registration": flight.get("registration"),
        "aircraft_type": flight.get("aircraft_type"),
        "origin_text": format_airport(origin),
        "destination_text": format_airport(dest),
        "scheduled_out": flight.get("scheduled_out"),
        "estimated_out": flight.get("estimated_out"),
        "actual_out": flight.get("actual_out"),
        "scheduled_in": flight.get("scheduled_in"),
        "estimated_in": flight.get("estimated_in"),
        "actual_in": flight.get("actual_in"),
        "gate_origin": flight.get("gate_origin"),
        "gate_destination": flight.get("gate_destination"),
        "terminal_origin": flight.get("terminal_origin"),
        "terminal_destination": flight.get("terminal_destination"),
        "filed_altitude": altitude_feet_pretty(flight.get("filed_altitude")),
        "live_altitude": altitude_feet_pretty(lp.get("altitude")) if lp else "—",
        "groundspeed_kt": lp.get("groundspeed") if lp else None,
        "groundspeed_mph": knots_to_mph_str(lp.get("groundspeed")) if lp else "—",
        "heading_deg": lp.get("heading") if lp else None,
        "position_time": lp.get("timestamp") if lp else None,
        "position_lat": plat,
        "position_lon": plon,
        "route_o_lat": o_lat,
        "route_o_lon": o_lon,
        "route_d_lat": d_lat,
        "route_d_lon": d_lon,
        "route_distance": flight.get("route_distance"),
        "progress_percent": flight.get("progress_percent"),
    }
