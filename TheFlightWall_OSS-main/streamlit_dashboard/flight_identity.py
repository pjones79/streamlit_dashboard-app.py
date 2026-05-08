"""
Marketing / ICAO / IATA flight labels (no remote logos; VS branding uses a bundled asset in app.py).
"""
from __future__ import annotations

import re
from typing import Any

AIRLINE_BY_IATA: dict[str, tuple[str, str]] = {
    "VS": ("Virgin Atlantic", "VIR"),
}

AIRLINE_BY_ICAO: dict[str, tuple[str, str]] = {
    "VIR": ("Virgin Atlantic", "VS"),
}


def _norm_compact(s: str) -> str:
    return "".join(str(s).split()).upper()


def _split_carrier_digits(compact: str) -> tuple[str | None, str | None, str]:
    c = _norm_compact(compact)
    if not c:
        return None, None, ""
    if len(c) >= 4 and c[:3].isalpha() and c[3:].isdigit():
        return None, c[:3], c[3:]
    if len(c) >= 3 and c[:2].isalpha() and c[2:].isdigit():
        return c[:2], None, c[2:]
    return None, None, ""


def _pad_digits(digits: str) -> str:
    if not digits.isdigit():
        return digits
    if len(digits) <= 3:
        return digits.zfill(3)
    return digits.zfill(4)


def build_flight_identity(user_input: str, flight_row: dict[str, Any] | None) -> dict[str, Any]:
    compact = _norm_compact(user_input)
    iata_guess, icao_guess, digits_guess = _split_carrier_digits(compact)

    airline_name: str | None = None
    iata: str | None = None
    icao: str | None = None
    digits: str = digits_guess

    if flight_row:
        airline_name = (flight_row.get("operator") or "").strip() or None
        iata = ((flight_row.get("operator_iata") or "") or "").strip().upper() or None
        icao = ((flight_row.get("operator_icao") or "") or "").strip().upper() or None
        ident_iata = ((flight_row.get("ident_iata") or "") or "").strip().upper()
        ident_icao = ((flight_row.get("ident_icao") or "") or "").strip().upper()
        fn = str(flight_row.get("flight_number") or "").strip().upper()

        if ident_iata and len(ident_iata) >= 3 and ident_iata[2:].isdigit():
            iata = iata or ident_iata[:2]
            digits = ident_iata[2:] or digits
        elif ident_icao and len(ident_icao) >= 4 and ident_icao[3:].isdigit():
            icao = icao or ident_icao[:3]
            digits = ident_icao[3:] or digits
        elif fn:
            m = re.match(r"^([A-Z]{2})(\d+)$", fn.replace(" ", ""))
            if m:
                iata = iata or m.group(1)
                digits = m.group(2) or digits

    iata = (iata or iata_guess or "").strip().upper() or None
    icao = (icao or icao_guess or "").strip().upper() or None
    if not digits:
        digits = digits_guess

    if iata and iata in AIRLINE_BY_IATA:
        name_br, icao_br = AIRLINE_BY_IATA[iata]
        airline_name = airline_name or name_br
        icao = icao or icao_br
    if icao and icao in AIRLINE_BY_ICAO:
        name_br, iata_br = AIRLINE_BY_ICAO[icao]
        airline_name = airline_name or name_br
        iata = iata or iata_br

    pad = _pad_digits(digits) if digits.isdigit() else (digits or "")

    vir_style = f"{icao}{pad}" if icao and pad else "—"
    vs_style = f"{iata}{pad}" if iata and pad else "—"

    if iata and digits:
        marketing = f"{iata.title()}{digits}"
    else:
        marketing = user_input.strip() or "—"

    bits: list[str] = []
    if airline_name:
        bits.append(airline_name)
    bits.append(f"flight {marketing}" if marketing != "—" else marketing)
    if vir_style != "—":
        bits.append(vir_style)
    if vs_style != "—" and vs_style != vir_style:
        bits.append(vs_style)
    elif vs_style != "—" and vir_style == "—":
        bits.append(vs_style)

    return {
        "airline_name": airline_name or "—",
        "marketing": marketing,
        "ident_icao_padded": vir_style,
        "ident_iata_padded": vs_style,
        "summary_line": ", ".join(bits),
    }


def is_virgin_atlantic(user_input: str, flight_row: dict[str, Any] | None) -> bool:
    """True when the flight is Virgin Atlantic (VS / VIR)."""
    info = build_flight_identity(user_input.strip(), flight_row)
    iata_p = (info.get("ident_iata_padded") or "").upper()
    icao_p = (info.get("ident_icao_padded") or "").upper()
    if iata_p.startswith("VS") and iata_p != "—":
        return True
    if icao_p.startswith("VIR") and icao_p != "—":
        return True
    return "VIRGIN" in (info.get("airline_name") or "").upper()
