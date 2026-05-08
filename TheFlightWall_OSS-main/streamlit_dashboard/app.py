"""
Dark-mode flight dashboard: live ADS-B via OpenSky Network (+ ADSBDB route hints).
Run: streamlit run app.py
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st
import pandas as pd

try:
    from dotenv import load_dotenv

    _ENV = Path(__file__).resolve().parent / ".env"
    load_dotenv(_ENV, encoding="utf-8-sig")
    load_dotenv(encoding="utf-8-sig")
except ImportError:
    pass

import importlib

import flight_identity
import geo_places
import image_utils
import opensky_live
import roster_db

# Streamlit reruns this script but keeps already-imported modules in sys.modules; reload so
# edits to these local modules apply without a full server restart.
importlib.reload(geo_places)
importlib.reload(opensky_live)
importlib.reload(flight_identity)
importlib.reload(roster_db)

PAGE_TITLE = "Flight Dashboard"
_DASH_DIR = Path(__file__).resolve().parent
VIRGIN_787_IMAGE = _DASH_DIR / "assets" / "virgin_atlantic_787.png"

CARD_INNER_HTML = """
<div class="hud-card next-flight-card" role="region" aria-label="Next flight">
    <div class="hud-card__eyebrow">{eyebrow}</div>
    <div class="hud-card__flight-no">{flight_no}</div>
    {flight_subline}
    <div class="hud-card__meta hud-card__meta--grid">
      <div class="hud-meta-cell">
        <span class="hud-meta-label">Departure (actual / est. · local)</span>
        <span class="hud-meta-route">{dep_route}</span>
        <span class="hud-meta-value hud-meta-value--multiline">{dep_when}</span>
      </div>
      <div class="hud-meta-cell">
        <span class="hud-meta-label">Arrival (actual / est. · local)</span>
        <span class="hud-meta-route">{arr_route}</span>
        <span class="hud-meta-value hud-meta-value--multiline">{arr_when}</span>
      </div>
      <div class="hud-meta-cell hud-meta-cell--status">
        <span class="hud-meta-label">Status</span>
        <span class="hud-meta-value hud-meta-value--accent">{status_line}</span>
      </div>
    </div>
    {journey_html}
    <div class="hud-card__progress-head">
      <span>Journey progress</span>
      <span class="hud-percent">{pct:.0f}%</span>
    </div>
</div>
"""


def _global_css() -> str:
    return """
<style>
  :root {
    --va-red: #ff3358;
    --va-red-glow: #ff6b86;
    --va-magenta: #ff4d9d;
    --va-plum: #a855f7;
    --va-plum-deep: #6b2f8a;
    --va-cream: #ffffff;
    --va-soft: #f5e8f2;
    --va-muted: #e8d4e4;
  }
  html, body, [data-testid="stAppViewContainer"] {
    background: radial-gradient(ellipse 1100px 560px at 10% -8%, rgba(255, 70, 100, 0.42), transparent 55%),
                radial-gradient(ellipse 1000px 540px at 95% 5%, rgba(168, 85, 247, 0.38), transparent 52%),
                linear-gradient(168deg, #3a1840 0%, #1f0d24 40%, #14081a 100%) !important;
  }
  .block-container {
    padding-top: 1.25rem !important;
    padding-bottom: 2rem !important;
    max-width: 920px !important;
  }
  [data-testid="stHeader"] {
    background: rgba(35, 16, 42, 0.94);
    border-bottom: 1px solid rgba(255, 80, 120, 0.35);
  }
  [data-testid="stSidebar"] {
    background: linear-gradient(200deg, #4a1d52 0%, #1a0c1f 100%) !important;
    border-right: 1px solid rgba(255, 90, 130, 0.35) !important;
  }
  [data-testid="stSidebar"] h3, [data-testid="stSidebar"] .stMarkdown h3 {
    color: var(--va-cream) !important;
  }
  [data-testid="stProgress"] > div > div > div {
    background: linear-gradient(90deg, #ff3358 0%, #ff7ab0 40%, #c084fc 100%) !important;
    box-shadow: 0 0 24px rgba(255, 80, 130, 0.55);
    border-radius: 999px;
  }
  [data-testid="stProgress"] > div > div {
    border-radius: 999px;
    background: rgba(25, 10, 30, 0.65) !important;
    height: 14px !important;
  }
  /* Main-flight aircraft: drop solid black matte into the page background */
  section.main [data-testid="stImage"],
  section.main [data-testid="stImage"] > div,
  section.main [data-testid="stImage"] > div > div {
    background: transparent !important;
  }
  section.main [data-testid="stImage"] img {
    mix-blend-mode: normal;
    filter: brightness(1.06) saturate(1.08);
  }
  .hud-wrap { width: 100%; }
  .next-flight-card {
    position: relative;
    overflow: hidden;
    border-radius: 20px;
    padding: clamp(1.1rem, 4vw, 2rem) clamp(1.15rem, 4vw, 2.25rem);
    margin-bottom: 0.75rem;
    background: linear-gradient(150deg, rgba(168, 85, 247, 0.22) 0%, rgba(45, 20, 52, 0.88) 42%, rgba(18, 8, 22, 0.94) 100%);
    border: 1px solid rgba(255, 90, 140, 0.45);
    box-shadow: 0 24px 50px rgba(0, 0, 0, 0.45),
                0 0 0 1px rgba(255, 255, 255, 0.1) inset;
  }
  .next-flight-card::before {
    content: "";
    position: absolute;
    inset: -40% 55% auto -20%;
    height: 160%;
    background: radial-gradient(circle at 30% 30%, rgba(255, 70, 100, 0.2), transparent 62%);
    pointer-events: none;
  }
  .next-flight-card::after {
    content: "";
    position: absolute;
    inset: auto -10% -60% 45%;
    height: 120%;
    background: radial-gradient(circle at 70% 70%, rgba(168, 85, 247, 0.2), transparent 60%);
    pointer-events: none;
  }
  .hud-card__eyebrow {
    position: relative;
    z-index: 1;
    font-size: clamp(0.72rem, 2.8vw, 0.82rem);
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--va-muted);
    font-weight: 600;
    margin-bottom: 0.35rem;
  }
  .hud-card__flight-no {
    position: relative;
    z-index: 1;
    font-size: clamp(2.05rem, 9vw, 3.35rem);
    font-weight: 800;
    letter-spacing: -0.02em;
    line-height: 1.05;
    color: var(--va-cream);
    text-shadow: 0 6px 32px rgba(255, 80, 120, 0.35);
    margin-bottom: 0.35rem;
  }
  .hud-card__flight-sub {
    position: relative;
    z-index: 1;
    font-size: clamp(0.95rem, 3.4vw, 1.15rem);
    font-weight: 600;
    line-height: 1.35;
    color: #e8c4de;
    margin-bottom: clamp(0.75rem, 2.5vw, 1.1rem);
    max-width: 36rem;
  }
  .hud-card__meta--grid {
    position: relative;
    z-index: 1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.85rem 1.25rem;
    align-items: start;
    margin-bottom: 1.15rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid rgba(255, 200, 220, 0.22);
  }
  .hud-card__meta--grid .hud-meta-cell:not(.hud-meta-cell--status) {
    min-width: 0;
  }
  .hud-meta-cell--status {
    grid-column: 1 / -1;
    text-align: center;
    padding-top: 0.65rem;
    margin-top: 0.15rem;
    border-top: 1px solid rgba(255, 255, 255, 0.12);
  }
  .hud-meta-value--multiline {
    display: block;
    line-height: 1.45;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: clamp(0.88rem, 3.2vw, 1.12rem);
    font-variant-numeric: tabular-nums;
  }
  .hud-meta-route {
    display: block;
    font-size: clamp(0.78rem, 2.9vw, 0.95rem);
    font-weight: 600;
    letter-spacing: 0.04em;
    color: #eec8e4;
    margin-bottom: 0.3rem;
    line-height: 1.35;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .hud-meta-label {
    display: block;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #d8b8d4;
    font-weight: 600;
    margin-bottom: 0.35rem;
  }
  .hud-meta-value {
    font-size: clamp(1rem, 3.6vw, 1.25rem);
    font-weight: 600;
    color: var(--va-soft);
  }
  .hud-meta-value--accent { color: #ff8fb8; font-weight: 700; }
  .hud-card__progress-head {
    position: relative;
    z-index: 1;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 0.8rem;
    color: var(--va-muted);
    margin-bottom: 0.45rem;
    font-weight: 500;
  }
  .hud-percent {
    font-variant-numeric: tabular-nums;
    color: #ffc8dc;
    font-weight: 700;
  }
  .hud-journey {
    position: relative;
    z-index: 1;
    margin: 0 0 0.85rem 0;
    padding-top: 0.65rem;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
  }
  .hud-journey-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.55rem 0.9rem;
  }
  .hud-journey-cell { min-width: 0; }
  .hud-journey-k {
    display: block;
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #c9a8c4;
    font-weight: 600;
    margin-bottom: 0.15rem;
    line-height: 1.2;
  }
  .hud-journey-v {
    font-size: clamp(0.82rem, 2.8vw, 0.95rem);
    font-weight: 600;
    color: var(--va-soft);
    font-variant-numeric: tabular-nums;
    line-height: 1.25;
    word-break: break-word;
  }
  @media (max-width: 640px) {
    .block-container {
      padding-left: 0.85rem !important;
      padding-right: 0.85rem !important;
    }
    .hud-card__meta--grid { grid-template-columns: 1fr; }
    .hud-meta-cell--status { text-align: left; }
    .hud-journey-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  .route-line {
    font-size: clamp(0.85rem, 3vw, 1rem);
    color: var(--va-muted);
    letter-spacing: 0.04em;
    margin: 0 0 0.6rem 0;
    font-weight: 600;
    text-transform: uppercase;
  }
  .identity-summary {
    font-size: 1.05rem;
    font-weight: 600;
    line-height: 1.35;
    color: var(--va-cream);
  }
  .live-grid { color: #f0d8ec; font-size: 0.92rem; }
  .live-currently-over {
    margin: 0 0 0.7rem 0;
    font-size: 0.95rem;
    line-height: 1.4;
    color: #f0d8ec;
  }
  .live-currently-over__label {
    font-weight: 700;
    color: #ffc8dc;
    margin-right: 0.4rem;
  }
  .live-currently-over__place { font-weight: 600; }
  .live-position-block { margin: 0 0 0.85rem 0; }
  .live-position-coords {
    font-size: 0.88rem;
    font-variant-numeric: tabular-nums;
    color: #e8cce4;
    line-height: 1.55;
    margin-bottom: 0.45rem;
    font-weight: 500;
  }
  .live-position-nearest {
    margin: 0;
    font-size: 0.95rem;
    line-height: 1.4;
    color: #f0d8ec;
  }
  .live-position-nearest__label {
    font-weight: 700;
    color: #ffc8dc;
    margin-right: 0.35rem;
  }
  .live-position-nearest__value { font-weight: 600; }
</style>
"""


def _init_session_defaults() -> None:
    if "inp_flight" not in st.session_state:
        st.session_state.inp_flight = ""
    if "inp_fa_date" not in st.session_state:
        d = None
        s = st.session_state.get("inp_fa_date_str")
        if s is not None and str(s).strip():
            d = roster_db.parse_uk_date(str(s))
        st.session_state.inp_fa_date = d
    st.session_state.pop("inp_fa_date_str", None)


# Bump when dashboard behavior or defaults change so cache + session quirks reset once per user session.
_DASHBOARD_BUILD = 15


def _compact_flight(s: str) -> str:
    return "".join(str(s).split()).upper()


def _is_removed_demo_flight(s: str) -> bool:
    c = _compact_flight(s)
    return c in ("UA1823", "UAL1823")


def _fa_lookup_date() -> date:
    """Calendar day for OpenSky segment overlap (UTC day). If the sidebar date is empty, use **today**."""
    v = st.session_state.get("inp_fa_date")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.today()


def _apply_dashboard_migrations() -> None:
    """Clear stale live-data cache after upgrades; drop legacy default flight number."""
    if st.session_state.get("_dashboard_build") != _DASHBOARD_BUILD:
        _cached_opensky_bundle.clear()
        st.session_state["_dashboard_build"] = _DASHBOARD_BUILD
        st.session_state.pop("inp_dep_t", None)
        st.session_state.pop("inp_fa_date_str", None)
        st.session_state["inp_fa_date"] = None
        st.session_state.pop("_roster_fa_date_sig", None)
    if _is_removed_demo_flight(str(st.session_state.get("inp_flight", ""))):
        st.session_state.inp_flight = ""


_LIVE_CACHE_TTL = 20

_MARK_FN_RE = re.compile(r"^([A-Z]{2})(\d{1,4})([A-Z]?)$")


def _norm_flight_id_for_match(s: str) -> str:
    """Normalize marketing idents so e.g. ``VIR158``, ``VS158``, ``VS0158`` compare equal."""
    t = "".join(str(s).split()).upper().replace(" ", "")
    if not t:
        return t
    if t.startswith("VIR") and len(t) > 3 and t[3:].isdigit():
        return f"VS{int(t[3:], 10)}"
    m = _MARK_FN_RE.match(t)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if b.isdigit():
            return f"{a}{int(b, 10)}{c}"
        return f"{a}{b}{c}"
    return t


def _roster_route_hints_for_lookup(sb: str, leg: roster_db.RosterLeg | None) -> tuple[str, str]:
    """
    When the sidebar flight matches the next roster leg (or the leg has route-only placeholder),
    pass origin/destination into lookups so the correct sector is preferred when the same
    flight number appears on multiple routes.
    """
    if leg is None:
        return "", ""
    o = leg.origin.strip()
    d = leg.destination.strip()
    if not o or not d:
        return "", ""
    lfn = leg.flight_number.strip()
    if roster_db.is_route_only_flight_number(lfn):
        return o, d
    if not str(sb).strip():
        return "", ""
    if _norm_flight_id_for_match(sb) == _norm_flight_id_for_match(lfn):
        return o, d
    return "", ""


@st.cache_data(ttl=_LIVE_CACHE_TTL, show_spinner=False)
def _cached_opensky_bundle(
    flight_number: str,
    extra_idents: tuple[str, ...],
    route_o: str,
    route_d: str,
    day_iso: str,
    icao24_hint: str,
) -> dict[str, Any]:
    """Cached OpenSky / ADSBDB live bundle (no API key)."""
    fn = str(flight_number).strip()
    ic = "".join(c for c in str(icao24_hint or "").strip().lower() if c in "0123456789abcdef")
    ic6 = ic[:6] if len(ic) >= 6 else ""
    if not fn and ic6:
        fn = f"ICAO24-{ic6}"
    if not fn:
        return {
            "source": "opensky",
            "err": "no_flight",
            "flight": None,
            "board": None,
            "position": None,
        }
    try:
        d = date.fromisoformat(day_iso)
    except ValueError:
        d = None
    ex_first = str(extra_idents[0]).strip() if extra_idents else None
    return opensky_live.build_dashboard_bundle(
        fn,
        explicit_callsign=ex_first,
        icao24_hint=icao24_hint.strip() or None,
        search_date=d,
    )


def _manual_icao24_sidebar() -> str:
    """Optional 6-character hex from sidebar (e.g. opensky explorer ``407f19``)."""
    raw = str(st.session_state.get("inp_icao24", "")).strip().lower()
    hx = "".join(c for c in raw if c in "0123456789abcdef")
    return hx[:6] if len(hx) >= 6 else ""


def _bundle_for_dashboard(
    *,
    n_roster: int,
    leg: roster_db.RosterLeg | None,
) -> dict[str, Any]:
    sb = str(st.session_state.get("inp_flight", "")).strip()
    lookup_d = _fa_lookup_date().isoformat()

    def _roster_icao24_for_opensky() -> str:
        if leg is None:
            return ""
        if sb.strip() and _norm_flight_id_for_match(sb) != _norm_flight_id_for_match(leg.flight_number.strip()):
            return ""
        return str(leg.icao24 or "").strip()

    ic = _manual_icao24_sidebar() or _roster_icao24_for_opensky()

    if sb:
        ro, rd = _roster_route_hints_for_lookup(sb, leg)
        return _cached_opensky_bundle(sb, (), ro, rd, lookup_d, ic)
    if n_roster and leg:
        fa_fn, extra = roster_db.flightaware_ident_from_leg(leg, "")
        ro, rd = leg.origin.strip(), leg.destination.strip()
        return _cached_opensky_bundle(fa_fn, extra, ro, rd, lookup_d, ic)
    if n_roster:
        return {
            "source": "opensky",
            "err": "no_upcoming_leg",
            "flight": None,
            "board": None,
            "position": None,
        }
    fn = str(st.session_state.inp_flight).strip()
    return _cached_opensky_bundle(fn, (), "", "", lookup_d, ic)


def _opensky_err_user_message(code: str | None) -> str:
    """Plain-language status line for OpenSky bundle error codes."""
    c = str(code or "").strip()
    if c == "no_flight":
        return (
            "No flight to look up — open the sidebar (**Flight tracker**), enter a callsign or flight number "
            "(e.g. **VIR4**, **VS4**), or import a roster so the next leg supplies a ident. "
            "Positions are only shown when OpenSky has a live ADS-B match."
        )
    if c == "opensky_feed_unavailable":
        return (
            "Could not load OpenSky traffic data from this host (timeout, block, or rate limit). "
            "The app already tries **smaller regional** downloads first for Cloud. "
            "Add a free **OpenSky** login via secrets ``OPENSKY_USERNAME`` / ``OPENSKY_PASSWORD`` (see repo `.env.example`), "
            "then **Refresh live data**. Or run the dashboard locally."
        )
    if c == "not_in_airspace":
        return (
            "No live ADS-B match for that ident right now — the aircraft may be on the ground, outside coverage, "
            "or broadcasting a different callsign. Try **VS47** / **VIR47** (suffixes like **GH** match automatically), "
            "paste **ICAO24** (6 hex) in the sidebar, or set **icao24** on a roster row."
        )
    if c == "no_upcoming_leg":
        return "No upcoming flights in roster"
    if c:
        return f"OpenSky: {c}"
    return "—"


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Flight tracker")
        st.caption(
            "Live ADS-B via **OpenSky** (regional refresh + optional account — see `.env.example`). "
            "Cached about **20s**; use **Refresh live data** to force a new pull."
        )
        st.text_input(
            "Flight number",
            key="inp_flight",
            help="Marketing or radio id (e.g. **VS47**, **VIR47**). Schedule letter suffixes on OpenSky "
            "(e.g. **VS47GH**) are matched to **VS47** automatically when possible.",
        )
        st.text_input(
            "ICAO24 (optional, 6 hex)",
            key="inp_icao24",
            help="From OpenSky Explorer if callsign search fails (example: **407f19**). Overrides roster hex when both apply.",
            placeholder="e.g. 407f19",
        )
        roster_db.init_db()
        st.date_input(
            "Segment / history date (UK calendar)",
            format="DD/MM/YYYY",
            key="inp_fa_date",
            help="Narrows which recent OpenSky ADS-B segment is labeled (UTC day overlap). "
            "Positions are always live; leave empty for **today**.",
        )
        _fd = st.session_state.get("inp_fa_date")
        if isinstance(_fd, datetime):
            _fd = _fd.date()
        if isinstance(_fd, date):
            st.caption(f"OpenSky segment day: **{roster_db.format_uk_date(_fd)}**.")

        if roster_db.roster_row_count():
            st.caption("Non-empty **Flight number** = tracker overrides roster. Leave blank to use the roster.")
        if st.button(
            "Refresh live data",
            help="Clears the in-memory OpenSky cache and refetches.",
            use_container_width=True,
            key="fa_force_refresh",
        ):
            _cached_opensky_bundle.clear()
            st.toast("Live data cache cleared — fetching fresh data.", icon="🔄")
            st.rerun()

        st.markdown("---")
        st.markdown("### My Roster")
        st.caption(
            "Upload a CSV to store legs in SQLite (`roster.sqlite3` next to this app). "
            "The dashboard picks the **next departure after the current time**."
        )
        up = st.file_uploader("Roster CSV", type=["csv"], key="roster_csv")
        if up is not None:
            raw = up.getvalue()
            sig = hashlib.sha256(raw).hexdigest()
            if st.session_state.get("_roster_hash") != sig:
                n, err = roster_db.import_csv_bytes(raw, up.name)
                if err:
                    st.error(err)
                else:
                    st.session_state._roster_hash = sig
                    st.success(f"Imported {n} row(s) from {up.name!r}.")

        c0, c1 = st.columns(2)
        n_roster_rows = roster_db.roster_row_count()
        with c0:
            st.caption(f"**{n_roster_rows}** row(s) in DB")
        with c1:
            if st.button("Clear roster", type="secondary", use_container_width=True):
                roster_db.clear_roster()
                st.session_state.pop("_roster_hash", None)
                st.success("Roster cleared.")
        if n_roster_rows:
            _legs = roster_db.list_all_roster_legs()
            if _legs:
                st.markdown("**Imported flights**")
                st.dataframe(
                    [
                        {
                            "Flight": roster_db.imported_table_flight_label(L),
                            "Date": roster_db.format_uk_date(L.dep.date()),
                            "Time": L.dep.strftime("%H:%M"),
                            "Route": (
                                f"{L.origin.upper()} → {L.destination.upper()}"
                                if (L.origin or L.destination)
                                else "—"
                            ),
                        }
                        for L in _legs
                    ],
                    hide_index=True,
                    use_container_width=True,
                    height=min(260, 38 + 36 * len(_legs)),
                )
        with st.expander("CSV column hints"):
            st.markdown(
                """
**Personal Crew Schedule Report (Virgin-style export)**  
Whole-file import is supported: flying duties with **Details** like `LHR  - BOS` and **Report times** become roster legs. If the row has **VS** / **VIR** in the text, that flight number is used. Otherwise a numeric **Duties** code (e.g. `157`) is mapped to **VS157**. If neither applies, the leg is stored as `LHR-BOS-YYYYMMDD` — **add your VS flight number in the sidebar** for reliable callsign lookup.

**Required (any one alias each)**  
- **Flight:** `flight_number`, `flight`, `flight_no`, `flt`, `ident`  
- **Departure time:** `dep_time`, `departure_time`, `STD`, `ETD`, `departure`, `dep`  

**Optional**  
- **Departure date:** `dep_date`, `flight_date`, `date` (if omitted, the leg rolls **today / tomorrow** from the clock)  
- **Arrival:** `arr_time`, `arrival_time`, `STA`, `ETA`, plus optional `arr_date`  
- **Route:** `origin` / `from` / `dep_airport`, `destination` / `to` / `arr_airport`  
- **Block:** `block_minutes` (used if arrival time is missing; default **90** minutes)  
- **`callsign`** / **`icao24`**: optional hints for OpenSky lookup  

**Main dashboard** uses **OpenSky** (no airline API key).
"""
            )


def _leg_route_arrow(leg: roster_db.RosterLeg | None) -> str:
    if not leg:
        return ""
    o, d = leg.origin.strip().upper(), leg.destination.strip().upper()
    return f"{o} → {d}" if (o or d) else ""


def _virgin_787_layout_eligible(
    user_raw: str,
    row: dict[str, Any] | None,
    leg: roster_db.RosterLeg | None,
    sb: str,
) -> bool:
    """Bundled 787 art: VS ident, tracker row, or Virgin crew roster leg."""
    if not VIRGIN_787_IMAGE.is_file():
        return False
    if flight_identity.is_virgin_atlantic(user_raw, row):
        return True
    if leg and not sb:
        fn = leg.flight_number.strip().upper()
        if fn.startswith("VS") or fn.startswith("VIR"):
            return True
        if roster_db.is_route_only_flight_number(leg.flight_number):
            return True
    return False


def _journey_block_html(metrics: dict[str, str]) -> str:
    keys = (
        ("Flight time", "flight_time"),
        ("Elapsed", "elapsed"),
        ("Remaining", "remaining"),
        ("Miles flown", "miles_flown"),
        ("Miles to go", "miles_to_go"),
        ("Arrival gate", "arr_gate"),
    )
    parts: list[str] = []
    for label, k in keys:
        parts.append(
            f'<div class="hud-journey-cell">'
            f'<span class="hud-journey-k">{escape(label)}</span>'
            f'<span class="hud-journey-v">{escape(str(metrics.get(k, "—")))}</span>'
            f"</div>"
        )
    return f'<div class="hud-journey"><div class="hud-journey-grid">{"".join(parts)}</div></div>'


def _flight_card_html(
    *,
    eyebrow: str,
    flight_no: str,
    flight_subline: str = "",
    dep_route: str,
    dep_when: str,
    arr_route: str,
    arr_when: str,
    journey_html: str,
    status_line: str,
    pct: float,
) -> str:
    sub = (
        f'<div class="hud-card__flight-sub">{escape(str(flight_subline).strip())}</div>'
        if (flight_subline and str(flight_subline).strip())
        else ""
    )
    inner = CARD_INNER_HTML.format(
        eyebrow=escape(str(eyebrow)),
        flight_no=flight_no if flight_no else "—",
        flight_subline=sub,
        dep_route=escape(str(dep_route)),
        dep_when=escape(str(dep_when)),
        arr_route=escape(str(arr_route)),
        arr_when=escape(str(arr_when)),
        journey_html=journey_html,
        status_line=escape(str(status_line)),
        pct=100.0 * pct,
    )
    return f'<div class="hud-wrap">{inner}</div>'


def _render_identity_line(user_input: str | None, row: dict[str, Any] | None) -> None:
    u = (user_input or "").strip()
    ident = flight_identity.build_flight_identity(u, row if isinstance(row, dict) else None)
    st.markdown(
        f'<p class="identity-summary">{escape(ident["summary_line"])}</p>',
        unsafe_allow_html=True,
    )


def _render_live_flight_board(board: dict[str, Any], *, embedded: bool = False) -> None:
    b = board
    tag = str(b.get("data_source_tag") or "OpenSky")
    if embedded:
        st.markdown(f"#### Live ({escape(tag)})")
    else:
        st.markdown(f"##### Live flight ({escape(tag)})")
    trio = geo_places.live_position_lines(b.get("position_lat"), b.get("position_lon"))
    if trio is None:
        st.markdown(
            '<p class="live-currently-over"><span class="live-currently-over__label">Position</span>'
            "<span class=\"live-currently-over__place\">"
            "Not available yet (no coordinates in this board snapshot)"
            "</span></p>",
            unsafe_allow_html=True,
        )
    else:
        lat_line, lon_line, place = trio
        st.markdown(
            f'<div class="live-position-block">'
            f'<div class="live-position-coords">{escape(lat_line)}<br/>{escape(lon_line)}</div>'
            f'<p class="live-position-nearest">'
            f'<span class="live-position-nearest__label">Nearest place</span>'
            f'<span class="live-position-nearest__value">{escape(place)}</span>'
            f"</p></div>",
            unsafe_allow_html=True,
        )
    if embedded:
        u1, u2 = st.columns(2)
        with u1:
            st.markdown('<span class="live-grid">**Tail / reg**</span>', unsafe_allow_html=True)
            st.write(escape(str(b.get("registration") or "—")))
            st.caption(str(b.get("aircraft_type") or "—"))
        with u2:
            st.markdown('<span class="live-grid">**Altitude**</span>', unsafe_allow_html=True)
            st.write(escape(str(b.get("live_altitude") or "—")))
            st.caption(f'Filed: {escape(str(b.get("filed_altitude") or "—"))}')
        v1, v2 = st.columns(2)
        with v1:
            st.markdown('<span class="live-grid">**Speed**</span>', unsafe_allow_html=True)
            st.write(escape(str(b.get("groundspeed_mph") or "—")))
            st.caption(f'Heading {escape(str(b.get("heading_deg") or "—"))}°')
        with v2:
            st.markdown('<span class="live-grid">**Gates**</span>', unsafe_allow_html=True)
            st.write(f'Dep {escape(str(b.get("gate_origin") or "—"))}')
            st.caption(f'Arr {escape(str(b.get("gate_destination") or "—"))}')
        return
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        st.markdown('<span class="live-grid">**Tail / reg**</span>', unsafe_allow_html=True)
        st.write(escape(str(b.get("registration") or "—")))
        st.caption(str(b.get("aircraft_type") or "—"))
    with g2:
        st.markdown('<span class="live-grid">**Altitude**</span>', unsafe_allow_html=True)
        st.write(escape(str(b.get("live_altitude") or "—")))
        st.caption(f'Filed: {escape(str(b.get("filed_altitude") or "—"))}')
    with g3:
        st.markdown('<span class="live-grid">**Speed**</span>', unsafe_allow_html=True)
        st.write(escape(str(b.get("groundspeed_mph") or "—")))
        st.caption(f'Heading {escape(str(b.get("heading_deg") or "—"))}°')
    with g4:
        st.markdown('<span class="live-grid">**Gates**</span>', unsafe_allow_html=True)
        st.write(f'Dep {escape(str(b.get("gate_origin") or "—"))}')
        st.caption(f'Arr {escape(str(b.get("gate_destination") or "—"))}')


def _render_opensky_map(
    pos: opensky_live.AircraftPosition,
    route_adb: opensky_live.FlightRoute | None,
) -> None:
    """Streamlit map: aircraft position plus filed route endpoints when known."""
    st.markdown("##### Map (OpenSky)")
    rows: list[dict[str, Any]] = [{"lat": float(pos.lat), "lon": float(pos.lon)}]
    cap_parts = ["**Aircraft** (live ADS-B)"]
    if route_adb is not None:
        rows.append({"lat": float(route_adb.origin_lat), "lon": float(route_adb.origin_lon)})
        rows.append({"lat": float(route_adb.dest_lat), "lon": float(route_adb.dest_lon)})
        cap_parts.append("**Origin** / **destination** from ADSBDB route (approx.)")
    st.caption(" · ".join(cap_parts) + ".")
    df = pd.DataFrame(rows)
    try:
        st.map(df, zoom=4)
    except TypeError:
        st.map(df)
    st.link_button(
        "Open on OpenSky Explorer (this ICAO24)",
        opensky_live.explorer_detail_url(pos.icao24),
        use_container_width=True,
    )


@st.fragment(run_every=timedelta(seconds=_LIVE_CACHE_TTL))
def live_next_flight_fragment() -> None:
    roster_db.init_db()
    n_roster = roster_db.roster_row_count()
    leg = roster_db.next_roster_leg() if n_roster else None
    bundle = _bundle_for_dashboard(n_roster=n_roster, leg=leg)

    err = bundle.get("err")
    row = bundle.get("flight") if isinstance(bundle.get("flight"), dict) else None
    board = bundle.get("board") or {}
    pos_os = bundle.get("opensky_pos")
    route_adb = bundle.get("opensky_route_adb")

    sb = str(st.session_state.inp_flight).strip()
    if sb:
        user_raw = sb
    elif leg:
        user_raw = leg.flight_number.strip()
    else:
        user_raw = ""
    icao_q = _manual_icao24_sidebar()
    has_tracker_intent = bool(sb) or bool(icao_q) or (n_roster and leg is not None)

    if err in ("no_flight", "opensky_feed_unavailable") and not has_tracker_intent:
        if err == "opensky_feed_unavailable":
            st.warning(_opensky_err_user_message(err))
        else:
            st.caption("Use the **sidebar** to enter a flight or **ICAO24**, or import a **roster CSV**.")
        return

    if err == "opensky_feed_unavailable":
        _render_identity_line(user_raw, row)
        st.error(_opensky_err_user_message(err))
        return

    _render_identity_line(user_raw, row)

    route_txt = _leg_route_arrow(leg)
    flight_subline = ""
    flight_no = "—"
    dep_route = arr_route = dep_when = arr_when = "—"
    pct, status_line = 0.0, "—"
    eyebrow = "OpenSky"
    journey_html = _journey_block_html(opensky_live.empty_journey_metrics())

    if err == "no_upcoming_leg":
        dep_route = arr_route = dep_when = arr_when = "—"
        pct, status_line = 0.0, _opensky_err_user_message(err)
        flight_no = "—"
        eyebrow = "Next flight (roster)"
    elif err:
        flight_subline = ""
        dep_route = arr_route = dep_when = arr_when = "—"
        pct, status_line = 0.0, _opensky_err_user_message(err)
        eyebrow = "Next flight (roster)" if (leg and not sb) else "Flight tracker"
        journey_html = _journey_block_html(opensky_live.empty_journey_metrics())
        if leg and not sb:
            fn_stored = leg.flight_number.strip()
            o_u, d_u = leg.origin.strip().upper(), leg.destination.strip().upper()
            dep_route = escape(f"{o_u} → {d_u}" if (o_u or d_u) else "—")
            dep_when = escape(roster_db.format_uk_datetime(leg.dep))
            arr_route = escape(d_u if d_u else (o_u or "—"))
            arr_when = escape(roster_db.format_uk_datetime(leg.arr))
            if roster_db.is_route_only_flight_number(fn_stored):
                flight_no = escape(roster_db.imported_table_flight_label(leg))
                flight_subline = "Virgin Atlantic"
                if route_txt:
                    flight_subline += f" · {route_txt}"
                flight_subline += " · Try **VIR…** callsign if VS search fails"
            else:
                flight_no = escape(fn_stored)
                flight_subline = "Virgin Atlantic"
                if route_txt:
                    flight_subline += f" · {route_txt}"
        else:
            flight_no = escape(user_raw or "-")
    elif board and pos_os is not None:
        dep_route = escape(str(bundle.get("opensky_dep_route") or "—"))
        arr_route = escape(str(bundle.get("opensky_arr_route") or "—"))
        dep_when = escape(str(bundle.get("opensky_dep_when") or "—"))
        arr_when = escape(str(bundle.get("opensky_arr_when") or "—"))
        pct = float(bundle.get("opensky_fraction") or 0.0)
        status_line = str(bundle.get("opensky_status") or "—")
        if isinstance(status_line, str) and len(status_line) > 120:
            status_line = status_line[:117] + "…"
        journey_html = _journey_block_html(
            opensky_live.journey_metrics_strings(pos_os, route_adb, pct)
        )
        ident_txt = (str(row.get("ident") or "").strip() if row else "") or user_raw
        if n_roster and leg and not sb:
            if not roster_db.is_route_only_flight_number(leg.flight_number.strip()):
                flight_no = escape(leg.flight_number.strip())
            else:
                flight_no = escape(roster_db.imported_table_flight_label(leg))
            flight_subline = "Virgin Atlantic"
            if route_txt:
                flight_subline += f" · {route_txt}"
            eyebrow = "Next leg (OpenSky)"
        else:
            flight_no = escape(ident_txt or "—")
            eyebrow = "Live tracker (OpenSky)"
        if sb and leg and route_txt:
            pre = f"Roster sector · {route_txt}"
            flight_subline = f"{pre} · {flight_subline}" if flight_subline else pre
    else:
        flight_no = escape(user_raw or "—")
        pct, status_line = (
            0.0,
            "No live track to display — if you entered a flight, try **Refresh live data**; "
            "otherwise OpenSky may not have matched this ident yet.",
        )

    card_body = _flight_card_html(
        eyebrow=eyebrow,
        flight_no=flight_no if flight_no else "—",
        flight_subline=flight_subline,
        dep_route=dep_route,
        dep_when=dep_when,
        arr_route=arr_route,
        arr_when=arr_when,
        journey_html=journey_html,
        status_line=status_line,
        pct=pct,
    )
    show_vs_tail = _virgin_787_layout_eligible(
        user_raw,
        row if isinstance(row, dict) else None,
        leg,
        sb,
    )
    if show_vs_tail:
        c_vis, c_card = st.columns([0.42, 0.58])
        with c_vis:
            _vs_png = image_utils.virgin_png_for_ui(VIRGIN_787_IMAGE)
            st.image(
                _vs_png if _vs_png else str(VIRGIN_787_IMAGE),
                use_container_width=True,
            )
            if row and board:
                _render_live_flight_board(board, embedded=True)
        with c_card:
            st.markdown(card_body, unsafe_allow_html=True)
    else:
        st.markdown(card_body, unsafe_allow_html=True)
    st.progress(pct)
    if row and board and not show_vs_tail:
        _render_live_flight_board(board)
    if pos_os is not None and board:
        _render_opensky_map(pos_os, route_adb)


def _route_line_from_bundle(bundle: dict[str, Any], n: int, leg: roster_db.RosterLeg | None) -> str:
    sb = str(st.session_state.inp_flight).strip()
    if bundle.get("board"):
        b = bundle["board"]
        o = escape((b.get("origin_text") or "—").split(" · ")[0][:12])
        d = escape((b.get("destination_text") or "—").split(" · ")[0][:12])
        return f"{o} → {d}"
    if sb:
        return escape(sb) + " — OpenSky…"
    if n and leg:
        o, d = escape(leg.origin.strip().upper()), escape(leg.destination.strip().upper())
        return f"{o} → {d}" if (o or d) else "Roster leg"
    if n:
        return "Roster loaded — no upcoming departure"
    fn = str(st.session_state.inp_flight).strip()
    return escape(fn) + " — OpenSky…" if fn else "Departures"


def main() -> None:
    _expand_sidebar = st.session_state.pop("_force_expand_sidebar", False)
    st.set_page_config(
        page_title=PAGE_TITLE,
        layout="wide",
        initial_sidebar_state="expanded" if _expand_sidebar else "auto",
    )
    st.set_option("client.showSidebarNavigation", False)
    st.markdown(_global_css(), unsafe_allow_html=True)
    roster_db.init_db()
    _init_session_defaults()
    _apply_dashboard_migrations()
    render_sidebar()

    n = roster_db.roster_row_count()
    leg = roster_db.next_roster_leg() if n else None
    bundle = _bundle_for_dashboard(n_roster=n, leg=leg)
    if st.button(
        "Flight tracker & roster",
        help="Opens the left sidebar: flight number, segment date, roster CSV, and refresh.",
        type="secondary",
        key="main_open_sidebar",
    ):
        st.session_state._force_expand_sidebar = True
        st.rerun()
    st.markdown(f'<p class="route-line">{_route_line_from_bundle(bundle, n, leg)}</p>', unsafe_allow_html=True)
    if n and leg and not str(st.session_state.inp_flight).strip():
        route = " → ".join(x for x in (leg.origin.strip().upper(), leg.destination.strip().upper()) if x)
        route_s = route if route else "—"
        if roster_db.is_route_only_flight_number(leg.flight_number):
            st.caption(
                f"**Roster mode:** next sector **{route_s}**, dep **{roster_db.format_uk_datetime(leg.dep)}** "
                f"(imported duty **{leg.callsign or '—'}**). "
                "Use **Segment / history date** in the sidebar to pick another UTC day for OpenSky labels."
            )
        else:
            st.caption(
                f"**Roster mode:** next leg **{leg.flight_number.strip()}**, dep **{roster_db.format_uk_datetime(leg.dep)}** "
                f"({route_s}). Change **Segment / history date** below the flight box to adjust OpenSky segment matching."
            )

    live_next_flight_fragment()


if __name__ == "__main__":
    main()
