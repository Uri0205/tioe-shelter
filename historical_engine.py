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

HISTORICAL_ENGINE_VERSION = "0.9.3"
DATASTORE_URL = "https://data.gov.il/api/3/action/datastore_search"
DEFAULT_PAGE_SIZE = 5000
STATION_FILTER_CHUNK_SIZE = 40

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



def _typed_station_filter_value(value: str):
    """Match CKAN field types conservatively for StationId filters."""
    s = _clean_id(value)
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except Exception:
            pass
    return s


def fetch_datastore_resource_for_stations(
    resource_id: str,
    station_ids: Iterable[str],
    *,
    chunk_size: int = STATION_FILTER_CHUNK_SIZE,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_sec: int = 45,
    max_retries: int = 3,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch only requested StationId rows through CKAN datastore_search.

    v0.9.3 compatibility fix: data.gov.il exposes datastore_search but the
    datastore_search_sql action returned HTTP 404 in production. CKAN's normal
    datastore_search supports list-valued filters, which are equivalent to a
    WHERE IN condition. We POST nested JSON filters so the request stays short
    and only rows for the selected city's stations are returned.
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
        "StationId", "StationName", "year_key", "month_key",
        *[f"day_{i}" for i in range(1, 32)],
    ]

    for start in range(0, len(ids), int(chunk_size)):
        chunk = ids[start:start + int(chunk_size)]
        filter_values = [_typed_station_filter_value(v) for v in chunk]
        offset = 0
        while True:
            body = {
                "resource_id": str(resource_id),
                "filters": {"StationId": filter_values},
                "fields": wanted_cols,
                "limit": int(page_size),
                "offset": int(offset),
                "include_total": True,
                "records_format": "objects",
            }
            payload = None
            last_exc = None
            for attempt in range(int(max_retries)):
                try:
                    resp = sess.post(DATASTORE_URL, json=body, timeout=timeout_sec)
                    resp.raise_for_status()
                    payload = resp.json()
                    if not payload.get("success"):
                        raise RuntimeError(f"CKAN datastore_search success=false: {payload}")
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt + 1 < int(max_retries):
                        time.sleep(0.8 * (attempt + 1))
            if payload is None:
                raise RuntimeError(
                    f"Historical API query failed for station chunk "
                    f"{start // int(chunk_size) + 1}: {last_exc}"
                )

            result = payload.get("result", {}) or {}
            batch = result.get("records", []) or []
            if batch:
                frames.append(pd.DataFrame.from_records(batch))
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
            effective_limit = result.get("limit", page_size)
            try:
                effective_limit = int(effective_limit)
            except Exception:
                effective_limit = int(page_size)
            if got < max(1, effective_limit):
                break

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

def normalize_station_datastore_records(raw: pd.DataFrame, expected_year: Optional[int] = None) -> pd.DataFrame:
    """Memory-bounded conversion of station validations to weekday OnDay.

    Source rows are OBSERVED validation counts by station, month, time bucket and
    calendar day (day_1..day_31). Historical OnDay is CALCULATED as:
      1) sum all available time buckets for each station + calendar month/day;
      2) retain Sunday-Thursday valid dates;
      3) average observed daily totals across the resource year.

    v0.9.2 deliberately avoids pandas.melt(day_1..day_31). On a large city that
    temporary long table can be >30x the monthly table and can exceed Streamlit
    Community Cloud memory. The same arithmetic is performed directly on the
    31 wide day columns, keeping peak RAM bounded.
    """
    empty_cols = [
        "StationId", "StationName", "year", "OnDay_historical",
        "observed_weekdays", "months_observed", "demand_provenance",
    ]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=empty_cols)

    required = {"StationId", "StationName", "year_key", "month_key"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Historical station resource missing required columns: {sorted(missing)}")

    day_cols = [c for c in raw.columns if str(c).startswith("day_") and str(c)[4:].isdigit()]
    day_cols = sorted(day_cols, key=lambda c: int(str(c)[4:]))
    if not day_cols:
        raise ValueError("Historical station resource has no day_1..day_31 fields")

    df = raw[["StationId", "StationName", "year_key", "month_key", *day_cols]].copy()
    df["StationId"] = df["StationId"].map(_clean_id)
    df["StationName"] = df["StationName"].fillna("").astype(str).str.strip()
    df["year_key"] = pd.to_numeric(df["year_key"], errors="coerce")
    df["month_key"] = pd.to_numeric(df["month_key"], errors="coerce")
    if expected_year is not None:
        df = df[df["year_key"] == int(expected_year)].copy()
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    for c in day_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Several LowOrPeakDescFull rows may exist for one station/month. Sum every
    # observed time bucket into the station's all-day count for each date.
    agg = (
        df.groupby(["StationId", "year_key", "month_key"], dropna=False)[day_cols]
        .sum(min_count=1)
        .reset_index()
    )

    # Keep a stable display name separately; text must not split a station.
    names = (
        df[df["StationId"] != ""]
        .groupby("StationId", dropna=False)["StationName"]
        .agg(lambda x: next((v for v in x.astype(str) if v.strip()), ""))
        .to_dict()
    )

    accum: dict[tuple[str, int], dict] = {}
    for row in agg.itertuples(index=False):
        sid = _clean_id(getattr(row, "StationId"))
        if not sid:
            continue
        try:
            year = int(getattr(row, "year_key"))
            month = int(getattr(row, "month_key"))
            max_day = calendar.monthrange(year, month)[1]
        except Exception:
            continue
        key = (sid, year)
        rec = accum.setdefault(key, {"sum": 0.0, "count": 0, "months": set()})
        used_month = False
        for col in day_cols:
            day = int(str(col)[4:])
            if day > max_day or not _is_israel_weekday(year, month, day):
                continue
            val = getattr(row, col)
            if pd.isna(val):
                continue
            rec["sum"] += float(val)
            rec["count"] += 1
            used_month = True
        if used_month:
            rec["months"].add(month)

    rows = []
    for (sid, year), rec in accum.items():
        if rec["count"] <= 0:
            continue
        rows.append({
            "StationId": sid,
            "StationName": names.get(sid, ""),
            "year": int(year),
            "OnDay_historical": float(rec["sum"] / rec["count"]),
            "observed_weekdays": int(rec["count"]),
            "months_observed": int(len(rec["months"])),
            "demand_provenance": (
                "CALCULATED:OBSERVED_DATA_GOV_IL_STATION_VALIDATIONS->"
                "SUM_TIME_BUCKETS_PER_DATE->MEAN_SUN_THU"
            ),
        })
    return pd.DataFrame(rows, columns=empty_cols).sort_values(
        ["StationId", "year"]
    ).reset_index(drop=True) if rows else pd.DataFrame(columns=empty_cols)


def _combine_normalized_station_chunks(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine chunk results without averaging averages incorrectly.

    UI chunks partition unique StationIds, so duplicate station-years should not
    normally occur. A defensive weighted combine is retained for correctness.
    """
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=[
            "StationId", "StationName", "year", "OnDay_historical",
            "observed_weekdays", "months_observed", "demand_provenance",
        ])
    out = pd.concat(frames, ignore_index=True)
    if not out.duplicated(["StationId", "year"]).any():
        return out.sort_values(["StationId", "year"]).reset_index(drop=True)

    out["_weighted_sum"] = (
        pd.to_numeric(out["OnDay_historical"], errors="coerce")
        * pd.to_numeric(out["observed_weekdays"], errors="coerce")
    )
    rows = []
    for (sid, year), g in out.groupby(["StationId", "year"], dropna=False):
        count = int(pd.to_numeric(g["observed_weekdays"], errors="coerce").fillna(0).sum())
        weighted = float(pd.to_numeric(g["_weighted_sum"], errors="coerce").fillna(0).sum())
        rows.append({
            "StationId": sid,
            "StationName": next((str(v) for v in g["StationName"] if str(v).strip()), ""),
            "year": int(year),
            "OnDay_historical": weighted / count if count else np.nan,
            "observed_weekdays": count,
            "months_observed": int(pd.to_numeric(g["months_observed"], errors="coerce").fillna(0).max()),
            "demand_provenance": g["demand_provenance"].iloc[0],
        })
    return pd.DataFrame(rows).sort_values(["StationId", "year"]).reset_index(drop=True)

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
    raw_row_count = 0
    if station_ids is not None:
        # v0.9.2: process one bounded StationId group at a time and discard raw
        # rows immediately after normalization. This avoids holding the city's
        # complete raw year plus a 31x temporary day table in Streamlit RAM.
        ids = []
        seen = set()
        for value in station_ids:
            sid = _clean_id(value)
            if sid and sid not in seen:
                seen.add(sid)
                ids.append(sid)
        normalized_parts = []
        for start in range(0, len(ids), STATION_FILTER_CHUNK_SIZE):
            part_ids = ids[start:start + STATION_FILTER_CHUNK_SIZE]
            raw_part = fetch_datastore_resource_for_stations(
                rid, part_ids, chunk_size=max(1, len(part_ids)), session=session
            )
            raw_row_count += int(len(raw_part))
            normalized_parts.append(
                normalize_station_datastore_records(raw_part, expected_year=year)
            )
            del raw_part
        normalized = _combine_normalized_station_chunks(normalized_parts)
    else:
        # Backward-compatible non-UI path. The Streamlit UI always supplies city
        # station IDs and therefore never performs a national full-table fetch.
        raw = fetch_datastore_resource(rid, session=session)
        raw_row_count = int(len(raw))
        normalized = normalize_station_datastore_records(raw, expected_year=year)
    return HistoricalDemandResult(
        year=year,
        resource_id=rid,
        station_demand=normalized,
        raw_row_count=int(raw_row_count),
        fetched_at_utc=pd.Timestamp.utcnow().isoformat(),
        provenance="CALCULATED_FROM_OBSERVED_DATA_GOV_IL",
        status="AVAILABLE" if not normalized.empty else "UNAVAILABLE",
        message="",
    )
