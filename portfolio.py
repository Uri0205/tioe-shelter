from __future__ import annotations

import os
import glob
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

PORTFOLIO_VERSION = "0.9"


from historical_engine import (
    HISTORICAL_RESOURCES,
    HistoricalDemandResult,
    load_historical_station_demand,
)
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

    # v0.8 Opportunity Inspector semantics. These fields make the distinction
    # between analytical evidence and field feasibility explicit without changing
    # discovery, matching, ranking, or one-to-one allocation logic.
    al["physical_feasibility"] = "UNAVAILABLE"
    al["decision_tier"] = "REVIEW"
    al["recipient_demand_provenance"] = "OBSERVED:Stations.xlsx:OnDay"
    al["donor_demand_provenance"] = "OBSERVED:Stations.xlsx:OnDay"
    al["gain_provenance"] = "CALCULATED:recipient_OnDay-donor_OnDay"
    al["distance_provenance"] = "CALCULATED:coordinates;INFORMATION_ONLY"
    al["allocation_provenance"] = "CALCULATED:one-to-one allocation"
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


# -----------------------------------------------------------------------------
# v0.9 Historical Demand Replay
# -----------------------------------------------------------------------------
def _replay_stations_with_historical_demand(
    result: PortfolioResult,
    historical: HistoricalDemandResult,
) -> tuple[pd.DataFrame, dict]:
    """Apply historical station demand to the CURRENT infrastructure inventory.

    This is deliberately a historical-demand replay, not a claim about historical
    shelter inventory. Infrastructure type, coordinates and city are taken from
    the current Stations.xlsx. Historical demand is joined using the audited
    station_demand_id -> data.gov.il StationId mapping.
    """
    current = result.stations.copy()
    if historical is None or historical.station_demand is None or historical.station_demand.empty:
        return pd.DataFrame(), {
            "status": "UNAVAILABLE",
            "matched_current_stops": 0,
            "current_stops": int(len(current)),
            "historical_stations": 0,
            "match_rate_current": np.nan,
        }

    hist = historical.station_demand.copy()
    hist["StationId"] = hist["StationId"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    hist["OnDay_historical"] = pd.to_numeric(hist["OnDay_historical"], errors="coerce")
    hist = hist.dropna(subset=["OnDay_historical"]).drop_duplicates("StationId", keep="last")

    current["station_demand_id"] = current["station_demand_id"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    replay = current.merge(
        hist[["StationId", "OnDay_historical", "observed_weekdays", "months_observed", "demand_provenance"]],
        left_on="station_demand_id",
        right_on="StationId",
        how="left",
    )
    matched = replay["OnDay_historical"].notna()
    replay = replay[matched].copy()
    if replay.empty:
        return replay, {
            "status": "NO_MATCHES",
            "matched_current_stops": 0,
            "current_stops": int(len(current)),
            "historical_stations": int(len(hist)),
            "match_rate_current": 0.0 if len(current) else np.nan,
        }

    replay["OnDay_current_source"] = replay["OnDay"]
    replay["OnDay"] = replay["OnDay_historical"]
    replay["demand_provenance"] = (
        "CALCULATED:OBSERVED_DATA_GOV_IL_STATION_VALIDATIONS->HISTORICAL_ONDAY"
    )
    replay["analysis_status"] = np.where(
        replay["shed_structure"] == 2, "SHELTER_BASELINE", "POLE_UNASSESSED"
    )
    replay["demand_percentile_city"] = (
        replay.groupby("city_name", group_keys=False)["OnDay"]
        .rank(pct=True, method="average")
    )

    return replay.reset_index(drop=True), {
        "status": "AVAILABLE",
        "matched_current_stops": int(matched.sum()),
        "current_stops": int(len(current)),
        "historical_stations": int(len(hist)),
        "match_rate_current": float(matched.mean()) if len(current) else np.nan,
    }


def historical_city_replay(
    result: PortfolioResult,
    city: str,
    year: int,
    historical: Optional[HistoricalDemandResult] = None,
) -> dict:
    """Re-run shelter discovery using historical demand + current infrastructure.

    Provenance contract:
      historical demand cells: OBSERVED at source;
      Historical OnDay: CALCULATED;
      replay opportunity/KPI: SIMULATED (historical demand on current inventory).
    """
    year = int(year)
    hist = historical or load_historical_station_demand(year)
    replay_base, coverage = _replay_stations_with_historical_demand(result, hist)
    if replay_base.empty:
        return {
            "status": "UNAVAILABLE",
            "year": year,
            "resource_id": getattr(hist, "resource_id", ""),
            "coverage": coverage,
            "stations": pd.DataFrame(),
            "allocations": pd.DataFrame(),
            "metrics": {},
            "provenance": "UNAVAILABLE",
            "message": getattr(hist, "message", "Historical demand unavailable"),
        }

    cfg = DiscoveryConfig()
    classified = classify_candidates(replay_base, cfg)
    allocations = build_allocation_plan(classified, cfg)
    city_st = classified[classified["city_name"].astype(str) == str(city)].copy()
    city_al = allocations[allocations["city_name"].astype(str) == str(city)].copy() if not allocations.empty else pd.DataFrame()

    known = city_st["shed_structure"].isin([1, 2])
    demand = pd.to_numeric(city_st["OnDay"], errors="coerce")
    uns = float(demand[(city_st["shed_structure"] == 1) & known].fillna(0).sum())
    shel = float(demand[(city_st["shed_structure"] == 2) & known].fillna(0).sum())
    total = uns + shel
    gain = float(pd.to_numeric(city_al.get("relocation_gain", pd.Series(dtype=float)), errors="coerce").clip(lower=0).sum()) if not city_al.empty else 0.0

    if not city_al.empty:
        city_al = city_al.copy()
        city_al["opportunity_id"] = [
            _opportunity_id(str(r["city_name"]), str(r["recipient_station_key"]))
            for _, r in city_al.iterrows()
        ]
        city_al["replay_provenance"] = "SIMULATED:HISTORICAL_DEMAND_ON_CURRENT_INFRASTRUCTURE"

    return {
        "status": "AVAILABLE",
        "year": year,
        "resource_id": hist.resource_id,
        "coverage": coverage,
        "stations": city_st,
        "allocations": city_al,
        "metrics": {
            "matched_stops_city": int(len(city_st)),
            "unsheltered_boardings_replay": uns,
            "sheltered_boardings_replay": shel,
            "unsheltered_share_replay": (uns / total) if total > 0 else np.nan,
            "active_opportunities_replay": int(len(city_al)),
            "net_reallocation_gain_replay": gain,
        },
        "provenance": "SIMULATED:HISTORICAL_DEMAND_ON_CURRENT_INFRASTRUCTURE",
        "historical_demand_provenance": hist.provenance,
        "raw_row_count": hist.raw_row_count,
        "fetched_at_utc": hist.fetched_at_utc,
        "message": "",
    }


def compare_historical_replay_to_current(
    result: PortfolioResult,
    city: str,
    replay: dict,
    stable_abs_gain: float = 25.0,
    stable_rel_gain: float = 0.05,
) -> pd.DataFrame:
    """Compare a historical-demand replay to current one-to-one opportunities.

    This classification is CALCULATED from two opportunity sets. It does not
    infer causality and does not claim the historical infrastructure was equal to
    today's inventory.
    """
    current = city_opportunities(result, city).copy()
    hist = replay.get("allocations", pd.DataFrame()).copy() if replay else pd.DataFrame()

    cur_by = {}
    if not current.empty:
        for _, r in current.iterrows():
            cur_by[str(r["recipient_station_key"])] = r
    hist_by = {}
    if not hist.empty:
        for _, r in hist.iterrows():
            hist_by[str(r["recipient_station_key"])] = r

    rows = []
    all_keys = sorted(set(cur_by) | set(hist_by))
    for key in all_keys:
        c = cur_by.get(key)
        h = hist_by.get(key)
        if h is None and c is not None:
            status = "NEW_SINCE_HISTORICAL_REPLAY"
        elif c is None and h is not None:
            status = "NO_LONGER_CURRENT_OPPORTUNITY"
        else:
            old_gain = float(pd.to_numeric(h.get("relocation_gain"), errors="coerce"))
            new_gain = float(pd.to_numeric(c.get("relocation_gain"), errors="coerce"))
            delta = new_gain - old_gain
            denom = max(abs(old_gain), 1.0)
            if abs(delta) <= max(float(stable_abs_gain), float(stable_rel_gain) * denom):
                status = "PERSISTING_STABLE"
            elif delta > 0:
                status = "STRENGTHENED"
            else:
                status = "WEAKENED"

        ref = c if c is not None else h
        old_gain = pd.to_numeric(h.get("relocation_gain"), errors="coerce") if h is not None else np.nan
        new_gain = pd.to_numeric(c.get("relocation_gain"), errors="coerce") if c is not None else np.nan
        rows.append({
            "recipient_station_key": key,
            "recipient_name": str(ref.get("recipient_name", "")),
            "historical_status": status,
            "historical_gain": old_gain,
            "current_gain": new_gain,
            "gain_delta": (float(new_gain) - float(old_gain)) if pd.notna(old_gain) and pd.notna(new_gain) else np.nan,
            "historical_donor_name": str(h.get("donor_name", "")) if h is not None else "",
            "current_donor_name": str(c.get("donor_name", "")) if c is not None else "",
            "comparison_provenance": "CALCULATED:HISTORICAL_REPLAY_VS_CURRENT",
        })
    return pd.DataFrame(rows)
