"""SQLite roster storage and CSV import for the flight dashboard."""
from __future__ import annotations

# Shown in import error messages so you can tell whether Streamlit picked up this file
# (see importlib.reload(roster_db) in app.py — without reload, edits here stay stale).
ROSTER_CSV_LOADER_TAG = "stdlib-csv-v3"

import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

_ALIASES: dict[str, tuple[str, ...]] = {
    "flight_number": (
        "flight_number",
        "flight",
        "flight_no",
        "flt",
        "fn",
        "ident",
        "flightnumber",
    ),
    "dep_time": (
        "dep_time",
        "departure_time",
        "departure",
        "std",
        "etd",
        "dep",
        "out",
    ),
    "dep_date": ("dep_date", "flight_date", "date", "day", "departure_date"),
    "arr_time": (
        "arr_time",
        "arrival_time",
        "arrival",
        "sta",
        "eta",
        "arr",
        "in",
    ),
    "arr_date": ("arr_date", "arrival_date"),
    "origin": ("origin", "from", "dep_airport", "orig", "departure_airport"),
    "destination": (
        "destination",
        "to",
        "arr_airport",
        "dest",
        "arrival_airport",
    ),
    "block_minutes": ("block_minutes", "block", "dur", "duration_min", "flight_time_min"),
    "callsign": ("callsign", "adsb_callsign", "cs", "radio_callsign"),
    "icao24": ("icao24", "icao", "hex", "mode_s", "modes"),
}


def db_path() -> Path:
    return Path(__file__).resolve().parent / "roster.sqlite3"


def format_uk_date(d: date) -> str:
    """Display a :class:`~datetime.date` as **DD/MM/YYYY** (UK)."""
    return d.strftime("%d/%m/%Y")


def format_uk_datetime(dt: datetime) -> str:
    """Display a naive/local :class:`~datetime.datetime` as **DD/MM/YYYY HH:MM** (24h, UK date)."""
    return dt.strftime("%d/%m/%Y %H:%M")


_UK_DATE_INPUT_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s*$")


def parse_uk_date(s: str) -> date | None:
    """
    Parse a string as **DD/MM/YYYY** (day first, then month). Whitespace stripped.
    Accepts 1- or 2-digit day and month (e.g. ``8/5/2026`` or ``08/05/2026``).
    """
    m = _UK_DATE_INPUT_RE.match((s or "").strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _norm_col(name: str) -> str:
    return "".join(
        c.lower() if c.isalnum() else "_" for c in name.strip()
    ).strip("_")


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """Map logical field -> actual dataframe column name."""
    rev = {_norm_col(c): c for c in df.columns}
    out: dict[str, str] = {}
    for logical, options in _ALIASES.items():
        for opt in options:
            key = _norm_col(opt)
            if key in rev:
                out[logical] = rev[key]
                break
    return out


# Roster legs imported from crew reports without a flight number use this pattern;
# FlightAware lookup should fall back to the sidebar "Flight number" when present.
ROUTE_ONLY_FLIGHT_NUMBER_RE = re.compile(r"^([A-Z]{3})-([A-Z]{3})-(\d{8})$")


def is_route_only_flight_number(value: str) -> bool:
    return bool(ROUTE_ONLY_FLIGHT_NUMBER_RE.match(str(value).strip()))


def flightaware_ident_from_leg(leg: RosterLeg, sidebar_flight: str) -> tuple[str, tuple[str, ...]]:
    """
    Flight number / ident for AeroAPI and FlightAware links.

    If ``sidebar_flight`` is non-empty, it **overrides** the stored leg flight for lookups
    (same roster departure date). Otherwise uses ``leg.flight_number`` (including route-only
    placeholders like ``LHR-BOS-YYYYMMDD`` when the CSV had no VS number).
    """
    fn = leg.flight_number.strip()
    extra = (leg.callsign.strip(),) if leg.callsign.strip() else ()
    alt = str(sidebar_flight).strip()
    if alt:
        return alt, extra
    return fn, extra


def imported_table_flight_label(leg: RosterLeg) -> str:
    """
    Sidebar **Imported flights** table: show the flight without duplicating the calendar date.
    Route-only placeholders are ``AAA-BBB-YYYYMMDD`` — date belongs in the Date column only.
    """
    fn = leg.flight_number.strip()
    if is_route_only_flight_number(fn):
        o, d = leg.origin.strip().upper(), leg.destination.strip().upper()
        if o and d:
            return f"{o}-{d}"
        m = ROUTE_ONLY_FLIGHT_NUMBER_RE.match(fn)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    return fn


_DDMY_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")
_ROUTE_CELL_RE = re.compile(r"^\*?\s*([A-Z]{3})\s*-\s*([A-Z]{3})\s*$", re.IGNORECASE)
_FLIGHT_NUM_RE = re.compile(r"\b(?:VS|VIR)\s*-?\s*(\d{1,4}[A-Z]?)\b", re.IGNORECASE)
_DUTY_FLIGHT_RE = re.compile(r"^\d+$")
# Numeric **Duties** cell on flying sectors often matches the public flight number (e.g. duty ``157`` → VS157)
# when the export does not spell out ``VS`` in the Crew block.
_DUTY_AS_VS_MAX = 9999
_SKIP_DETAILS_SUBSTR = (
    "day off",
    "standby",
    "reserve duty",
    "part time",
    "annual leave",
    "my day",
    "groundschool",
    "pattern",
)


def _marketing_flight_from_crew_row(
    blob: str,
    duties_first: str,
    orig: str,
    dest: str,
    dep_d: date,
) -> str:
    """
    Prefer explicit ``VS`` / ``VIR`` in the row text; otherwise use a numeric **Duties** code
    as ``VS{n}`` (common on Virgin exports where the CSV omits the ``VS`` prefix).
    """
    fm = _FLIGHT_NUM_RE.search(blob)
    if fm:
        num = fm.group(1)
        return f"VS{int(num)}" if num.isdigit() else f"VS{num}"
    if _DUTY_FLIGHT_RE.match(duties_first):
        try:
            dnum = int(duties_first, 10)
        except ValueError:
            dnum = -1
        if 1 <= dnum <= _DUTY_AS_VS_MAX:
            return f"VS{dnum}"
    return f"{orig}-{dest}-{dep_d.strftime('%Y%m%d')}"


def _parse_dd_mm_yyyy(cell: str) -> date | None:
    m = _DDMY_RE.match((cell or "").strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _first_line(s: str) -> str:
    return (s or "").split("\n", 1)[0].strip()


def _empty_crew_schedule_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "flight_number",
            "dep_date",
            "dep_time",
            "origin",
            "destination",
            "callsign",
            "arr_time",
        ]
    )


def _crew_schedule_header_keys_ok(hmap: dict[str, int]) -> bool:
    """True if this row's headers look like Virgin-style Date/Duties/Details/Report table."""
    try:
        _col_idx(hmap, "date")
        _col_idx(hmap, "duties", "duty")
        _col_idx(hmap, "details")
        _col_idx(
            hmap,
            "report times",
            "report",
            "std",
            "etd",
            "check in times",
            "chk in",
        )
        return True
    except KeyError:
        return False


def _find_crew_schedule_header_line(lines: list[str]) -> int | None:
    """
    Locate the schedule header row. Exports often quote fields (``"Date","Duties",…``), so we
    parse candidate lines with :mod:`csv` instead of a ``Date[,;]…`` regex (which misses a
    leading quote and wrongly falls back to pandas / the C tokenizer).
    """
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        for delim in (",", ";", "\t"):
            try:
                row = next(csv.reader(io.StringIO(line), delimiter=delim))
            except Exception:
                continue
            if len(row) < 6:
                continue
            if _crew_schedule_header_keys_ok(_header_key_to_index([c.strip() for c in row])):
                return i
    return None


def _header_key_to_index(header_cells: list[str]) -> dict[str, int]:
    """Map normalized header label -> column index (handles case, extra spaces, BOM)."""
    out: dict[str, int] = {}
    for i, raw in enumerate(header_cells):
        h = raw.strip().lstrip("\ufeff").strip()
        key = " ".join(h.lower().split())
        out[key] = i
    return out


def _col_idx(norm_index: dict[str, int], *aliases: str) -> int:
    for a in aliases:
        k = " ".join(a.strip().lower().split())
        if k in norm_index:
            return norm_index[k]
    raise KeyError(aliases[0])


def _col_idx_optional(norm_index: dict[str, int], *aliases: str) -> int | None:
    try:
        return _col_idx(norm_index, *aliases)
    except KeyError:
        return None


def _parse_crew_schedule_report(text: str) -> pd.DataFrame | None:
    """
    Virgin-style **Personal Crew Schedule Report** CSV: preamble, then table
    ``Date,Duties,Details,Report times,...`` with multi-line quoted **Crew** cells.

    Extracts flying sectors (numeric duty + ``AAA  -  BBB`` route + report time).

    Returns ``None`` if this file does not contain the crew schedule header row.
    Returns an **empty** DataFrame (correct columns) if the table was found but no
    flying rows matched — avoids falling back to pandas on the preamble (which
    triggers the infamous "Expected 2 fields… saw 8" C-parser error).
    """
    lines = text.splitlines()
    start = _find_crew_schedule_header_line(lines)
    if start is None:
        return None

    subset = "\n".join(lines[start:])
    table: list[list[str]] | None = None
    for delim in (",", ";", "\t"):
        t = list(csv.reader(io.StringIO(subset), delimiter=delim))
        if not t or len(t[0]) < 6:
            continue
        hdr = [h.strip() for h in t[0]]
        hmap_try = _header_key_to_index(hdr)
        if not _crew_schedule_header_keys_ok(hmap_try):
            continue
        table = t
        break
    if table is None:
        return _empty_crew_schedule_df()
    if len(table) < 2:
        return _empty_crew_schedule_df()

    header = [h.strip() for h in table[0]]
    try:
        hmap = _header_key_to_index(header)
        i_date = _col_idx(hmap, "date")
        i_duties = _col_idx(hmap, "duties", "duty")
        i_details = _col_idx(hmap, "details")
        i_report = _col_idx(
            hmap,
            "report times",
            "report",
            "std",
            "etd",
            "check in times",
            "chk in",
        )
    except KeyError:
        return _empty_crew_schedule_df()
    i_debrief = _col_idx_optional(
        hmap,
        "debrief times",
        "debrief",
        "actual times/delays",
        "actual times",
        "sta",
        "eta",
    )

    out_rows: list[dict[str, Any]] = []

    for row in table[1:]:
        if not row or i_date >= len(row):
            continue
        c0 = row[i_date].strip()
        if any(
            c0.startswith(m)
            for m in (
                "Total Hours",
                "Hotel Information",
                "Descriptions",
                "Generated on",
                "Block Hours",
            )
        ):
            break
        dep_d = _parse_dd_mm_yyyy(c0)
        if dep_d is None:
            continue

        details = row[i_details].strip() if i_details < len(row) else ""
        dl = details.lower()
        if not details or any(x in dl for x in _SKIP_DETAILS_SUBSTR):
            continue

        duties_first = _first_line(row[i_duties] if i_duties < len(row) else "")
        if not _DUTY_FLIGHT_RE.match(duties_first):
            continue

        single_line_details = " ".join(details.split())
        rm = _ROUTE_CELL_RE.match(single_line_details.strip())
        if not rm:
            continue
        orig, dest = rm.group(1).upper(), rm.group(2).upper()

        report = row[i_report].strip() if i_report < len(row) else ""
        if not report:
            continue
        tm_m = re.search(r"\b(\d{1,2}:\d{2})\b", report)
        if not tm_m:
            continue
        dep_time_s = tm_m.group(1)

        debrief = (
            row[i_debrief].strip()
            if i_debrief is not None and i_debrief < len(row)
            else ""
        )
        arr_time_s: str | None = None
        if debrief:
            cleaned = debrief.split("?", 1)[0]
            tm2 = re.search(r"\b(\d{1,2}:\d{2})\b", cleaned)
            if tm2:
                arr_time_s = tm2.group(1)

        blob = " ".join(x.strip() for x in row if x)
        flight_number = _marketing_flight_from_crew_row(blob, duties_first, orig, dest, dep_d)

        r: dict[str, Any] = {
            "flight_number": flight_number,
            "dep_date": dep_d,
            "dep_time": dep_time_s,
            "origin": orig,
            "destination": dest,
            "callsign": duties_first,
        }
        if arr_time_s:
            r["arr_time"] = arr_time_s
        out_rows.append(r)

    if not out_rows:
        return _empty_crew_schedule_df()
    return pd.DataFrame(out_rows)


def _fmt_time(t: time) -> str:
    return t.strftime("%H:%M:%S")


def _parse_time_iso(s: str) -> time:
    if not s:
        return time(0, 0)
    s = s.strip()
    if len(s) <= 8 and s.count(":") >= 1:
        parts = s.split(":")
        h, m = int(parts[0]), int(parts[1])
        sec = int(parts[2]) if len(parts) > 2 else 0
        return time(h, m, sec)
    return time.fromisoformat(s)


def _parse_time(val) -> time | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, time):
        return val
    if isinstance(val, datetime):
        return val.time()
    s = str(val).strip()
    if not s:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime().time()


def _parse_date(val) -> date | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    if not s:
        return None
    ts = pd.to_datetime(s, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.to_pydatetime().date()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roster (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              flight_number TEXT NOT NULL,
              dep_date TEXT,
              dep_time TEXT NOT NULL,
              arr_date TEXT,
              arr_time TEXT,
              origin TEXT,
              destination TEXT,
              block_minutes INTEGER,
              source_file TEXT,
              imported_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_roster_flight ON roster(flight_number)"
        )
        _ensure_roster_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_roster_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(roster)").fetchall()}
    if "callsign" not in existing:
        conn.execute("ALTER TABLE roster ADD COLUMN callsign TEXT")
    if "icao24" not in existing:
        conn.execute("ALTER TABLE roster ADD COLUMN icao24 TEXT")


def clear_roster() -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute("DELETE FROM roster")
        conn.commit()
    finally:
        conn.close()


def roster_row_count() -> int:
    if not db_path().exists():
        return 0
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*) FROM roster").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _read_csv_bytes(data: bytes) -> pd.DataFrame:
    """
    Parse CSV bytes with Windows-friendly encoding fallbacks (Excel often uses cp1252).

    Tolerates inconsistent preamble rows (title lines before the real header), alternate
    delimiters (``,`` / ``;`` / tab), and a few malformed lines (skipped) — common with
    roster exports and Excel "Save As CSV".
    """
    last_err: UnicodeDecodeError | None = None
    text: str | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError as e:
            last_err = e
            continue
    if text is None:
        raise OSError(
            "Could not decode CSV bytes (tried: utf-8-sig, utf-8, cp1252, latin-1)"
        ) from last_err

    crew_df = _parse_crew_schedule_report(text)
    if crew_df is not None:
        return crew_df

    def _dataframe_from_text(skiprows: int, sep: str) -> pd.DataFrame | None:
        """Build a DataFrame using :mod:`csv` only — avoids pandas' C tokenizer entirely."""
        lines = text.splitlines()
        if skiprows >= len(lines):
            return None
        blob = "\n".join(lines[skiprows:])
        try:
            rows = list(csv.reader(io.StringIO(blob), delimiter=sep))
        except Exception:
            return None
        if not rows or len(rows) < 2:
            return None
        header = [str(h).strip() for h in rows[0]]
        if len(header) < 2:
            return None
        width = len(header)
        body: list[list[str]] = []
        for r in rows[1:]:
            if not any(str(c).strip() for c in r):
                continue
            cells = [str(c).strip() for c in r]
            if len(cells) < width:
                cells.extend([""] * (width - len(cells)))
            elif len(cells) > width:
                cells = cells[:width]
            body.append(cells)
        if not body:
            return None
        try:
            return pd.DataFrame(body, columns=header)
        except (ValueError, TypeError):
            return None

    def _try_read(skiprows: int, sep: str | None) -> pd.DataFrame | None:
        delims: list[str] = [sep] if sep is not None else [",", ";", "\t", "|"]
        for d in delims:
            df = _dataframe_from_text(skiprows, d)
            if df is not None and not df.empty:
                return df
        return None

    def _trim_junk_columns(df: pd.DataFrame) -> pd.DataFrame:
        keep: list[str] = []
        for c in df.columns:
            cs = str(c).strip()
            if cs.startswith("Unnamed"):
                if df[c].isna().all():
                    continue
            keep.append(c)
        return df[keep] if keep else df

    # Prefer a table that actually maps to our required roster columns (handles preamble rows).
    for skip in range(0, 25):
        for sep in (None, ",", ";", "\t", "|"):
            df = _try_read(skip, sep)
            if df is None or df.empty:
                continue
            df = _trim_junk_columns(df)
            if df.shape[1] < 2:
                continue
            cols = _resolve_columns(df)
            if "flight_number" in cols and "dep_time" in cols:
                return df

    # Last resort: flexible read, first non-trivial frame (may still fail validation later).
    for sep in (None, ",", ";", "\t"):
        df = _try_read(0, sep)
        if df is not None and not df.empty and df.shape[1] >= 2:
            return _trim_junk_columns(df)

    raise ValueError(
        "Could not parse CSV: no delimiter/header combination produced usable columns. "
        "Ensure a header row lists flight + departure time, and use comma, semicolon, or tab."
    )


def import_csv_bytes(data: bytes, source_name: str) -> tuple[int, str | None]:
    """
    Replace roster with rows from CSV. Returns (rows_inserted, error_message).
    """
    init_db()
    try:
        df = _read_csv_bytes(data)
    except Exception as e:
        return (
            0,
            f"Could not read CSV: {e} (loader {ROSTER_CSV_LOADER_TAG})",
        )

    if df.empty:
        if "flight_number" in df.columns and "dep_time" in df.columns:
            return (
                0,
                "Crew schedule table was found but no flying sectors were imported "
                "(expect numeric **Duties** + route **AAA - BBB** in **Details** + time in **Report times**).",
            )
        return 0, "CSV has no data rows."

    cols = _resolve_columns(df)
    if "flight_number" not in cols:
        return 0, "Missing a flight column (expected one of: flight_number, flight, flight_no, …)."
    if "dep_time" not in cols:
        return 0, "Missing a departure time column (expected one of: dep_time, departure_time, STD, ETD, …)."

    now_iso = datetime.now().isoformat(timespec="seconds")
    rows: list[tuple] = []

    for idx, r in df.iterrows():
        fn = r.get(cols["flight_number"])
        if fn is None or (isinstance(fn, float) and pd.isna(fn)):
            continue
        flight_number = str(fn).strip()
        if not flight_number:
            continue

        dep_t = _parse_time(r.get(cols["dep_time"]))
        if dep_t is None:
            continue

        dep_d = _parse_date(r[cols["dep_date"]]) if "dep_date" in cols else None
        arr_t = _parse_time(r.get(cols["arr_time"])) if "arr_time" in cols else None
        arr_d = _parse_date(r[cols["arr_date"]]) if "arr_date" in cols else None

        origin = ""
        dest = ""
        if "origin" in cols:
            v = r.get(cols["origin"])
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                origin = str(v).strip()
        if "destination" in cols:
            v = r.get(cols["destination"])
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                dest = str(v).strip()

        block: int | None = None
        if "block_minutes" in cols:
            v = r.get(cols["block_minutes"])
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                try:
                    block = int(float(v))
                except (TypeError, ValueError):
                    block = None

        callsign = ""
        if "callsign" in cols:
            v = r.get(cols["callsign"])
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                callsign = str(v).strip()

        icao24 = ""
        if "icao24" in cols:
            v = r.get(cols["icao24"])
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                icao24 = str(v).strip().lower().replace("-", "")

        rows.append(
            (
                flight_number,
                dep_d.isoformat() if dep_d else None,
                _fmt_time(dep_t),
                arr_d.isoformat() if arr_d else None,
                _fmt_time(arr_t) if arr_t is not None else None,
                origin,
                dest,
                block,
                callsign or None,
                icao24 or None,
                source_name,
                now_iso,
            )
        )

    if not rows:
        return 0, "No valid rows found (need flight + parsable departure time per row)."

    conn = _connect()
    try:
        conn.execute("DELETE FROM roster")
        conn.executemany(
            """
            INSERT INTO roster (
              flight_number, dep_date, dep_time, arr_date, arr_time,
              origin, destination, block_minutes, callsign, icao24,
              source_file, imported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()

    return len(rows), None


@dataclass
class RosterLeg:
    flight_number: str
    dep: datetime
    arr: datetime
    origin: str
    destination: str
    callsign: str = ""
    icao24: str = ""


def _combine_local(d: date, t: time) -> datetime:
    return datetime.combine(d, t)


def _next_occurrence_dep(
    dep_date: date | None, dep_time: time, now: datetime
) -> datetime:
    if dep_date is not None:
        return _combine_local(dep_date, dep_time)
    dt = _combine_local(now.date(), dep_time)
    if dt < now:
        dt += timedelta(days=1)
    return dt


def _default_arr(dep: datetime, arr_date: date | None, arr_time: time | None, block: int | None) -> datetime:
    if arr_time is not None:
        if arr_date is not None:
            arr = _combine_local(arr_date, arr_time)
        else:
            arr = _combine_local(dep.date(), arr_time)
            if arr < dep:
                arr += timedelta(days=1)
        return arr
    minutes = block if block is not None and block > 0 else 90
    return dep + timedelta(minutes=minutes)


def next_roster_leg(now: datetime | None = None) -> RosterLeg | None:
    """Pick the upcoming leg: smallest departure >= now (handles daily undated rows)."""
    if roster_row_count() == 0:
        return None
    now = now or datetime.now()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT flight_number, dep_date, dep_time, arr_date, arr_time,
                   origin, destination, block_minutes, callsign, icao24
            FROM roster
            """
        )
        raw = cur.fetchall()
    finally:
        conn.close()

    best: tuple[datetime, RosterLeg] | None = None

    for (
        flight_number,
        dep_date_s,
        dep_time_s,
        arr_date_s,
        arr_time_s,
        origin,
        destination,
        block_minutes,
        callsign,
        icao24,
    ) in raw:
        dep_d = date.fromisoformat(dep_date_s) if dep_date_s else None
        dep_t = _parse_time_iso(dep_time_s) if dep_time_s else None
        if dep_t is None:
            continue

        arr_d = date.fromisoformat(arr_date_s) if arr_date_s else None
        arr_t = _parse_time_iso(arr_time_s) if arr_time_s else None
        block = int(block_minutes) if block_minutes is not None else None
        cs = str(callsign).strip() if callsign else ""
        hx = str(icao24).strip() if icao24 else ""

        if dep_d is not None:
            dep = _combine_local(dep_d, dep_t)
            arr = _default_arr(dep, arr_d, arr_t, block)
            if dep >= now:
                cand = (
                    dep,
                    RosterLeg(
                        flight_number,
                        dep,
                        arr,
                        origin or "",
                        destination or "",
                        cs,
                        hx,
                    ),
                )
                if best is None or cand[0] < best[0]:
                    best = cand
            continue

        dep = _next_occurrence_dep(None, dep_t, now)
        arr = _default_arr(dep, arr_d, arr_t, block)
        if dep >= now:
            cand = (
                dep,
                RosterLeg(
                    flight_number,
                    dep,
                    arr,
                    origin or "",
                    destination or "",
                    cs,
                    hx,
                ),
            )
            if best is None or cand[0] < best[0]:
                best = cand

    if best is not None:
        return best[1]

    return None


def list_all_roster_legs(now: datetime | None = None) -> list[RosterLeg]:
    """
    All roster rows as legs with computed departure (for pickers / FlightAware date hint).
    Includes dated rows in the past; undated rows use the next calendar occurrence of dep_time.
    """
    now = now or datetime.now()
    if roster_row_count() == 0:
        return []
    conn = _connect()
    try:
        cur = conn.execute(
            """
            SELECT flight_number, dep_date, dep_time, arr_date, arr_time,
                   origin, destination, block_minutes, callsign, icao24
            FROM roster
            """
        )
        raw = cur.fetchall()
    finally:
        conn.close()

    legs: list[RosterLeg] = []
    for (
        flight_number,
        dep_date_s,
        dep_time_s,
        arr_date_s,
        arr_time_s,
        origin,
        destination,
        block_minutes,
        callsign,
        icao24,
    ) in raw:
        dep_d = date.fromisoformat(dep_date_s) if dep_date_s else None
        dep_t = _parse_time_iso(dep_time_s) if dep_time_s else None
        if dep_t is None:
            continue
        arr_d = date.fromisoformat(arr_date_s) if arr_date_s else None
        arr_t = _parse_time_iso(arr_time_s) if arr_time_s else None
        block = int(block_minutes) if block_minutes is not None else None
        cs = str(callsign).strip() if callsign else ""
        hx = str(icao24).strip() if icao24 else ""

        if dep_d is not None:
            dep = _combine_local(dep_d, dep_t)
        else:
            dep = _next_occurrence_dep(None, dep_t, now)
        arr = _default_arr(dep, arr_d, arr_t, block)
        legs.append(
            RosterLeg(
                flight_number,
                dep,
                arr,
                origin or "",
                destination or "",
                cs,
                hx,
            )
        )
    legs.sort(key=lambda x: x.dep)
    return legs
