"""Lat/lon formatting and reverse geocoding (OpenStreetMap Nominatim) for live position UI."""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

import requests

_NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"


def _nominatim_reverse_json(lat: float, lon: float) -> dict[str, Any] | Literal["unmapped"] | None:
    """
    Nominatim reverse JSON, or ``"unmapped"`` when the API has no feature (typical for open ocean).
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
