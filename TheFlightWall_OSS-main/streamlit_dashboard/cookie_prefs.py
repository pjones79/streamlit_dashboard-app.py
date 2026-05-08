"""
Optional browser cookie storage for the FlightAware API key (\"Remember me\").

Uses ``extra-streamlit-components`` CookieManager (first-party cookies for this app origin).
If the dependency is missing, helpers no-op and keys are session-only.
"""
from __future__ import annotations

import datetime
from typing import Any

import streamlit as st

_COOKIE_NAME = "flightwall_aeroapi_v1"
_REMEMBER_DAYS = 90
_MISSING = object()


def _manager() -> Any | None:
    if "_fw_cookie_mgr" not in st.session_state:
        try:
            import extra_streamlit_components as stx
        except ImportError:
            st.session_state._fw_cookie_mgr = _MISSING
            return None
        st.session_state._fw_cookie_mgr = stx.CookieManager(key="flightwall_cookie_mgr")
    m = st.session_state._fw_cookie_mgr
    if m is _MISSING:
        return None
    return m


def load_stored_aeroapi_key() -> str:
    m = _manager()
    if m is None:
        return ""
    try:
        v = m.get(_COOKIE_NAME)
        return str(v).strip() if v else ""
    except Exception:
        return ""


def hydrate_api_key_widget_defaults() -> None:
    if "user_aeroapi_key" not in st.session_state:
        st.session_state.user_aeroapi_key = load_stored_aeroapi_key()
    if "remember_aeroapi_key" not in st.session_state:
        st.session_state.remember_aeroapi_key = bool(
            str(st.session_state.get("user_aeroapi_key", "")).strip()
        )


def sync_remembered_api_key() -> None:
    """Persist or clear cookie from current session_state (call after widgets bind)."""
    m = _manager()
    if m is None:
        return
    remember = bool(st.session_state.get("remember_aeroapi_key", False))
    raw = str(st.session_state.get("user_aeroapi_key", "")).strip()
    if remember and raw:
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=_REMEMBER_DAYS)
        m.set(
            _COOKIE_NAME,
            raw,
            key="fw_ck_set",
            path="/",
            expires_at=exp,
            same_site="lax",
            secure=None,
        )
    else:
        try:
            if m.get(_COOKIE_NAME):
                m.delete(_COOKIE_NAME, key="fw_ck_del")
        except Exception:
            pass
