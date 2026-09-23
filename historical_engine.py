from __future__ import annotations

import calendar
import math
import os
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd
import requests

HISTORICAL_ENGINE_VERSION = "0.9.1"
DATASTORE_URL = "https://data.gov.il/api/3/action/datastore_search"
DATASTORE_SQL_URL = "https://data.gov.il/api/3/action/datastore_search_sql"
DEFAULT_PAGE_SIZE = 5000
SQL_STATION_CHUNK_SIZE = 80

# Official station-validation resources supplied for TIOE Shelter historical demand.
HISTORICAL_RESOURCES = {
    2025: "b2c6b258-4638-4f8e-bcad-600f0cdfb449",
    2024: "51703b73-c27b-497e-8701-ea979a0c3835",
}


@dataclass
class HistoricalDemandResult:
    year: int
    resource_id: str
    station_demand: pd.DataFrame
    raw_row_count: int
    fetched_at_utc: str
    provenance: str
    status: str
    message: str = ""


def _clean_id(value) -> str:
    if pd.isna(value):
        return ""
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _is_israel_weekday(year: int, month: int, day: int) -> bool:
    """Sunday-Thursday. Python weekday: Monday=0 ... Sunday=6."""
    try:
        wd = pd.Timestamp(year=int(year), month=int(month), day=int(day)).weekday()
    except Exception:
        return False
    return wd in {0, 1, 2, 3, 6}


def fetch_datastore_resource(
    resource_id: str,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_sec: int = 45,
    max_retries: int = 3,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch one CKAN DataStore resource using bounded pagination.

    The API may cap a requested page size. Pagination therefore advances by the
    number of records actually returned, not by the requested size.
    """
    sess = session or requests.Session()
    offset = 0
    records: list[dict] = []
    fields = None

    while True:
        params = {
            "resource_id": str(resource_id),
            "limit": int(page_size),
            "offset": int(offset),
            "include_total": "true",
        }
        last_exc = None
        payload = None
        for attempt in range(max_retries):
            try:
                resp = sess.get(DATASTORE_URL, params=params, timeout=timeout_sec)
                resp.raise_for_status()
                payload = resp.json()
                if not payload.get("success"):
                    raise RuntimeError(f"CKAN success=false: {payload}")
                break
            except Exception as exc:  # network/API boundary
                last_exc = exc
                if attempt + 1 < max_retries:
                    time.sleep(0.8 * (attempt + 1))
        if payload is None:
            raise RuntimeError(
                f"Failed to fetch resource {resource_id} at offset {offset}: {last_exc}"
            )

        result = payload.get("result", {}) or {}
        batch = result.get("records", []) or []
        if fields is None:
            fields = result.get("fields")
        records.extend(batch)

        got = len(batch)
        if got == 0:
            break
        offset += got

        total = result.get("total")
        if total is not None:
            try:
                if offset >= int(total):
                    break
            except Exception:
                pass
        # When total is omitted/estimated, a short page is the termination signal.
        effective_limit = result.get("limit", page_size)
        try:
            effective_limit = int(effective_limit)
        except Exception:
            effective_limit = int(page_size)
        if got < max(1, effective_limit):
            break

    return pd.DataFrame.from_records(records)



def _sql_literal(value: str) -> str:
    """Return a conservative SQL literal for CKAN datastore_search_sql."""
    s = _clean_id(value)
    if not s:
        return "NULL"
    if s.lstrip("-").isdigit():
        return s
    return "'" + s.replace("'", "''") + "'"


def fetch_datastore_resource_for_stations(
    resource_id: str,
    station_ids: Iterable[str],
    *,
    chunk_size: int = SQL_STATION_CHUNK_SIZE,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_sec: int = 45,
    max_retries: int = 3,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch only requested StationId rows through CKAN SQL.

    v0.9.1 memory-safety rule: the Streamlit app must never download the full
    national historical resource merely to analyze one city. Station IDs are
    taken from the CURRENT city's audited station crosswalk and queried in
    bounded groups. This keeps both network traffic and peak RAM bounded.
    """
    ids = []
    seen = set()
    for value in station_ids or []:
        sid = _clean_id(value)
        if sid and sid not in seen:
            seen.add(sid)
            ids.append(sid)
    if not ids:
        return pd.DataFrame()

    sess = session or requests.Session()
    frames: list[pd.DataFrame] = []
    wanted_cols = [
        '"StationId"', '"StationName"', '"year_key"', '"month_key"',
        *[f'"day_{i}"' for i in range(1, 32)],
    ]
    select_cols = ",".join(wanted_cols)
    table = '"' + str(resource_id).replace('"', '""') + '"'

    for start in range(0, len(ids), int(chunk_size)):
        chunk = ids[start:start + int(chunk_size)]
        literals = ",".join(_sql_literal(v) for v in chunk)
        offset = 0
        while True:
            sql = (
                f"SELECT {select_cols} FROM {table} "
                f'WHERE "StationId" IN ({literals}) '
                f"LIMIT {int(page_size)} OFFSET {int(offset)}"
            )
            payload = None
            last_exc = None
            for attempt in range(int(max_retries)):
                try:
                    resp = sess.get(DATASTORE_SQL_URL, params={"sql": sql}, timeout=timeout_sec)
                    resp.raise_for_status()
                    payload = resp.json()
                    if not payload.get("success"):
                        raise RuntimeError(f"CKAN SQL success=false: {payload}")
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt + 1 < int(max_retries):
                        time.sleep(0.8 * (attempt + 1))
            if payload is None:
                raise RuntimeError(
                    f"Historical API query failed for station chunk {start // int(chunk_size) + 1}: {last_exc}"
                )

            batch = (payload.get("result", {}) or {}).get("records", []) or []
            if batch:
                frames.append(pd.DataFrame.from_records(batch))
            got = len(batch)
            if got < int(page_size):
                break
            offset += got

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

def normalize_station_datastore_records(raw: pd.DataFrame, expected_year: Optional[int] = None) -> pd.DataFrame:
    """Convert station/month/time-bucket validations to average weekday OnDay.

    Source rows are OBSERVED validation counts by station, month, time bucket and
    calendar day (day_1..day_31). Historical OnDay is CALCULATED as:
      1) sum all available time buckets for each station + calendar date;
      2) retain Sunday-Thursday valid dates;
      3) average those daily totals across the resource year.

    No missing daily value is converted to zero. A calendar date contributes only
    when at least one time-bucket record contains an observed value for that day.
    """
    if raw is None or raw.empty:
        return pd.DataFrame(columns=[
            "StationId", "StationName", "year", "OnDay_historical",
            "observed_weekdays", "months_observed", "demand_provenance",
        ])

    required = {"StationId", "StationName", "year_key", "month_key"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Historical station resource missing required columns: {sorted(missing)}")

    day_cols = [c for c in raw.columns if str(c).startswith("day_") and str(c)[4:].isdigit()]
    if not day_cols:
        raise ValueError("Historical station resource has no day_1..day_31 fields")

    df = raw.copy()
    df["StationId"] = df["StationId"].map(_clean_id)
    df["StationName"] = df["StationName"].fillna("").astype(str).str.strip()
    df["year_key"] = pd.to_numeric(df["year_key"], errors="coerce")
    df["month_key"] = pd.to_numeric(df["month_key"], errors="coerce")
    if expected_year is not None:
        df = df[df["year_key"] == int(expected_year)].copy()
    if df.empty:
        return pd.DataFrame(columns=[
            "StationId", "StationName", "year", "OnDay_historical",
            "observed_weekdays", "months_observed", "demand_provenance",
        ])

    for c in day_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # One station-month can contain several LowOrPeakDescFull rows. Sum them into
    # the station's all-day validations for each numbered calendar day.
    agg = (
        df.groupby(["StationId", "StationName", "year_key", "month_key"], dropna=False)[day_cols]
        .sum(min_count=1)
        .reset_index()
    )

    long = agg.melt(
        id_vars=["StationId", "StationName", "year_key", "month_key"],
        value_vars=day_cols,
        var_name="day_col",
        value_name="daily_validations",
    )
    long["day"] = pd.to_numeric(long["day_col"].str.replace("day_", "", regex=False), errors="coerce")
    long = long.dropna(subset=["year_key", "month_key", "day", "daily_validations"]).copy()
    long["year_key"] = long["year_key"].astype(int)
    long["month_key"] = long["month_key"].astype(int)
    long["day"] = long["day"].astype(int)

    # Remove impossible dates and keep Sunday-Thursday only.
    valid_date = []
    weekday = []
    for y, m, d in zip(long["year_key"], long["month_key"], long["day"]):
        try:
            max_day = calendar.monthrange(int(y), int(m))[1]
            ok = 1 <= int(d) <= max_day
        except Exception:
            ok = False
        valid_date.append(ok)
        weekday.append(ok and _is_israel_weekday(int(y), int(m), int(d)))
    long = long[np.asarray(valid_date, dtype=bool) & np.asarray(weekday, dtype=bool)].copy()

    if long.empty:
        return pd.DataFrame(columns=[
            "StationId", "StationName", "year", "OnDay_historical",
            "observed_weekdays", "months_observed", "demand_provenance",
        ])

    # If the same StationId is returned with multiple names, preserve a readable
    # representative name without letting the text split one physical station.
    def _name_mode(s: pd.Series) -> str:
        vals = s.fillna("").astype(str).str.strip()
        vals = vals[vals != ""]
        if vals.empty:
            return ""
        mode = vals.mode()
        return str(mode.iloc[0] if not mode.empty else vals.iloc[0])

    grouped = long.groupby(["StationId", "year_key"], dropna=False)
    out = grouped.agg(
        OnDay_historical=("daily_validations", "mean"),
        observed_weekdays=("daily_validations", "count"),
        months_observed=("month_key", "nunique"),
        StationName=("StationName", _name_mode),
    ).reset_index()
    out = out.rename(columns={"year_key": "year"})
    out["OnDay_historical"] = pd.to_numeric(out["OnDay_historical"], errors="coerce")
    out["demand_provenance"] = (
        "CALCULATED:OBSERVED_DATA_GOV_IL_STATION_VALIDATIONS->"
        "SUM_TIME_BUCKETS_PER_DATE->MEAN_SUN_THU"
    )
    return out[[
        "StationId", "StationName", "year", "OnDay_historical",
        "observed_weekdays", "months_observed", "demand_provenance",
    ]].sort_values(["StationId", "year"]).reset_index(drop=True)


def load_historical_station_demand(
    year: int,
    *,
    resource_id: Optional[str] = None,
    station_ids: Optional[Iterable[str]] = None,
    session: Optional[requests.Session] = None,
) -> HistoricalDemandResult:
    year = int(year)
    rid = str(resource_id or HISTORICAL_RESOURCES.get(year, ""))
    if not rid:
        return HistoricalDemandResult(
            year=year, resource_id="", station_demand=pd.DataFrame(), raw_row_count=0,
            fetched_at_utc=pd.Timestamp.utcnow().isoformat(), provenance="UNAVAILABLE",
            status="UNAVAILABLE", message=f"No resource configured for {year}",
        )
    if station_ids is not None:
        raw = fetch_datastore_resource_for_stations(rid, station_ids, session=session)
    else:
        # Backward-compatible programmatic path. UI v0.9.1 always supplies city
        # station IDs and therefore never performs a national full-table fetch.
        raw = fetch_datastore_resource(rid, session=session)
    normalized = normalize_station_datastore_records(raw, expected_year=year)
    return HistoricalDemandResult(
        year=year,
        resource_id=rid,
        station_demand=normalized,
        raw_row_count=int(len(raw)),
        fetched_at_utc=pd.Timestamp.utcnow().isoformat(),
        provenance="CALCULATED_FROM_OBSERVED_DATA_GOV_IL",
        status="AVAILABLE" if not normalized.empty else "UNAVAILABLE",
        message="",
    )
