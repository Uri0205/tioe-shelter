# TIOE Shelter Infrastructure Optimization - Portfolio Engine v0.6
# Independent Colab prototype: no PTL engine dependency.
#
# v0.4 changes:
# - Hebrew-first UI; removes unnecessary English from the map and side panel.
# - Default map shows only the recommended one-to-one opportunities; baseline layers are optional.
# - Numbered donor/recipient pairs make map and side panel correspond visually.
# - Distance remains secondary informational context only.
# - Replaces technical donor/recipient wording in UI with plain infrastructure language.
#
# v0.3 changes:
# - Demand-first recipient discovery: city-local OnDay rank is the primary criterion.
# - Routes / DepDay remain context only; they do not decide shelter need.
# - Adds one-to-one recommended allocation set: one donor shelter can be allocated once.
# - Keeps candidate alternatives separately from the recommended allocation set.
# - Same-city remains the hard boundary; distance is informational only.
# - Adds lightweight snapshot history / change monitor for recurring portfolio review.
# - Keeps Decision Tier = REVIEW because physical relocation feasibility is UNAVAILABLE.

SHELTER_ENGINE_VERSION = "0.9"

import os
import re
import html
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

try:
    import folium
    from folium import FeatureGroup
except ImportError as exc:
    raise ImportError(
        "Missing folium. In Colab run: !pip -q install folium openpyxl ipywidgets"
    ) from exc


VERSION = "0.8"

# -----------------------------------------------------------------------------
# Discovery configuration
# -----------------------------------------------------------------------------
# These are transparent discovery parameters, NOT policy thresholds and NOT a
# statement that a pole outside the discovery set "does not need" a shelter.
# They are deliberately configurable and should be validated before production.
DONOR_CITY_PERCENTILE = 0.20
RECIPIENT_CITY_DEMAND_CUTOFF = 0.80
TOP_DONORS_PER_RECIPIENT = 5
SAME_COMPLEX_DISTANCE_M = 80

# Infrastructure truth from Stations metadata:
# 1 = pole, 2 = shelter, 3 = central station, 9 = unknown
SHED_LABELS = {
    1: "POLE",
    2: "SHELTER",
    3: "CENTRAL_STATION",
    9: "UNKNOWN",
}


@dataclass(frozen=True)
class DiscoveryConfig:
    donor_city_percentile: float = DONOR_CITY_PERCENTILE
    recipient_city_demand_cutoff: float = RECIPIENT_CITY_DEMAND_CUTOFF
    top_donors_per_recipient: int = TOP_DONORS_PER_RECIPIENT
    same_complex_distance_m: int = SAME_COMPLEX_DISTANCE_M


def _norm_text(value) -> str:
    if pd.isna(value):
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"[\s\-_/.,'\"()]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _clean_id(value) -> str:
    if pd.isna(value):
        return ""
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _coord_to_deg(series: pd.Series) -> pd.Series:
    """Support either integer microdegrees or decimal-degree coordinates."""
    x = pd.to_numeric(series, errors="coerce")
    return pd.Series(
        np.where(x.abs() > 1000, x / 1_000_000.0, x),
        index=series.index,
    )


def haversine_m(lat1, lon1, lat2, lon2):
    """Vectorized great-circle distance in meters (informational in v0.2)."""
    r = 6371008.8
    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    )
    return 2 * r * np.arcsin(np.sqrt(a))


def find_stations_file(explicit_path: Optional[str] = None) -> str:
    """Locate Stations.xlsx in common Colab/Drive locations."""
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    candidates += [
        "/content/drive/MyDrive/TIOE_Data/Stations.xlsx",
        "/content/drive/MyDrive/TIOE DATA/Stations.xlsx",
        "/content/drive/MyDrive/Stations.xlsx",
        "/mnt/data/stations_inspect/Stations.xlsx",  # local test/runtime fallback
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p

    roots = ["/content/drive/MyDrive", "/content/drive/Shareddrives"]
    for root in roots:
        if not os.path.exists(root):
            continue
        for current, dirs, files in os.walk(root):
            rel_depth = os.path.relpath(current, root).count(os.sep)
            if rel_depth > 4:
                dirs[:] = []
                continue
            if "Stations.xlsx" in files:
                return os.path.join(current, "Stations.xlsx")

    raise FileNotFoundError(
        "Stations.xlsx not found. Put it in TIOE_Data or pass explicit_path to load_station_profiles()."
    )


def _rank_within_city(df: pd.DataFrame, source_col: str, output_col: str) -> None:
    """Add city-local percentile rank while preserving missing values."""
    df[output_col] = (
        df.groupby("city_name", group_keys=False)[source_col]
        .rank(pct=True, method="average")
    )


def load_station_profiles(path: Optional[str] = None) -> pd.DataFrame:
    """
    Load and normalize Stations.xlsx.

    MVP eligibility:
      - StationTypeId == 1 (regular stop)
      - StationStatusId == 1 (active)
      - shed_structure in {1,2} (known pole/shelter)
      - valid Israel-area coordinates

    Provenance:
      infrastructure_type: OBSERVED from official station source
      OnDay / Routes / DepDay: OBSERVED source fields
      city percentiles / priority score / gain / distance: CALCULATED
    """
    path = find_stations_file(path)
    usecols = [
        "ID", "Name", "STOP_ID", "CorrectPhStopName", "ID_SEKER",
        "LinkUserID", "Street", "House", "CityCode", "CityName",
        "Longitude", "Latitude", "StationTypeId", "StationStatusId",
        "shed_structure", "OnDay", "On0609", "Routes", "DepDay",
    ]
    df = pd.read_excel(path, usecols=lambda c: c in usecols)

    numeric_cols = [
        "Longitude", "Latitude", "StationTypeId", "StationStatusId",
        "shed_structure", "OnDay", "On0609", "Routes", "DepDay",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["lat"] = _coord_to_deg(df["Latitude"])
    df["lon"] = _coord_to_deg(df["Longitude"])
    df["station_key"] = df["ID"].map(_clean_id)
    # Audit finding: Stations.Name corresponds to StationId in tikuf_per_station.
    df["station_demand_id"] = df["Name"].map(_clean_id)
    df["stop_id"] = df["STOP_ID"].map(_clean_id)
    df["station_name"] = df["CorrectPhStopName"].fillna("").astype(str).str.strip()
    df["city_name"] = df["CityName"].fillna("").astype(str).str.strip()
    df["infrastructure_type"] = df["shed_structure"].map(SHED_LABELS).fillna("UNAVAILABLE")

    eligible = df[
        (df["StationTypeId"] == 1)
        & (df["StationStatusId"] == 1)
        & (df["shed_structure"].isin([1, 2]))
        & df["lat"].between(29.0, 34.0)
        & df["lon"].between(34.0, 36.0)
        & (df["city_name"] != "")
    ].copy()

    # City-local ranks. Missing source values stay missing; no proxy is invented.
    _rank_within_city(eligible, "OnDay", "demand_percentile_city")
    _rank_within_city(eligible, "Routes", "routes_percentile_city")
    _rank_within_city(eligible, "DepDay", "departures_percentile_city")

    # v0.3: demand is the primary recipient signal. Routes / DepDay are context
    # only and are never averaged into the shelter-need decision.
    service_cols = ["routes_percentile_city", "departures_percentile_city"]
    eligible["service_context_signal_count"] = eligible[service_cols].notna().sum(axis=1)
    eligible["service_context_score"] = eligible[service_cols].mean(axis=1, skipna=True)
    eligible.loc[
        eligible["service_context_signal_count"] == 0,
        "service_context_score",
    ] = np.nan

    eligible["infra_provenance"] = "OBSERVED:Stations.xlsx:shed_structure"
    eligible["demand_provenance"] = np.where(
        eligible["OnDay"].notna(),
        "OBSERVED:Stations.xlsx:OnDay",
        "UNAVAILABLE",
    )
    eligible["service_provenance"] = np.where(
        eligible[["Routes", "DepDay"]].notna().any(axis=1),
        "OBSERVED:Stations.xlsx:Routes/DepDay",
        "UNAVAILABLE",
    )

    # Neutral baseline labels; never imply "no shelter needed".
    eligible["analysis_status"] = np.where(
        eligible["shed_structure"] == 2,
        "SHELTER_BASELINE",
        "POLE_UNASSESSED",
    )

    return eligible.reset_index(drop=True)


def classify_candidates(
    stations: pd.DataFrame,
    config: DiscoveryConfig,
) -> pd.DataFrame:
    """
    Classify discovery candidates using city-local context.

    Donor candidate:
      existing shelter + OnDay available + bottom city demand percentile.

    Recipient candidate (v0.3 demand-first):
      pole + OnDay available + city demand percentile above the configured
      discovery cutoff. Routes / DepDay are retained as context only.

    A pole outside the candidate set remains POLE_UNASSESSED. This is never a
    conclusion that the stop does not need a shelter.
    """
    out = stations.copy()

    donor_mask = (
        (out["shed_structure"] == 2)
        & out["OnDay"].notna()
        & out["demand_percentile_city"].notna()
        & (out["demand_percentile_city"] <= config.donor_city_percentile)
    )

    recipient_mask = (
        (out["shed_structure"] == 1)
        & out["OnDay"].notna()
        & out["demand_percentile_city"].notna()
        & (out["demand_percentile_city"] >= config.recipient_city_demand_cutoff)
    )

    out.loc[donor_mask, "analysis_status"] = "DONOR_CANDIDATE"
    out.loc[recipient_mask, "analysis_status"] = "RECIPIENT_CANDIDATE"

    return out


def _same_complex_guard(
    recipient: pd.Series,
    donor: pd.Series,
    distance_m: float,
    config: DiscoveryConfig,
) -> tuple[str, str]:
    """Advisory guard only; never proves or disproves physical feasibility."""
    reasons = []
    if distance_m <= config.same_complex_distance_m:
        rec_name = _norm_text(recipient.get("station_name"))
        don_name = _norm_text(donor.get("station_name"))
        rec_seker = _clean_id(recipient.get("ID_SEKER"))
        don_seker = _clean_id(donor.get("ID_SEKER"))
        rec_link = _clean_id(recipient.get("LinkUserID"))
        don_link = _clean_id(donor.get("LinkUserID"))

        if rec_name and don_name and rec_name == don_name:
            reasons.append("SAME_NORMALIZED_STOP_NAME")
        if rec_seker and don_seker and rec_seker == don_seker:
            reasons.append("SAME_ID_SEKER")
        if rec_link and don_link and rec_link == don_link:
            reasons.append("SAME_LINK_USER_ID")
        if not reasons:
            reasons.append("VERY_CLOSE_STOPS")

    if reasons:
        return "REVIEW", ";".join(reasons)
    return "PASS", "NO_SAME_COMPLEX_SIGNAL"


def build_candidate_opportunities(
    stations: pd.DataFrame,
    config: DiscoveryConfig,
) -> pd.DataFrame:
    """
    Create same-city candidate relocation opportunities.

    Hard boundary: same city. Distance is informational only. Candidate donor
    alternatives are ranked by lowest donor demand / greatest gain. This table
    is discovery evidence, not a one-to-one allocation plan.
    """
    donors = stations[stations["analysis_status"] == "DONOR_CANDIDATE"].copy()
    recipients = stations[stations["analysis_status"] == "RECIPIENT_CANDIDATE"].copy()
    rows = []

    if donors.empty or recipients.empty:
        return pd.DataFrame()

    donors_by_city = {city: g.copy() for city, g in donors.groupby("city_name")}

    for _, rec in recipients.iterrows():
        city_donors = donors_by_city.get(rec["city_name"])
        if city_donors is None or city_donors.empty:
            continue

        local = city_donors.copy()
        local["distance_m"] = haversine_m(
            float(rec["lat"]),
            float(rec["lon"]),
            local["lat"].to_numpy(float),
            local["lon"].to_numpy(float),
        )
        local["relocation_gain"] = float(rec["OnDay"]) - local["OnDay"].astype(float)

        # Distance does NOT determine candidate selection in v0.2.
        # For a fixed recipient, greatest gain equals lowest donor demand.
        local = local.sort_values(
            ["relocation_gain", "OnDay", "station_key"],
            ascending=[False, True, True],
        ).head(config.top_donors_per_recipient)

        for alt_rank, (_, donor) in enumerate(local.iterrows(), start=1):
            distance_m = float(donor["distance_m"])
            gain = float(donor["relocation_gain"])
            guard, guard_reason = _same_complex_guard(rec, donor, distance_m, config)

            rows.append({
                "city_name": rec["city_name"],
                "recipient_station_key": rec["station_key"],
                "recipient_station_id": rec["station_demand_id"],
                "recipient_stop_id": rec["stop_id"],
                "recipient_name": rec["station_name"],
                "recipient_lat": float(rec["lat"]),
                "recipient_lon": float(rec["lon"]),
                "recipient_demand": float(rec["OnDay"]),
                "recipient_routes": None if pd.isna(rec.get("Routes")) else float(rec["Routes"]),
                "recipient_departures": None if pd.isna(rec.get("DepDay")) else float(rec["DepDay"]),
                "recipient_demand_pct_city": None if pd.isna(rec.get("demand_percentile_city")) else float(rec["demand_percentile_city"]),
                "recipient_routes_pct_city": None if pd.isna(rec.get("routes_percentile_city")) else float(rec["routes_percentile_city"]),
                "recipient_departures_pct_city": None if pd.isna(rec.get("departures_percentile_city")) else float(rec["departures_percentile_city"]),
                "recipient_service_context_score": None if pd.isna(rec.get("service_context_score")) else float(rec["service_context_score"]),
                "recipient_service_context_signal_count": int(rec["service_context_signal_count"]),
                "donor_station_key": donor["station_key"],
                "donor_station_id": donor["station_demand_id"],
                "donor_stop_id": donor["stop_id"],
                "donor_name": donor["station_name"],
                "donor_lat": float(donor["lat"]),
                "donor_lon": float(donor["lon"]),
                "donor_demand": float(donor["OnDay"]),
                "donor_demand_pct_city": None if pd.isna(donor.get("demand_percentile_city")) else float(donor["demand_percentile_city"]),
                "relocation_gain": gain,
                "distance_m": distance_m,
                "distance_role": "CALCULATED_INFORMATIONAL_ONLY",
                "alternative_rank": alt_rank,
                "same_complex_guard": guard,
                "guard_reason": guard_reason,
                "physical_relocation_feasibility": "UNAVAILABLE",
                "opportunity_provenance": "CALCULATED",
                "decision_tier": "REVIEW",
            })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            ["city_name", "recipient_demand", "relocation_gain", "alternative_rank"],
            ascending=[True, False, False, True],
        ).reset_index(drop=True)
    return out



def build_allocation_plan(
    stations: pd.DataFrame,
    config: DiscoveryConfig,
) -> pd.DataFrame:
    """
    Build a one-to-one same-city reallocation set.

    Rules:
      - recipients are processed demand-first (highest OnDay first);
      - each donor shelter can be allocated at most once;
      - within the unused donor pool, PASS guards are preferred, then the
        lowest donor demand (maximum gain). Distance never filters/ranks.
      - physical feasibility remains UNAVAILABLE; every row stays REVIEW.
    """
    donors = stations[stations["analysis_status"] == "DONOR_CANDIDATE"].copy()
    recipients = stations[stations["analysis_status"] == "RECIPIENT_CANDIDATE"].copy()
    rows = []

    for city, city_recipients in recipients.groupby("city_name"):
        city_donors = donors[donors["city_name"] == city].copy()
        if city_donors.empty:
            continue

        city_recipients = city_recipients.sort_values(
            ["OnDay", "demand_percentile_city", "station_key"],
            ascending=[False, False, True],
        )
        used_donors = set()
        allocation_rank = 0

        for _, rec in city_recipients.iterrows():
            available = city_donors[~city_donors["station_key"].isin(used_donors)].copy()
            if available.empty:
                break

            available["distance_m"] = haversine_m(
                float(rec["lat"]),
                float(rec["lon"]),
                available["lat"].to_numpy(float),
                available["lon"].to_numpy(float),
            )
            available["relocation_gain"] = float(rec["OnDay"]) - available["OnDay"].astype(float)

            guard_values = []
            guard_reasons = []
            for _, donor in available.iterrows():
                g, reason = _same_complex_guard(
                    rec, donor, float(donor["distance_m"]), config
                )
                guard_values.append(g)
                guard_reasons.append(reason)
            available["same_complex_guard"] = guard_values
            available["guard_reason"] = guard_reasons
            available["guard_sort"] = np.where(
                available["same_complex_guard"] == "PASS", 0, 1
            )

            chosen = available.sort_values(
                ["guard_sort", "OnDay", "station_key"],
                ascending=[True, True, True],
            ).iloc[0]

            used_donors.add(chosen["station_key"])
            allocation_rank += 1
            rows.append({
                "city_name": city,
                "allocation_rank_city": allocation_rank,
                "recipient_station_key": rec["station_key"],
                "recipient_station_id": rec["station_demand_id"],
                "recipient_stop_id": rec["stop_id"],
                "recipient_name": rec["station_name"],
                "recipient_lat": float(rec["lat"]),
                "recipient_lon": float(rec["lon"]),
                "recipient_demand": float(rec["OnDay"]),
                "recipient_demand_pct_city": float(rec["demand_percentile_city"]),
                "recipient_routes": None if pd.isna(rec.get("Routes")) else float(rec["Routes"]),
                "recipient_departures": None if pd.isna(rec.get("DepDay")) else float(rec["DepDay"]),
                "recipient_service_context_score": None if pd.isna(rec.get("service_context_score")) else float(rec["service_context_score"]),
                "donor_station_key": chosen["station_key"],
                "donor_station_id": chosen["station_demand_id"],
                "donor_stop_id": chosen["stop_id"],
                "donor_name": chosen["station_name"],
                "donor_lat": float(chosen["lat"]),
                "donor_lon": float(chosen["lon"]),
                "donor_demand": float(chosen["OnDay"]),
                "donor_demand_pct_city": float(chosen["demand_percentile_city"]),
                "relocation_gain": float(chosen["relocation_gain"]),
                "distance_m": float(chosen["distance_m"]),
                "distance_role": "CALCULATED_INFORMATIONAL_ONLY",
                "same_complex_guard": chosen["same_complex_guard"],
                "guard_reason": chosen["guard_reason"],
                "physical_relocation_feasibility": "UNAVAILABLE",
                "allocation_provenance": "CALCULATED_ONE_TO_ONE_DISCOVERY",
                "decision_tier": "REVIEW",
            })

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            ["city_name", "allocation_rank_city"],
            ascending=[True, True],
        ).reset_index(drop=True)
    return out


def _source_fingerprint(path: str) -> str:
    stat = os.stat(path)
    return f"{os.path.abspath(path)}|{stat.st_size}|{int(stat.st_mtime)}"


def _history_dir_for(stations_path: str) -> str:
    return os.path.join(os.path.dirname(stations_path), "Shelter_History")


def _read_previous_snapshot(history_dir: str):
    latest_path = os.path.join(history_dir, "latest.json")
    if not os.path.exists(latest_path):
        return None
    try:
        with open(latest_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        alloc_path = meta.get("allocation_plan_path")
        if alloc_path and os.path.exists(alloc_path):
            prev_alloc = pd.read_csv(alloc_path, dtype={"recipient_station_key": str, "donor_station_key": str})
        else:
            prev_alloc = pd.DataFrame()
        return meta, prev_alloc
    except Exception:
        return None


def build_change_monitor(
    current_stations: pd.DataFrame,
    current_allocation: pd.DataFrame,
    previous_allocation: pd.DataFrame,
) -> pd.DataFrame:
    """Compare recipient-level allocation state without inventing a causal reason."""
    rows = []
    prev = previous_allocation.copy() if previous_allocation is not None else pd.DataFrame()
    curr = current_allocation.copy() if current_allocation is not None else pd.DataFrame()

    prev_by = {} if prev.empty else {str(r["recipient_station_key"]): r for _, r in prev.iterrows()}
    curr_by = {} if curr.empty else {str(r["recipient_station_key"]): r for _, r in curr.iterrows()}
    station_by = {str(r["station_key"]): r for _, r in current_stations.iterrows()}

    for key, r in curr_by.items():
        old = prev_by.get(key)
        if old is None:
            status = "NEW"
            delta = np.nan
            donor_changed = False
            old_donor = None
            old_gain = np.nan
        else:
            status = "PERSISTING"
            old_gain = pd.to_numeric(old.get("relocation_gain"), errors="coerce")
            delta = float(r["relocation_gain"]) - float(old_gain) if pd.notna(old_gain) else np.nan
            old_donor = old.get("donor_name")
            donor_changed = str(old.get("donor_station_key")) != str(r.get("donor_station_key"))
        rows.append({
            "change_status": status,
            "city_name": r.get("city_name"),
            "recipient_station_key": key,
            "recipient_name": r.get("recipient_name"),
            "previous_donor_name": old_donor,
            "current_donor_name": r.get("donor_name"),
            "donor_changed": donor_changed,
            "previous_gain": old_gain,
            "current_gain": r.get("relocation_gain"),
            "gain_delta": delta,
        })

    for key, old in prev_by.items():
        if key in curr_by:
            continue
        st = station_by.get(key)
        if st is not None and st.get("infrastructure_type") == "SHELTER":
            status = "RESOLVED_INFRASTRUCTURE_CHANGED"
        elif st is not None and st.get("analysis_status") != "RECIPIENT_CANDIDATE":
            status = "NO_LONGER_PRIORITY_CANDIDATE"
        else:
            status = "DROPPED_FROM_CURRENT_SET"
        rows.append({
            "change_status": status,
            "city_name": old.get("city_name"),
            "recipient_station_key": key,
            "recipient_name": old.get("recipient_name"),
            "previous_donor_name": old.get("donor_name"),
            "current_donor_name": None,
            "donor_changed": False,
            "previous_gain": old.get("relocation_gain"),
            "current_gain": np.nan,
            "gain_delta": np.nan,
        })

    return pd.DataFrame(rows)


def prepare_snapshot_history(
    stations_path: str,
    station_profiles: pd.DataFrame,
    candidate_opportunities: pd.DataFrame,
    allocation_plan: pd.DataFrame,
    save_history: bool = True,
) -> dict:
    """Create a lightweight recurring snapshot, avoiding duplicates for unchanged source files."""
    history_dir = _history_dir_for(stations_path)
    fingerprint = _source_fingerprint(stations_path)
    previous = _read_previous_snapshot(history_dir)
    previous_meta, previous_alloc = previous if previous else (None, pd.DataFrame())
    changes = build_change_monitor(station_profiles, allocation_plan, previous_alloc)

    if not save_history:
        return {"status": "HISTORY_DISABLED", "changes": changes, "history_dir": history_dir}

    os.makedirs(history_dir, exist_ok=True)
    if previous_meta and previous_meta.get("source_fingerprint") == fingerprint:
        return {"status": "UNCHANGED_SOURCE_NOT_RESAVED", "changes": changes, "history_dir": history_dir}

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    station_path = os.path.join(history_dir, f"station_profiles_{run_id}.csv")
    candidate_path = os.path.join(history_dir, f"candidate_opportunities_{run_id}.csv")
    allocation_path = os.path.join(history_dir, f"allocation_plan_{run_id}.csv")
    change_path = os.path.join(history_dir, f"changes_{run_id}.csv")

    station_profiles.to_csv(station_path, index=False, encoding="utf-8-sig")
    candidate_opportunities.to_csv(candidate_path, index=False, encoding="utf-8-sig")
    allocation_plan.to_csv(allocation_path, index=False, encoding="utf-8-sig")
    changes.to_csv(change_path, index=False, encoding="utf-8-sig")

    meta = {
        "run_id": run_id,
        "version": VERSION,
        "source_path": stations_path,
        "source_fingerprint": fingerprint,
        "station_profiles_path": station_path,
        "candidate_opportunities_path": candidate_path,
        "allocation_plan_path": allocation_path,
        "changes_path": change_path,
        "station_count": int(len(station_profiles)),
        "candidate_opportunity_count": int(len(candidate_opportunities)),
        "allocation_count": int(len(allocation_plan)),
    }
    with open(os.path.join(history_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"status": "SNAPSHOT_SAVED", "changes": changes, "history_dir": history_dir, "meta": meta}

def _fmt_num(value, decimals=1) -> str:
    if value is None or pd.isna(value):
        return "UNAVAILABLE"
    return f"{float(value):,.{decimals}f}"


def _fmt_pct(value) -> str:
    if value is None or pd.isna(value):
        return "UNAVAILABLE"
    return f"{100.0 * float(value):.0f}%"


def _popup_html(row: pd.Series) -> str:
    name = html.escape(str(row.get("station_name", "")))
    city = html.escape(str(row.get("city_name", "")))
    infra_code = row.get("shed_structure")
    infra = "סככה" if infra_code == 2 else "עמוד" if infra_code == 1 else "לא ידוע"
    status_map = {
        "DONOR_CANDIDATE": "סככה עם ביקוש נמוך — מועמדת להעברה",
        "RECIPIENT_CANDIDATE": "תחנת עמוד עם ביקוש גבוה — יעד אפשרי",
        "POLE_REVIEW_NO_DEMAND": "תחנת עמוד ללא נתוני ביקוש — נדרשת בדיקה",
        "POLE_UNASSESSED": "תחנת עמוד — לא סווגה כהזדמנות בעדיפות גבוהה",
        "BASELINE": "תחנת בסיס",
    }
    status = status_map.get(str(row.get("analysis_status", "")), str(row.get("analysis_status", "")))
    return f"""
    <div dir='rtl' style='font-family:Arial;font-size:13px;min-width:280px;text-align:right'>
      <b style='font-size:15px'>{name}</b><br>{city}<br><br>
      <b>סוג תשתית:</b> {infra}<br>
      <b>תיקופים ביום חול:</b> {_fmt_num(row.get('OnDay'))}<br>
      <b>מספר קווים:</b> {_fmt_num(row.get('Routes'), 0)}<br>
      <b>עצירות ביום:</b> {_fmt_num(row.get('DepDay'), 0)}<br>
      <b>דירוג ביקוש בעיר:</b> {_fmt_pct(row.get('demand_percentile_city'))}<br>
      <b>סטטוס:</b> {html.escape(status)}<br><br>
      <span style='font-size:11px;color:#555'>
      מזהה ביקוש: {html.escape(str(row.get('station_demand_id','')))}<br>
      STOP_ID: {html.escape(str(row.get('stop_id','')))}<br>
      מקור סוג התחנה: OBSERVED — Stations.xlsx<br>
      מקור הביקוש: {html.escape(str(row.get('demand_provenance','')))}
      </span>
    </div>
    """


def build_city_map(
    city_stations: pd.DataFrame,
    city_allocation: pd.DataFrame,
    city_name: str,
) -> folium.Map:
    if city_stations.empty:
        return folium.Map(location=[31.8, 34.8], zoom_start=8, control_scale=True)

    center = [float(city_stations["lat"].median()), float(city_stations["lon"].median())]
    m = folium.Map(location=center, zoom_start=13, control_scale=True, tiles="CartoDB positron")

    # v0.4: keep the map quiet by default. Baseline inventory is available,
    # but only the actual recommended one-to-one pairs are shown initially.
    shelter_group = FeatureGroup(name="כל הסככות בעיר", show=False)
    pole_group = FeatureGroup(name="כל תחנות העמוד בעיר", show=False)
    donor_group = FeatureGroup(name="סככות מוצעות להעברה", show=True)
    recipient_group = FeatureGroup(name="תחנות עמוד יעד", show=True)
    review_group = FeatureGroup(name="תחנות ללא נתוני ביקוש", show=False)
    opp_group = FeatureGroup(name="התאמות מוצעות לבדיקה", show=True)

    # Baseline layers — hidden by default to avoid visual overload.
    for _, row in city_stations.iterrows():
        if row["shed_structure"] == 2:
            color = "green"
            target_group = shelter_group
        else:
            color = "gray"
            target_group = pole_group
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=3,
            color=color,
            weight=1,
            fill=True,
            fill_opacity=0.45,
            tooltip=f"{row['station_name']} | {'סככה' if row['shed_structure']==2 else 'עמוד'}",
            popup=folium.Popup(_popup_html(row), max_width=390),
        ).add_to(target_group)

    # Review-only poles without demand.
    for _, row in city_stations[city_stations["analysis_status"] == "POLE_REVIEW_NO_DEMAND"].iterrows():
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=5,
            color="orange",
            weight=2,
            fill=True,
            fill_opacity=0.75,
            tooltip=f"נדרשת בדיקה — אין נתוני ביקוש | {row['station_name']}",
            popup=folium.Popup(_popup_html(row), max_width=390),
        ).add_to(review_group)

    # Number only the actual one-to-one recommended allocation set.
    alloc_view = city_allocation.sort_values(
        ["recipient_demand", "relocation_gain"], ascending=[False, False]
    ).reset_index(drop=True)

    for idx, op in alloc_view.iterrows():
        pair_no = idx + 1
        donor_html = f"""
        <div style='background:#1565c0;color:white;border:2px solid white;border-radius:50%;
                    width:28px;height:28px;line-height:25px;text-align:center;font-weight:bold;
                    box-shadow:0 0 3px #555'>{pair_no}</div>"""
        recipient_html = f"""
        <div style='background:#d32f2f;color:white;border:2px solid white;border-radius:50%;
                    width:30px;height:30px;line-height:27px;text-align:center;font-weight:bold;
                    box-shadow:0 0 3px #555'>{pair_no}</div>"""

        folium.Marker(
            location=[op["donor_lat"], op["donor_lon"]],
            icon=folium.DivIcon(html=donor_html, icon_size=(30,30), icon_anchor=(15,15)),
            tooltip=f"{pair_no}. סככה מוצעת להעברה — {op['donor_name']} | ביקוש {op['donor_demand']:.1f}",
        ).add_to(donor_group)

        folium.Marker(
            location=[op["recipient_lat"], op["recipient_lon"]],
            icon=folium.DivIcon(html=recipient_html, icon_size=(32,32), icon_anchor=(16,16)),
            tooltip=f"{pair_no}. תחנת עמוד יעד — {op['recipient_name']} | ביקוש {op['recipient_demand']:.1f}",
        ).add_to(recipient_group)

        line_color = "orange" if op["same_complex_guard"] == "REVIEW" else "purple"
        folium.PolyLine(
            locations=[
                [op["donor_lat"], op["donor_lon"]],
                [op["recipient_lat"], op["recipient_lon"]],
            ],
            color=line_color,
            weight=3,
            opacity=0.75,
            tooltip=(
                f"{pair_no}. העברה מוצעת לבדיקה | "
                f"{op['donor_name']} → {op['recipient_name']} | "
                f"תוספת חשיפה: +{op['relocation_gain']:.1f}"
            ),
        ).add_to(opp_group)

    shelter_group.add_to(m)
    pole_group.add_to(m)
    donor_group.add_to(m)
    recipient_group.add_to(m)
    review_group.add_to(m)
    opp_group.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    bounds = city_stations[["lat", "lon"]].dropna()
    if len(bounds) >= 2:
        m.fit_bounds(
            [
                [float(bounds["lat"].min()), float(bounds["lon"].min())],
                [float(bounds["lat"].max()), float(bounds["lon"].max())],
            ],
            padding=(20, 20),
        )
    return m



def allocation_table_html(city_allocation: pd.DataFrame, max_rows: int = 50) -> str:
    if city_allocation.empty:
        return """
        <div dir='rtl' style='padding:16px;font-family:Arial;text-align:right'>
          <b>לא נמצאו כרגע התאמות להעברת סככה בעיר זו.</b><br>
          <span style='font-size:12px;color:#666'>היעדר התאמה אינו אומר שאין צורך בסככה.</span>
        </div>
        """

    view = city_allocation.sort_values(
        ["recipient_demand", "relocation_gain"], ascending=[False, False]
    ).head(max_rows).reset_index(drop=True)

    cards = []
    for idx, r in view.iterrows():
        pair_no = idx + 1
        review = r["same_complex_guard"] == "REVIEW"
        status_txt = "⚠ נדרשת בדיקה נוספת" if review else "ללא דגל חריג"
        status_color = "#b26a00" if review else "#2e7d32"
        cards.append(f"""
        <div dir='rtl' style='font-family:Arial;text-align:right;border:1px solid #ddd;
             border-radius:10px;padding:12px;margin:0 0 10px 0;background:white'>
          <div style='font-size:15px;font-weight:bold;margin-bottom:8px'>
            <span style='display:inline-block;background:#d32f2f;color:white;border-radius:50%;
                         width:24px;height:24px;line-height:24px;text-align:center;margin-left:6px'>{pair_no}</span>
            העברה מוצעת לבדיקה
          </div>
          <div style='margin-bottom:7px'>
            <b>מכאן:</b> {html.escape(str(r['donor_name']))}<br>
            <span style='color:#1565c0'>סככה קיימת</span> | תיקופים ביום חול: <b>{r['donor_demand']:.1f}</b>
          </div>
          <div style='margin-bottom:7px'>
            <b>לכאן:</b> {html.escape(str(r['recipient_name']))}<br>
            <span style='color:#d32f2f'>תחנת עמוד</span> | תיקופים ביום חול: <b>{r['recipient_demand']:.1f}</b>
          </div>
          <div style='font-size:14px;margin-bottom:5px'>
            <b>תוספת נוסעים שייהנו מסככה: +{r['relocation_gain']:.1f}</b>
          </div>
          <div style='font-size:11px;color:#666'>
            מרחק: {r['distance_m']:.0f} מ' — מידע בלבד, אינו משפיע על ההתאמה<br>
            ישימות פיזית של העברת הסככה: לא זמינה בשלב זה<br>
            <span style='color:{status_color}'>{status_txt}</span> | דרגת החלטה: לבדיקה
          </div>
        </div>
        """)

    return f"""
    <div dir='rtl' style='height:650px;overflow:auto;padding:4px 8px 4px 4px'>
      <div style='font-family:Arial;text-align:right;margin-bottom:10px'>
        <b>הזדמנויות להעברת סככות קיימות</b><br>
        <span style='font-size:11px;color:#666'>המספר בכל כרטיס תואם למספר הכחול והאדום על המפה.</span>
      </div>
      {''.join(cards)}
    </div>
    """



def _change_summary_html(changes: pd.DataFrame, history_status: str) -> str:
    if changes is None or changes.empty:
        return f"<span style='font-size:11px'>מעקב שינויים: {html.escape(history_status)} | אין עדיין הרצה קודמת להשוואה.</span>"
    counts = changes["change_status"].value_counts().to_dict()
    parts = [f"{k}: {v}" for k, v in counts.items()]
    return (
        f"<span style='font-size:11px'><b>מעקב שינויים:</b> {html.escape(history_status)} | "
        + " | ".join(parts)
        + "</span>"
    )


def launch_app(stations_path: Optional[str] = None, save_history: bool = True):
    """Launch the recurring Shelter Portfolio viewer in Colab/Jupyter."""
    try:
        import ipywidgets as widgets
        from IPython.display import display, clear_output
    except ImportError as exc:
        raise ImportError("Missing ipywidgets. In Colab run: !pip -q install ipywidgets") from exc

    resolved_path = find_stations_file(stations_path)
    base = load_station_profiles(resolved_path)
    cfg = DiscoveryConfig()
    classified = classify_candidates(base, cfg)
    candidates = build_candidate_opportunities(classified, cfg)
    allocation = build_allocation_plan(classified, cfg)
    history = prepare_snapshot_history(
        resolved_path, classified, candidates, allocation, save_history=save_history
    )
    changes = history.get("changes", pd.DataFrame())

    city_counts = base.groupby("city_name").size().sort_values(ascending=False)
    cities = [c for c in city_counts.index if c]
    if not cities:
        raise ValueError("No eligible cities found in Stations.xlsx")
    default_city = "אשקלון" if "אשקלון" in cities else cities[0]

    city_dropdown = widgets.Dropdown(
        options=cities,
        value=default_city,
        description="עיר:",
        layout=widgets.Layout(width="460px"),
    )
    summary_html = widgets.HTML()
    table_html = widgets.HTML(layout=widgets.Layout(width="40%", min_width="440px"))
    map_output = widgets.Output(layout=widgets.Layout(width="60%", min_width="700px"))

    def render(*_):
        city = city_dropdown.value
        city_stations = classified[classified["city_name"] == city].copy()
        city_alloc = allocation[allocation["city_name"] == city].copy()
        city_changes = changes[changes["city_name"] == city].copy() if not changes.empty else pd.DataFrame()

        n_shelter = int((city_stations["shed_structure"] == 2).sum())
        n_pole = int((city_stations["shed_structure"] == 1).sum())
        n_donor = int((city_stations["analysis_status"] == "DONOR_CANDIDATE").sum())
        n_rec = int((city_stations["analysis_status"] == "RECIPIENT_CANDIDATE").sum())
        n_alloc = len(city_alloc)
        unsheltered_exposure = float(
            city_stations.loc[city_stations["shed_structure"] == 1, "OnDay"].fillna(0).sum()
        )
        realloc_gain = float(city_alloc["relocation_gain"].sum()) if not city_alloc.empty else 0.0

        summary_html.value = f"""
        <div dir='rtl' style='font-family:Arial;padding:8px 0 12px 0;text-align:right'>
          <div style='font-size:18px;font-weight:bold;margin-bottom:6px'>
            אופטימיזציית סככות — {html.escape(city)}
          </div>
          <div style='margin-bottom:8px'>
            תחנות בעיר: <b>{len(city_stations):,}</b> &nbsp;|&nbsp;
            סככות: <b>{n_shelter:,}</b> &nbsp;|&nbsp;
            תחנות עמוד: <b>{n_pole:,}</b> &nbsp;|&nbsp;
            הזדמנויות להעברה לבדיקה: <b>{n_alloc:,}</b>
          </div>
          <div style='background:#f5f5f5;border-radius:8px;padding:8px 10px;margin-bottom:7px'>
            <b>איך קוראים את המפה?</b><br>
            <span style='color:#1565c0'><b>● כחול</b></span> = סככה קיימת עם ביקוש נמוך שמוצעת להעברה &nbsp;|&nbsp;
            <span style='color:#d32f2f'><b>● אדום</b></span> = תחנת עמוד עם ביקוש גבוה שמוצעת לקבלת הסככה &nbsp;|&nbsp;
            <b style='color:#7b1fa2'>— קו</b> = התאמה בין השתיים.<br>
            המספרים על המפה תואמים לכרטיסים בצד.
          </div>
          <div style='font-size:12px'>
            נוסעים יומיים בתחנות עמוד בעיר: <b>{unsheltered_exposure:,.1f}</b> &nbsp;|&nbsp;
            פוטנציאל תוספת חשיפה לסככה בהקצאות המוצעות: <b>+{realloc_gain:,.1f}</b>
          </div>
          <div style='font-size:11px;color:#666;margin-top:5px'>
            הזיהוי הוא ברמת אותה עיר. המרחק מוצג כמידע בלבד ואינו מסנן או מדרג.
            כל סככה מוקצית לכל היותר פעם אחת. ישימות פיזית = UNAVAILABLE; כל ההצעות הן REVIEW.<br>
            {_change_summary_html(city_changes, history.get('status','UNAVAILABLE'))}
          </div>
        </div>
        """
        table_html.value = allocation_table_html(city_alloc)

        with map_output:
            clear_output(wait=True)
            display(build_city_map(city_stations, city_alloc, city))

    city_dropdown.observe(render, names="value")
    display(
        city_dropdown,
        summary_html,
        widgets.HBox([map_output, table_html], layout=widgets.Layout(width="100%")),
    )
    render()

    return {
        "station_profiles": classified,
        "candidate_opportunities": candidates,
        "allocation_plan": allocation,
        "changes": changes,
        "history": history,
        "city_dropdown": city_dropdown,
        "config": cfg,
    }


if __name__ == "__main__":
    print(
        f"TIOE Shelter Map MVP v{VERSION} loaded.\n"
        "In Colab: mount Drive, then run launch_app()."
    )
