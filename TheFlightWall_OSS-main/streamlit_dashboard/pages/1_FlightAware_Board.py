"""
FlightAware-powered flight board: roster or manual flight + date.

Run the app from the parent folder: ``streamlit run app.py`` — this page appears in the sidebar.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import importlib

import streamlit as st
from html import escape

import aeroapi_client
import cookie_prefs
import flight_identity
import image_utils
import roster_db

importlib.reload(aeroapi_client)
importlib.reload(cookie_prefs)
importlib.reload(roster_db)

VIRGIN_787_IMAGE = Path(__file__).resolve().parent.parent / "assets" / "virgin_atlantic_787.png"


def _virgin_787_for_st() -> bytes | str:
    b = image_utils.virgin_png_for_ui(VIRGIN_787_IMAGE)
    return b if b else str(VIRGIN_787_IMAGE)


st.set_page_config(
    page_title="FlightAware Board",
    layout="wide",
    page_icon="✈",
)

st.markdown(
    """
<style>
  .block-container { padding-top: 1rem !important; max-width: 1000px !important; }
  .reg-tail { font-size: 2rem; font-weight: 800; letter-spacing: 0.06em; color: #ff8fb8; }
  .identity-summary { font-size: 1.1rem; font-weight: 650; line-height: 1.4; color: #ffffff; }
  section.main [data-testid="stImage"],
  section.main [data-testid="stImage"] > div,
  section.main [data-testid="stImage"] > div > div { background: transparent !important; }
  section.main [data-testid="stImage"] img {
    mix-blend-mode: normal;
    filter: brightness(1.06) saturate(1.08);
  }
</style>
    """,
    unsafe_allow_html=True,
)

st.title("FlightAware flight board")
st.caption(
    "Data from FlightAware AeroAPI only on this page (no OpenSky). "
    "Virgin Atlantic (VS) flights can show the **bundled 787** artwork when the asset is present."
)

cookie_prefs.hydrate_api_key_widget_defaults()

st.text_input(
    "Your FlightAware AeroAPI key",
    type="password",
    key="user_aeroapi_key",
    help="From FlightAware AeroAPI. Same field as the main dashboard (shared in this browser session). "
    "Optional: remember on this device via the checkbox below.",
    autocomplete="off",
)
st.checkbox(
    "Remember this key on this device",
    key="remember_aeroapi_key",
    help="Stores the key in a browser cookie for about 90 days. Not for shared computers.",
)
cookie_prefs.sync_remembered_api_key()

roster_db.init_db()
api_key = aeroapi_client.get_api_key(session_override=str(st.session_state.get("user_aeroapi_key", "")))
if not api_key:
    st.error(
        "Enter your **FlightAware AeroAPI** key above, or set **AEROAPI_KEY** in a `.env` file next to `app.py` for local runs."
    )
    st.stop()

flight_input = ""
dep_date: date = date.today()
extra_idents: list[str] | None = None

source = st.radio("Data source", ("Manual flight + date", "Roster or upload"), horizontal=True)

if source == "Manual flight + date":
    flight_input = st.text_input(
        "Flight number",
        value="",
        placeholder="e.g. VS3, BA220, VIR3",
        help="IATA+number or ICAO flight ident; date narrows the FlightAware search.",
    ).strip()
    dep_date = st.date_input(
        "Departure date (narrows FlightAware scheduled_out search)",
        value=date.today(),
    )
    extra_idents = None
else:
    up = st.file_uploader("Upload roster CSV (same format as main dashboard)", type=["csv"])
    if up is not None:
        raw = up.getvalue()
        n, err = roster_db.import_csv_bytes(raw, up.name)
        if err:
            st.error(err)
        else:
            st.success(f"Imported {n} row(s).")

    legs = roster_db.list_all_roster_legs()
    if not legs:
        st.info("No rows in the roster DB — upload a CSV or switch to **Manual**.")
    else:
        leg = st.selectbox(
            "Choose a leg",
            legs,
            format_func=lambda L: (
                f"{L.flight_number} · {roster_db.format_uk_datetime(L.dep)} · "
                f"{(L.origin or '—').upper()} → {(L.destination or '—').upper()}"
            ),
        )
        flight_input = leg.flight_number.strip()
        dep_date = leg.dep.date()
        extra_idents = [leg.callsign] if leg.callsign.strip() else None

run = st.button("Load from FlightAware", type="primary", use_container_width=True)

if run and flight_input:
    with st.spinner("Querying FlightAware…"):
        row, ident_used, err = aeroapi_client.find_flight_for_lookup(
            flight_input,
            api_key,
            on_date=dep_date,
            extra_idents_first=extra_idents,
        )
    if err or not row:
        st.warning(
            f"No flight data from AeroAPI for **{flight_input}** on **{roster_db.format_uk_date(dep_date)}** "
            f"(last error code: `{err}`). Try another date, ICAO ident (e.g. VIR3), or check your plan/quota."
        )
        ident = flight_identity.build_flight_identity(flight_input, None)
        if flight_identity.is_virgin_atlantic(flight_input, None) and VIRGIN_787_IMAGE.is_file():
            st.image(_virgin_787_for_st(), width=220)
        st.markdown(f'<p class="identity-summary">{escape(ident["summary_line"])}</p>', unsafe_allow_html=True)
    else:
        fid = row.get("fa_flight_id")
        if not fid:
            st.warning("Flight row has no `fa_flight_id` — live position may be unavailable.")
        pos = aeroapi_client.fetch_position(str(fid), api_key) if fid else None
        board = aeroapi_client.summarize_flight_board(row, pos)
        ap_origin = row.get("origin") if isinstance(row.get("origin"), dict) else None
        ap_dest = row.get("destination") if isinstance(row.get("destination"), dict) else None

        ident = flight_identity.build_flight_identity(flight_input, row)
        _show_vs = flight_identity.is_virgin_atlantic(flight_input, row) and VIRGIN_787_IMAGE.is_file()
        if _show_vs:
            c_id1, c_id2 = st.columns([1, 4])
            with c_id1:
                st.image(_virgin_787_for_st(), use_container_width=True)
            _id_second = c_id2
        else:
            _id_second = st.container()
        with _id_second:
            st.markdown(f'<p class="identity-summary">{escape(ident["summary_line"])}</p>', unsafe_allow_html=True)
            st.caption(
                f"Matched ident **{escape(str(ident_used or '—'))}** · `fa_flight_id` `{escape(str(fid or '—'))}`"
            )

        reg = board.get("registration") or "—"
        st.markdown(f'<p class="reg-tail">Tail / registration · {escape(str(reg))}</p>', unsafe_allow_html=True)
        st.caption(f"Aircraft type **{board.get('aircraft_type') or '—'}**")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### Route")
            st.write(f"**From:** {board.get('origin_text')}")
            st.write(f"**To:** {board.get('destination_text')}")
            st.write(f"**Status:** {board.get('status') or '—'}")
        with c2:
            st.markdown("##### Gates & terminals")
            st.write(f"**Gate (dep):** {board.get('gate_origin') or '—'}")
            st.write(f"**Gate (arr):** {board.get('gate_destination') or '—'}")
            st.write(f"**Terminal (dep):** {board.get('terminal_origin') or '—'}")
            st.write(f"**Terminal (arr):** {board.get('terminal_destination') or '—'}")

        st.markdown("##### Time & movement")
        t1, t2, t3 = st.columns(3)
        with t1:
            st.markdown("**Out / Off** (origin local)")
            st.write(f"Sched out: {aeroapi_client.format_iso_at_airport(row.get('scheduled_out'), ap_origin)}")
            st.write(f"Est out: {aeroapi_client.format_iso_at_airport(row.get('estimated_out'), ap_origin)}")
            st.write(f"Actual out: {aeroapi_client.format_iso_at_airport(row.get('actual_out'), ap_origin)}")
        with t2:
            st.markdown("**In / On** (destination local)")
            st.write(f"Sched in: {aeroapi_client.format_iso_at_airport(row.get('scheduled_in'), ap_dest)}")
            st.write(f"Est in: {aeroapi_client.format_iso_at_airport(row.get('estimated_in'), ap_dest)}")
            st.write(f"Actual in: {aeroapi_client.format_iso_at_airport(row.get('actual_in'), ap_dest)}")
        with t3:
            st.markdown("**Live position (if available)**")
            trio = aeroapi_client.live_position_lines(board.get("position_lat"), board.get("position_lon"))
            if trio:
                lat_line, lon_line, place = trio
                st.write(escape(lat_line))
                st.write(escape(lon_line))
                st.write(f"**Nearest place:** {escape(place)}")
            else:
                st.write("**Position:** not available yet (no live position from FlightAware).")
            st.write(f"**Alt:** {board.get('live_altitude')}")
            st.write(f"**Filed alt:** {board.get('filed_altitude')}")
            st.write(f"**Speed:** {board.get('groundspeed_mph') or '—'}")
            st.write(f"**Hdg:** {board.get('heading_deg') or '—'}°")
            st.write(f"**Pos time:** {aeroapi_client.format_iso_at_airport(board.get('position_time'), None)}")

        with st.expander("Raw FlightAware flight JSON (debug)"):
            st.json(row)

elif run and not flight_input:
    st.warning("Enter or select a flight number.")

st.markdown("---")
st.caption(
    "FlightAware AeroAPI — subject to your account terms and quotas. "
    "Position data depends on your subscription and flight state."
)
