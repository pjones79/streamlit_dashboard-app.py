"""Deep links to FlightAware live flight pages (browser — not a data API)."""
from __future__ import annotations

from urllib.parse import quote

import opensky_live

# UK site; path segment is the flight ident (often ICAO flight id, e.g. VIR3).
FLIGHTAWARE_UK_LIVE = "https://uk.flightaware.com/live/flight"


def live_flight_url(ident: str) -> str:
    clean = opensky_live._normalize_cs(ident)
    if not clean:
        return f"{FLIGHTAWARE_UK_LIVE}/"
    return f"{FLIGHTAWARE_UK_LIVE}/{quote(clean, safe='')}"


def preferred_flightaware_ident(
    flight_number: str,
    explicit_callsign: str | None,
    resolved_adsb_callsign: str | None,
) -> str:
    """
    Best ident for FlightAware URLs.
    Use airborne callsign from OpenSky when available; otherwise marketing + IATA→ICAO expansion.
    """
    if resolved_adsb_callsign:
        cs = opensky_live._normalize_cs(resolved_adsb_callsign)
        if cs and cs != "—":
            return cs
    cands = opensky_live.callsign_candidates(flight_number, explicit_callsign)
    if not cands:
        return opensky_live._normalize_cs(flight_number)
    for c in cands:
        if len(c) >= 4 and c[:3].isalpha() and c[3:].isdigit():
            return c
    return cands[0]
