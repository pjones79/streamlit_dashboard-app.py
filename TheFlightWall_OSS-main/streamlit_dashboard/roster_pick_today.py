"""
Read roster.csv with pandas: pick today's flight(s), else the next scheduled flight.
CSV expected columns: Date, Flight_Number (other columns ignored).

Date may be YYYY-MM-DD, MM/DD/YYYY, or other formats pandas can parse.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd


def get_flight_status(flight_id: str) -> dict:
    """Placeholder: simulate fetching live status (replace with real API later)."""
    return {
        "flight_id": flight_id,
        "status": "SCHEDULED",
        "detail": "simulated — replace get_flight_status with a real data source",
    }


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def read_roster_csv(path: str | Path, encoding: str | None = None) -> pd.DataFrame:
    """
    Load CSV with encoding fallbacks (Excel on Windows often writes cp1252, not UTF-8).
    If ``encoding`` is set, only that codec is used.
    """
    path = Path(path)
    raw = path.read_bytes()
    if encoding:
        text = raw.decode(encoding)
        return pd.read_csv(io.StringIO(text))

    last_err: UnicodeDecodeError | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            return pd.read_csv(io.StringIO(text))
        except UnicodeDecodeError as e:
            last_err = e
            continue
    msg = f"Could not decode CSV as text: {path} (tried: utf-8-sig, utf-8, cp1252, latin-1)"
    raise OSError(msg) from last_err


def _find_date_col(df: pd.DataFrame) -> str:
    for name in ("Date", "date", "flight_date", "dep_date"):
        if name in df.columns:
            return name
    raise ValueError(
        "No date column found. Expected one of: Date, date, flight_date, dep_date"
    )


def _find_flight_col(df: pd.DataFrame) -> str:
    for name in ("Flight_Number", "Flight_number", "flight_number", "flight", "Flight"):
        if name in df.columns:
            return name
    raise ValueError(
        "No flight column found. Expected one of: Flight_Number, flight_number, flight, Flight"
    )


def pick_today_or_next(csv_path: str | Path, encoding: str | None = None) -> tuple[str, date, dict]:
    """
    Returns (label, row_date, status_dict)
    label: 'today' | 'next' | 'earliest' (all dates in the past)

    ``encoding``: optional Python codec name for the CSV; if omitted, common encodings are tried.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path.resolve()}")

    df = _norm_cols(read_roster_csv(path, encoding=encoding))
    if df.empty:
        raise ValueError("Roster is empty")

    date_col = _find_date_col(df)
    flight_col = _find_flight_col(df)

    dts = pd.to_datetime(df[date_col], errors="coerce")
    if dts.isna().all():
        raise ValueError(f"Could not parse any dates in column {date_col!r}")

    df = df.assign(_dt=dts.dt.normalize())
    df = df.dropna(subset=["_dt"])
    df["_date"] = df["_dt"].dt.date

    today = date.today()

    today_rows = df[df["_date"] == today]
    if not today_rows.empty:
        flight = str(today_rows.iloc[0][flight_col]).strip()
        return "today", today, get_flight_status(flight)

    future = df[df["_date"] > today].sort_values("_date")
    if not future.empty:
        row = future.iloc[0]
        flight = str(row[flight_col]).strip()
        d = row["_date"]
        return "next", d, get_flight_status(flight)

    # All roster dates are in the past — use earliest row as reference
    earliest = df.sort_values("_date").iloc[0]
    flight = str(earliest[flight_col]).strip()
    d = earliest["_date"]
    return "earliest", d, get_flight_status(flight)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "csv",
        nargs="?",
        default="roster.csv",
        help="Path to roster CSV (default: roster.csv in cwd)",
    )
    p.add_argument(
        "--encoding",
        default=None,
        metavar="ENC",
        help="Force CSV text encoding (e.g. cp1252). Default: try utf-8-sig, utf-8, cp1252, latin-1.",
    )
    args = p.parse_args()

    label, row_date, status = pick_today_or_next(args.csv, encoding=args.encoding)
    flight_id = status["flight_id"]

    if label == "today":
        print(f"Today's flight: {flight_id} ({row_date.strftime('%d/%m/%Y')})")
    elif label == "next":
        print(
            f"No flight dated {date.today().strftime('%d/%m/%Y')}. "
            f"Next scheduled in roster: {flight_id} ({row_date.strftime('%d/%m/%Y')})"
        )
    else:
        print(
            f"No flight today and no future dates in roster. Earliest leg listed: {flight_id} "
            f"({row_date.strftime('%d/%m/%Y')})"
        )

    print("Live data (simulated):", status)


if __name__ == "__main__":
    main()
