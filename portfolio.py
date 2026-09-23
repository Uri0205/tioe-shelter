from __future__ import annotations

import os
import glob
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from shelter_engine import (
    DiscoveryConfig,
    find_stations_file,
    load_station_profiles,
    classify_candidates,
    build_candidate_opportunities,
    build_allocation_plan,
    prepare_snapshot_history,
)


@dataclass
class PortfolioResult:
    source_path: str
    stations: pd.DataFrame
    candidates: pd.DataFrame
    allocations: pd.DataFrame
    changes: pd.DataFrame
    history: dict


def run_portfolio(stations_path: Optional[str] = None, save_history: bool = True) -> PortfolioResult:
    source_path = find_stations_file(stations_path)
    base = load_station_profiles(source_path)
    cfg = DiscoveryConfig()
    classified = classify_candidates(base, cfg)
    candidates = build_candidate_opportunities(classified, cfg)
    allocations = build_allocation_plan(classified, cfg)
    history = prepare_snapshot_history(
        source_path,
        classified,
        candidates,
        allocations,
        save_history=save_history,
    )
    changes = history.get("changes", pd.DataFrame())
    return PortfolioResult(
        source_path=source_path,
        stations=classified,
        candidates=candidates,
        allocations=allocations,
        changes=changes,
        history=history,
    )


def _history_dir(source_path: str) -> str:
    return os.path.join(os.path.dirname(source_path), "Shelter_History")


def _snapshot_paths(result: PortfolioResult) -> list[str]:
    return sorted(glob.glob(os.path.join(_history_dir(result.source_path), "station_profiles_*.csv")))


def _read_city_snapshot(path: str, city: str) -> pd.DataFrame:
    try:
        snap = pd.read_csv(path, low_memory=False, dtype={"station_key": str})
    except Exception:
        return pd.DataFrame()
    if "city_name" not in snap.columns:
        return pd.DataFrame()
    return snap[snap["city_name"].astype(str) == str(city)].copy()


def source_data_coverage(result: PortfolioResult, city: str) -> dict:
    """Measure source coverage without treating unknown infrastructure as unsheltered.

    Denominator: active regular stops in Stations.xlsx for the selected city.
    Infrastructure-known = shed_structure in {1,2}.
    Demand-known = OnDay available among infrastructure-known stops.
    """
    usecols = ["CityName", "StationTypeId", "StationStatusId", "shed_structure", "OnDay"]
    try:
        raw = pd.read_excel(result.source_path, usecols=usecols)
    except Exception:
        return {
            "active_regular_stops": np.nan,
            "infra_known_stops": np.nan,
            "infra_unknown_stops": np.nan,
            "infra_coverage": np.nan,
            "demand_known_stops": np.nan,
            "demand_coverage": np.nan,
        }

    raw = raw[
        (pd.to_numeric(raw["StationTypeId"], errors="coerce") == 1)
        & (pd.to_numeric(raw["StationStatusId"], errors="coerce") == 1)
        & (raw["CityName"].astype(str).str.strip() == str(city).strip())
    ].copy()
    shed = pd.to_numeric(raw["shed_structure"], errors="coerce")
    infra_known_mask = shed.isin([1, 2])
    active = int(len(raw))
    infra_known = int(infra_known_mask.sum())
    infra_unknown = active - infra_known
    demand_known = int(pd.to_numeric(raw.loc[infra_known_mask, "OnDay"], errors="coerce").notna().sum())
    return {
        "active_regular_stops": active,
        "infra_known_stops": infra_known,
        "infra_unknown_stops": infra_unknown,
        "infra_coverage": infra_known / active if active else np.nan,
        "demand_known_stops": demand_known,
        "demand_coverage": demand_known / infra_known if infra_known else np.nan,
    }


def city_impact_attribution(result: PortfolioResult, city: str) -> dict:
    """Compare the latest two saved station snapshots.

    The result deliberately avoids claiming causality for demand/service changes.
    Infrastructure effect is CALCULATED from reported pole/shelter transitions,
    using current OnDay at stations whose infrastructure classification changed.
    The residual is reported as unseparated demand/service/source-universe change.
    """
    paths = _snapshot_paths(result)
    if len(paths) < 2:
        return {
            "available": False,
            "previous_unsheltered": np.nan,
            "current_unsheltered": np.nan,
            "total_improvement": np.nan,
            "infra_coverage_gain": np.nan,
            "residual_change": np.nan,
            "pole_to_shelter_count": 0,
            "shelter_to_pole_count": 0,
        }

    prev = _read_city_snapshot(paths[-2], city)
    curr = _read_city_snapshot(paths[-1], city)
    if prev.empty or curr.empty or "station_key" not in prev.columns or "station_key" not in curr.columns:
        return {"available": False}

    for df in (prev, curr):
        df["station_key"] = df["station_key"].astype(str)
        df["OnDay"] = pd.to_numeric(df["OnDay"], errors="coerce")
        df["shed_structure"] = pd.to_numeric(df["shed_structure"], errors="coerce")

    prev_uns = float(prev.loc[prev["shed_structure"] == 1, "OnDay"].fillna(0).sum())
    curr_uns = float(curr.loc[curr["shed_structure"] == 1, "OnDay"].fillna(0).sum())
    total_improvement = prev_uns - curr_uns  # positive = fewer boardings at unsheltered stops

    merged = prev[["station_key", "shed_structure", "OnDay"]].merge(
        curr[["station_key", "shed_structure", "OnDay"]],
        on="station_key",
        how="inner",
        suffixes=("_prev", "_curr"),
    )
    p2s = (merged["shed_structure_prev"] == 1) & (merged["shed_structure_curr"] == 2)
    s2p = (merged["shed_structure_prev"] == 2) & (merged["shed_structure_curr"] == 1)

    # Positive means net increase in boardings covered by shelters due to reported
    # infrastructure classification changes, evaluated at current demand.
    infra_gain = float(merged.loc[p2s, "OnDay_curr"].fillna(0).sum()) - float(
        merged.loc[s2p, "OnDay_curr"].fillna(0).sum()
    )
    residual = total_improvement - infra_gain

    return {
        "available": True,
        "previous_unsheltered": prev_uns,
        "current_unsheltered": curr_uns,
        "total_improvement": total_improvement,
        "infra_coverage_gain": infra_gain,
        "residual_change": residual,
        "pole_to_shelter_count": int(p2s.sum()),
        "shelter_to_pole_count": int(s2p.sum()),
    }


def city_metrics(result: PortfolioResult, city: str) -> dict:
    st = result.stations[result.stations["city_name"] == city].copy()
    al = result.allocations[result.allocations["city_name"] == city].copy()
    ch = result.changes[result.changes["city_name"] == city].copy() if not result.changes.empty else pd.DataFrame()

    total_demand = float(st["OnDay"].fillna(0).sum())
    unsheltered = float(st.loc[st["shed_structure"] == 1, "OnDay"].fillna(0).sum())
    sheltered = float(st.loc[st["shed_structure"] == 2, "OnDay"].fillna(0).sum())
    known_demand = sheltered + unsheltered
    sheltered_share = sheltered / known_demand if known_demand > 0 else np.nan
    unsheltered_share = unsheltered / known_demand if known_demand > 0 else np.nan

    change_counts = ch["change_status"].value_counts().to_dict() if not ch.empty else {}
    coverage = source_data_coverage(result, city)
    impact = city_impact_attribution(result, city)

    return {
        "station_count": int(len(st)),
        "shelters": int((st["shed_structure"] == 2).sum()),
        "poles": int((st["shed_structure"] == 1).sum()),
        "unsheltered_boardings": unsheltered,
        "sheltered_boardings": sheltered,
        "total_boardings_known_infra": known_demand,
        "sheltered_share": sheltered_share,
        "unsheltered_share": unsheltered_share,
        "active_opportunities": int(len(al)),
        "net_reallocation_gain": float(al["relocation_gain"].clip(lower=0).sum()) if not al.empty else 0.0,
        "new_opportunities": int(change_counts.get("NEW", 0)),
        "persisting_opportunities": int(change_counts.get("PERSISTING", 0)),
        "resolved_infrastructure": int(change_counts.get("RESOLVED_INFRASTRUCTURE_CHANGED", 0)),
        "dropped_or_changed": int(
            change_counts.get("NO_LONGER_PRIORITY_CANDIDATE", 0)
            + change_counts.get("DROPPED_FROM_CURRENT_SET", 0)
        ),
        "coverage": coverage,
        "impact": impact,
    }


def city_history(result: PortfolioResult, city: str) -> pd.DataFrame:
    """Build historical city KPI series from saved station profile snapshots.

    No interpolation or synthetic history: each row comes from a saved snapshot.
    """
    rows = []
    for path in _snapshot_paths(result):
        snap_city = _read_city_snapshot(path, city)
        if snap_city.empty:
            continue
        if "OnDay" not in snap_city or "shed_structure" not in snap_city:
            continue
        onday = pd.to_numeric(snap_city["OnDay"], errors="coerce")
        shed = pd.to_numeric(snap_city["shed_structure"], errors="coerce")
        unsheltered = float(onday[shed == 1].fillna(0).sum())
        sheltered = float(onday[shed == 2].fillna(0).sum())
        ts = os.path.basename(path).replace("station_profiles_", "").replace(".csv", "")
        try:
            dt = pd.to_datetime(ts, format="%Y%m%dT%H%M%SZ", utc=True)
        except Exception:
            dt = pd.NaT
        rows.append({
            "snapshot_time": dt,
            "unsheltered_boardings": unsheltered,
            "sheltered_boardings": sheltered,
            "shelters": int((shed == 2).sum()),
            "poles": int((shed == 1).sum()),
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("snapshot_time").drop_duplicates("snapshot_time", keep="last")
    return out


def _opportunity_id(city: str, recipient_station_key: str) -> str:
    """Stable ID for the recipient-side infrastructure opportunity.

    The donor may change between runs; the underlying opportunity is the same
    unsheltered recipient asset, so the ID is intentionally recipient-based.
    """
    raw = f"{city}|{recipient_station_key}".encode("utf-8")
    return "SH-" + hashlib.sha1(raw).hexdigest()[:10].upper()


def _state_dir(result: PortfolioResult) -> str:
    path = os.path.join(os.path.dirname(result.source_path), "TIOE_State")
    os.makedirs(path, exist_ok=True)
    return path


def _registry_path(result: PortfolioResult) -> str:
    return os.path.join(_state_dir(result), "opportunity_registry.csv")


def load_opportunity_registry(result: PortfolioResult) -> pd.DataFrame:
    path = _registry_path(result)
    cols = [
        "opportunity_id", "city_name", "recipient_station_key",
        "user_status", "review_note", "updated_at"
    ]
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(path, dtype={"recipient_station_key": str})
    except Exception:
        return pd.DataFrame(columns=cols)
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    return df[cols].copy()


def save_opportunity_state(
    result: PortfolioResult,
    opportunity_id: str,
    city_name: str,
    recipient_station_key: str,
    user_status: str,
    review_note: str = "",
) -> str:
    """Persist one user workflow state in a lightweight CSV registry.

    This is intentionally separate from system analytical status.
    """
    path = _registry_path(result)
    reg = load_opportunity_registry(result)
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "opportunity_id": str(opportunity_id),
        "city_name": str(city_name),
        "recipient_station_key": str(recipient_station_key),
        "user_status": str(user_status),
        "review_note": str(review_note or ""),
        "updated_at": now,
    }
    if not reg.empty and (reg["opportunity_id"].astype(str) == str(opportunity_id)).any():
        idx = reg.index[reg["opportunity_id"].astype(str) == str(opportunity_id)][0]
        for k, v in row.items():
            reg.loc[idx, k] = v
    else:
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    reg.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_current_snapshot(result: PortfolioResult) -> dict:
    """Save a snapshot explicitly; unchanged source files are not duplicated."""
    return prepare_snapshot_history(
        result.source_path,
        result.stations,
        result.candidates,
        result.allocations,
        save_history=True,
    )


def city_opportunities(result: PortfolioResult, city: str) -> pd.DataFrame:
    al = result.allocations[result.allocations["city_name"] == city].copy()
    if al.empty:
        return al
    al = al.sort_values(["recipient_demand", "relocation_gain"], ascending=[False, False]).reset_index(drop=True)
    al["status_he"] = np.where(al["same_complex_guard"] == "REVIEW", "נדרשת בדיקה", "ללא דגל חריג")
    al["opportunity_id"] = [
        _opportunity_id(str(r["city_name"]), str(r["recipient_station_key"]))
        for _, r in al.iterrows()
    ]

    # System status comes from the analytical comparison and remains separate
    # from the human workflow status.
    al["system_status"] = "CURRENT"
    al["gain_delta"] = np.nan
    if result.changes is not None and not result.changes.empty:
        ch = result.changes[result.changes["city_name"].astype(str) == str(city)].copy()
        if not ch.empty:
            ch["recipient_station_key"] = ch["recipient_station_key"].astype(str)
            ch_by = ch.drop_duplicates("recipient_station_key", keep="last").set_index("recipient_station_key")
            for idx, row in al.iterrows():
                key = str(row["recipient_station_key"])
                if key in ch_by.index:
                    c = ch_by.loc[key]
                    al.loc[idx, "system_status"] = str(c.get("change_status", "CURRENT"))
                    al.loc[idx, "gain_delta"] = pd.to_numeric(c.get("gain_delta"), errors="coerce")

    reg = load_opportunity_registry(result)
    if not reg.empty:
        reg = reg.drop_duplicates("opportunity_id", keep="last").set_index("opportunity_id")
    user_statuses, notes, updated = [], [], []
    for oid in al["opportunity_id"].astype(str):
        if not reg.empty and oid in reg.index:
            r = reg.loc[oid]
            user_statuses.append(str(r.get("user_status") or "טרם נבדקה"))
            notes.append(str(r.get("review_note") or ""))
            updated.append(str(r.get("updated_at") or ""))
        else:
            user_statuses.append("טרם נבדקה")
            notes.append("")
            updated.append("")
    al["user_status"] = user_statuses
    al["review_note"] = notes
    al["user_status_updated_at"] = updated
    return al

def map_points(result: PortfolioResult, city: str) -> pd.DataFrame:
    st = result.stations[result.stations["city_name"] == city].copy()
    al = city_opportunities(result, city)
    donor_ids = set(al["donor_station_key"].astype(str)) if not al.empty else set()
    recipient_ids = set(al["recipient_station_key"].astype(str)) if not al.empty else set()

    st["point_role"] = "OTHER"
    st.loc[st["station_key"].astype(str).isin(donor_ids), "point_role"] = "DONOR"
    st.loc[st["station_key"].astype(str).isin(recipient_ids), "point_role"] = "RECIPIENT"

    # Visual size only; not used by the analytical decision engine.
    demand = pd.to_numeric(st["OnDay"], errors="coerce").fillna(0).clip(lower=0)
    st["radius"] = np.where(
        st["point_role"] == "RECIPIENT",
        35 + np.sqrt(demand) * 7,
        np.where(st["point_role"] == "DONOR", 75, 25),
    )
    return st
