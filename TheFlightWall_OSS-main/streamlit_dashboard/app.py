"""
Dark-mode flight dashboard with FlightAware AeroAPI (flight card + live details).
Run: streamlit run app.py
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from dotenv import load_dotenv

    _ENV = Path(__file__).resolve().parent / ".env"
    load_dotenv(_ENV, encoding="utf-8-sig")
    load_dotenv(encoding="utf-8-sig")
except ImportError:
    pass

import importlib

import aeroapi_client
import cookie_prefs
import flight_identity
import image_utils
import flightaware_links
import roster_db

# Streamlit reruns this script but keeps already-imported modules in sys.modules; reload so
# edits to these local modules apply without a full server restart.
importlib.reload(aeroapi_client)
importlib.reload(cookie_prefs)
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


def _parse_iso_dt(val: Any) -> datetime | None:
    if val is None or not isinstance(val, str) or not val.strip():
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    cookie_prefs.hydrate_api_key_widget_defaults()


# Bump when dashboard behavior or defaults change so cache + session quirks reset once per user session.
_DASHBOARD_BUILD = 10


def _compact_flight(s: str) -> str:
    return "".join(str(s).split()).upper()


def _is_removed_demo_flight(s: str) -> bool:
    c = _compact_flight(s)
    return c in ("UA1823", "UAL1823")


def _effective_aeroapi_key() -> str:
    """User-supplied key in the sidebar (session) or ``AEROAPI_KEY`` from the environment."""
    return aeroapi_client.get_api_key(session_override=str(st.session_state.get("user_aeroapi_key", "")))


def _fa_lookup_date() -> date:
    """Calendar day for AeroAPI search. If the sidebar date is empty, use **today**."""
    v = st.session_state.get("inp_fa_date")
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.today()


def _apply_dashboard_migrations() -> None:
    """Clear stale FlightAware cache after upgrades; drop legacy default flight number."""
    if st.session_state.get("_dashboard_build") != _DASHBOARD_BUILD:
        _cached_fa_bundle.clear()
        st.session_state["_dashboard_build"] = _DASHBOARD_BUILD
        st.session_state.pop("inp_dep_t", None)
        st.session_state.pop("inp_fa_date_str", None)
        st.session_state["inp_fa_date"] = None
        st.session_state.pop("_roster_fa_date_sig", None)
    if _is_removed_demo_flight(str(st.session_state.get("inp_flight", ""))):
        st.session_state.inp_flight = ""


_FA_CACHE_TTL = 20

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
    pass origin/destination into FlightAware so ident-date lookups prefer the correct direction
    (same flight number often has outbound and return in the window).
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


@st.cache_data(ttl=_FA_CACHE_TTL, show_spinner=False)
def _cached_fa_bundle(
    api_key: str,
    flight_number: str,
    day_iso: str,
    extra_idents: tuple[str, ...],
    route_o: str,
    route_d: str,
) -> dict[str, Any]:
    """
    Cached FlightAware snapshot (flight row, position, flattened board fields).
    Browser refresh can still show the same snapshot until the TTL elapses — click **Refresh FlightAware now** in the sidebar to clear the cache.
    """
    key = str(api_key).strip()
    out: dict[str, Any] = {
        "err": None,
        "flight": None,
        "position": None,
        "ident_used": None,
        "board": None,
    }
    fn = str(flight_number).strip()
    if not key:
        out["err"] = "missing_AEROAPI_KEY"
        return out
    if not fn:
        out["err"] = "no_flight"
        return out
    try:
        d = date.fromisoformat(day_iso)
    except ValueError:
        out["err"] = "bad_date"
        return out

    row, ident, err = aeroapi_client.find_flight_for_lookup(
        fn,
        key,
        on_date=d,
        extra_idents_first=list(extra_idents) if extra_idents else None,
        origin_hint=route_o.strip() or None,
        dest_hint=route_d.strip() or None,
    )
    if err or not row:
        out["err"] = err or "not_found"
        return out

    out["flight"] = row
    out["ident_used"] = ident
    fid = row.get("fa_flight_id")
    pos = None
    if fid:
        pos = aeroapi_client.fetch_position(str(fid), key)
    out["position"] = pos
    out["board"] = aeroapi_client.summarize_flight_board(row, pos)
    return out


def _bundle_for_dashboard(
    *,
    n_roster: int,
    leg: roster_db.RosterLeg | None,
) -> dict[str, Any]:
    ak = _effective_aeroapi_key()
    sb = str(st.session_state.get("inp_flight", "")).strip()
    lookup_d = _fa_lookup_date().isoformat()
    if sb:
        ro, rd = _roster_route_hints_for_lookup(sb, leg)
        return _cached_fa_bundle(ak, sb, lookup_d, (), ro, rd)
    if n_roster and leg:
        fa_fn, extra = roster_db.flightaware_ident_from_leg(leg, "")
        ro, rd = leg.origin.strip(), leg.destination.strip()
        return _cached_fa_bundle(ak, fa_fn, lookup_d, extra, ro, rd)
    if n_roster:
        return {"err": "no_upcoming_leg", "flight": None, "ident_used": None, "board": None}
    fn = str(st.session_state.inp_flight).strip()
    return _cached_fa_bundle(ak, fn, lookup_d, (), "", "")


def _progress_from_fa(row: dict[str, Any] | None, board: dict[str, Any] | None) -> tuple[float, str]:
    if row is None:
        return 0.0, "—"
    status = (row.get("status") or "").strip() or "—"
    pct_raw = row.get("progress_percent")
    if pct_raw is not None:
        try:
            p = max(0.0, min(100.0, float(pct_raw))) / 100.0
            return p, status
        except (TypeError, ValueError):
            pass
    dep = _parse_iso_dt(aeroapi_client.pick_departure_iso_for_display(row, None))
    arr = _parse_iso_dt(aeroapi_client.pick_arrival_iso_for_display(row, None))
    if dep is None or arr is None:
        return 0.0, status
    if dep.tzinfo is None:
        dep = dep.replace(tzinfo=timezone.utc)
    else:
        dep = dep.astimezone(timezone.utc)
    if arr.tzinfo is None:
        arr = arr.replace(tzinfo=timezone.utc)
    else:
        arr = arr.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if now_utc < dep:
        return 0.0, status
    if now_utc >= arr:
        return 1.0, status
    num = (now_utc - dep).total_seconds()
    den = max((arr - dep).total_seconds(), 1.0)
    return min(max(num / den, 0.0), 1.0), status


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Flight tracker")
        st.text_input(
            "Your FlightAware AeroAPI key",
            type="password",
            key="user_aeroapi_key",
            help="Your key from flightaware.com/commercial/aeroapi — used for this app. "
            "For a private local run you can set AEROAPI_KEY in a .env file instead (no cookie).",
            autocomplete="off",
        )
        st.checkbox(
            "Remember this key on this device",
            key="remember_aeroapi_key",
            help="Saves the key in a first-party browser cookie (about 90 days) for this site only. "
            "Do not use on shared or public computers. Untick to remove the cookie.",
        )
        cookie_prefs.sync_remembered_api_key()
        st.caption(
            "FlightAware data is **cached ~20s** (save API quota). "
            "Use **Refresh FlightAware now** for an immediate pull."
        )
        st.text_input(
            "Flight number",
            key="inp_flight",
            help="FlightAware / AeroAPI ident (e.g. VS4, BA220). If you type anything here, "
            "the main card uses **Flight tracker** mode and **ignores** the roster. "
            "Clear the box to follow the next imported roster leg again.",
        )
        roster_db.init_db()
        st.date_input(
            "FlightAware search date (UK)",
            format="DD/MM/YYYY",
            key="inp_fa_date",
            help="Pick the calendar day for AeroAPI schedule search. Shown as **day / month / year**. "
            "Leave empty to use today’s date for FlightAware lookups.",
        )
        _fd = st.session_state.get("inp_fa_date")
        if isinstance(_fd, datetime):
            _fd = _fd.date()
        if isinstance(_fd, date):
            st.caption(f"FlightAware search day: **{roster_db.format_uk_date(_fd)}**.")

        if roster_db.roster_row_count():
            st.caption("Non-empty **Flight number** = tracker overrides roster. Leave blank to use the roster.")
        if st.button(
            "Refresh FlightAware now",
            help="Clears the in-memory FlightAware cache and refetches the flight and position.",
            use_container_width=True,
            key="fa_force_refresh",
        ):
            _cached_fa_bundle.clear()
            st.toast("FlightAware cache cleared — fetching fresh data.", icon="🔄")
            st.rerun()
        _render_flightaware_sidebar_button()

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
Whole-file import is supported: flying duties with **Details** like `LHR  - BOS` and **Report times** become roster legs. If the row has **VS** / **VIR** in the text, that flight number is used. Otherwise a numeric **Duties** code (e.g. `157`) is mapped to **VS157**. If neither applies, the leg is stored as `LHR-BOS-YYYYMMDD` — **Add your VS flight number in the sidebar for live FlightAware data.**

**Required (any one alias each)**  
- **Flight:** `flight_number`, `flight`, `flight_no`, `flt`, `ident`  
- **Departure time:** `dep_time`, `departure_time`, `STD`, `ETD`, `departure`, `dep`  

**Optional**  
- **Departure date:** `dep_date`, `flight_date`, `date` (if omitted, the leg rolls **today / tomorrow** from the clock)  
- **Arrival:** `arr_time`, `arrival_time`, `STA`, `ETA`, plus optional `arr_date`  
- **Route:** `origin` / `from` / `dep_airport`, `destination` / `to` / `arr_airport`  
- **Block:** `block_minutes` (used if arrival time is missing; default **90** minutes)  
- **`callsign`** / **`icao24`**: optional hints for lookups elsewhere  

**Main dashboard** uses **FlightAware AeroAPI** — add **Your FlightAware AeroAPI key** in the sidebar (or `AEROAPI_KEY` in `.env` when you run locally).
"""
            )


def _render_flightaware_sidebar_button() -> None:
    sb = str(st.session_state.get("inp_flight", "")).strip()
    if sb:
        ident = flightaware_links.preferred_flightaware_ident(sb, None, None)
        label = "Open tracker flight on FlightAware (UK)"
        url = flightaware_links.live_flight_url(ident)
        st.link_button(label, url, use_container_width=True)
        st.caption("Opens uk.flightaware.com in your browser (full site).")
        return

    n = roster_db.roster_row_count()
    if n:
        leg = roster_db.next_roster_leg()
        if leg is None:
            st.caption("FlightAware web: no upcoming roster leg.")
            return
        fa_fn, _ = roster_db.flightaware_ident_from_leg(leg, "")
        ident = flightaware_links.preferred_flightaware_ident(
            fa_fn,
            (leg.callsign or "").strip() or None,
            None,
        )
        label = "Open roster flight on FlightAware (UK)"
    else:
        st.caption("Enter a flight number for the web link.")
        return
    url = flightaware_links.live_flight_url(ident)
    st.link_button(label, url, use_container_width=True)
    st.caption("Opens uk.flightaware.com in your browser (full site).")


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
    """Bundled 787 art: VS ident, FlightAware row, or Virgin crew roster leg."""
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
    if embedded:
        st.markdown("#### Live (FlightAware)")
    else:
        st.markdown("##### Live flight (FlightAware)")
    trio = aeroapi_client.live_position_lines(b.get("position_lat"), b.get("position_lon"))
    if trio is None:
        st.markdown(
            '<p class="live-currently-over"><span class="live-currently-over__label">Position</span>'
            "<span class=\"live-currently-over__place\">"
            "Not available yet (no live position from FlightAware)"
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


@st.fragment(run_every=timedelta(seconds=_FA_CACHE_TTL))
def live_next_flight_fragment() -> None:
    roster_db.init_db()
    n_roster = roster_db.roster_row_count()
    leg = roster_db.next_roster_leg() if n_roster else None
    bundle = _bundle_for_dashboard(n_roster=n_roster, leg=leg)

    err = bundle.get("err")
    row = bundle.get("flight")
    board = bundle.get("board") or {}
    pos = bundle.get("position")

    sb = str(st.session_state.inp_flight).strip()
    if sb:
        user_raw = sb
    elif leg:
        user_raw = leg.flight_number.strip()
    else:
        user_raw = ""
    _render_identity_line(user_raw, row if isinstance(row, dict) else None)

    flight_subline = ""

    if not _effective_aeroapi_key():
        flight_no = "—"
        dep_route = arr_route = dep_when = arr_when = "—"
        pct, status_line = 0.0, "Add your FlightAware AeroAPI key in the sidebar (or set AEROAPI_KEY locally)"
        eyebrow = "FlightAware"
        journey_html = _journey_block_html(aeroapi_client.journey_metrics_strings(None, None, {}))
    elif n_roster and leg is None and not sb:
        dep_route = arr_route = dep_when = arr_when = "—"
        pct, status_line = 0.0, "No upcoming flights in roster"
        flight_no = "—"
        eyebrow = "Next flight (roster)"
        journey_html = _journey_block_html(aeroapi_client.journey_metrics_strings(None, None, {}))
    elif err:
        flight_subline = ""
        dep_route = arr_route = dep_when = arr_when = "—"
        pct, status_line = 0.0, f"FlightAware: {err}"
        eyebrow = "Next flight (roster)" if (leg and not sb) else "Flight tracker"
        journey_html = _journey_block_html(aeroapi_client.journey_metrics_strings(None, None, {}))
        if leg and not sb:
            fn_stored = leg.flight_number.strip()
            route_txt = _leg_route_arrow(leg)
            o_u, d_u = leg.origin.strip().upper(), leg.destination.strip().upper()
            dep_route = escape(
                f"{o_u} → {d_u}" if (o_u or d_u) else "—",
            )
            dep_when = escape(roster_db.format_uk_datetime(leg.dep))
            arr_route = escape(d_u if d_u else (o_u or "—"))
            arr_when = escape(roster_db.format_uk_datetime(leg.arr))
            if roster_db.is_route_only_flight_number(fn_stored):
                flight_no = escape(roster_db.imported_table_flight_label(leg))
                flight_subline = "Virgin Atlantic"
                if route_txt:
                    flight_subline += f" · {route_txt}"
                flight_subline += " · Add your VS flight number in the sidebar for live FlightAware data"
            else:
                flight_no = escape(fn_stored)
                flight_subline = "Virgin Atlantic"
                if route_txt:
                    flight_subline += f" · {route_txt}"
        else:
            flight_no = escape(user_raw or "-")
    elif row:
        origin = row.get("origin") if isinstance(row.get("origin"), dict) else None
        dest = row.get("destination") if isinstance(row.get("destination"), dict) else None
        dep_route = aeroapi_client.airport_route_line(origin)
        arr_route = aeroapi_client.airport_route_line(dest)
        dep_when = aeroapi_client.format_iso_at_airport_datetime_only(
            aeroapi_client.pick_departure_iso_for_display(row, pos if isinstance(pos, dict) else None),
            origin,
        )
        arr_when = aeroapi_client.format_iso_at_airport_datetime_only(
            aeroapi_client.pick_arrival_iso_for_display(row, pos if isinstance(pos, dict) else None),
            dest,
        )
        pct, status_line = _progress_from_fa(row, board)
        fn = (row.get("ident_iata") or row.get("ident_icao") or row.get("ident") or "").strip()
        route_txt = _leg_route_arrow(leg)
        flight_subline = ""
        if n_roster and leg and not sb:
            ident = (row.get("ident_iata") or row.get("ident_icao") or row.get("ident") or "").strip()
            if ident:
                flight_no = escape(ident)
            elif not roster_db.is_route_only_flight_number(leg.flight_number):
                flight_no = escape(leg.flight_number.strip())
            else:
                flight_no = escape(roster_db.imported_table_flight_label(leg))
            flight_subline = "Virgin Atlantic"
            if route_txt:
                flight_subline += f" · {route_txt}"
            if roster_db.is_route_only_flight_number(leg.flight_number) and not ident:
                flight_subline += " · Add your VS flight number in the sidebar for live FlightAware data"
            eyebrow = "Next flight (FlightAware)"
        else:
            flight_no = escape(sb or fn or "—")
            eyebrow = "Flight tracker (FlightAware)"
            if sb and leg and route_txt:
                flight_subline = f"Roster sector · {route_txt}"
            elif flight_identity.is_virgin_atlantic(sb or user_raw, row):
                flight_subline = "Virgin Atlantic"
        if isinstance(status_line, str) and len(status_line) > 120:
            status_line = status_line[:117] + "…"
        journey_html = _journey_block_html(
            aeroapi_client.journey_metrics_strings(
                row,
                pos if isinstance(pos, dict) else None,
                board,
            )
        )
    else:
        flight_no = "—"
        flight_subline = ""
        dep_route = arr_route = dep_when = arr_when = "—"
        pct, status_line = 0.0, "—"
        eyebrow = "FlightAware"
        journey_html = _journey_block_html(aeroapi_client.journey_metrics_strings(None, None, {}))

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


def _route_line_from_bundle(bundle: dict[str, Any], n: int, leg: roster_db.RosterLeg | None) -> str:
    row = bundle.get("flight")
    sb = str(st.session_state.inp_flight).strip()
    if row and bundle.get("board"):
        b = bundle["board"]
        o = escape((b.get("origin_text") or "—").split(" · ")[0][:12])
        d = escape((b.get("destination_text") or "—").split(" · ")[0][:12])
        return f"{o} → {d}"
    if sb:
        return escape(sb) + " — FlightAware…"
    if n and leg:
        o, d = escape(leg.origin.strip().upper()), escape(leg.destination.strip().upper())
        return f"{o} → {d}" if (o or d) else "Roster leg"
    if n:
        return "Roster loaded — no upcoming departure"
    fn = str(st.session_state.inp_flight).strip()
    return escape(fn) + " — FlightAware…" if fn else "Flight tracker"


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
        help="Opens the left sidebar: flight number, FlightAware search date, roster CSV, and refresh.",
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
                "Use **FlightAware search date** in the sidebar to query another day."
            )
        else:
            st.caption(
                f"**Roster mode:** next leg **{leg.flight_number.strip()}**, dep **{roster_db.format_uk_datetime(leg.dep)}** "
                f"({route_s}). Change **FlightAware search date** below the flight box to search a different day."
            )

    live_next_flight_fragment()


if __name__ == "__main__":
    main()
