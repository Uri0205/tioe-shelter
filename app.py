from __future__ import annotations

import os
import html
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk

import portfolio as portfolio_module

# v0.8 deployment-integrity guard. Streamlit Community Cloud can otherwise
# fail with an opaque ImportError when app.py and portfolio.py come from mixed
# commits. Keep the public portfolio API backward-compatible and validate it
# explicitly before binding the names used by the UI.
_REQUIRED_PORTFOLIO_API = (
    "run_portfolio",
    "city_metrics",
    "city_history",
    "city_opportunities",
    "map_points",
    "save_current_snapshot",
    "save_opportunity_state",
    "historical_city_replay",
    "compare_historical_replay_to_current",
    "load_historical_station_demand",
)
_missing_portfolio_api = [
    name for name in _REQUIRED_PORTFOLIO_API
    if not hasattr(portfolio_module, name)
]
if _missing_portfolio_api:
    raise ImportError(
        "TIOE Shelter deployment mismatch: portfolio.py is missing required "
        f"exports: {', '.join(_missing_portfolio_api)}. "
        "Replace app.py, portfolio.py and shelter_engine.py from the same release."
    )

run_portfolio = portfolio_module.run_portfolio
city_metrics = portfolio_module.city_metrics
city_history = portfolio_module.city_history
city_opportunities = portfolio_module.city_opportunities
map_points = portfolio_module.map_points
save_current_snapshot = portfolio_module.save_current_snapshot
save_opportunity_state = portfolio_module.save_opportunity_state
historical_city_replay = portfolio_module.historical_city_replay
compare_historical_replay_to_current = portfolio_module.compare_historical_replay_to_current
load_historical_station_demand = portfolio_module.load_historical_station_demand

APP_VERSION = "0.9.4"
USER_STATUSES = ["טרם נבדקה", "בבדיקה", "נדרשת בדיקת שטח", "אושרה", "יושמה", "לא רלוונטית"]
SYSTEM_HE = {
    "NEW": "חדשה",
    "PERSISTING": "נמשכת",
    "CURRENT": "נוכחית",
    "RESOLVED_INFRASTRUCTURE_CHANGED": "שינוי תשתית זוהה",
    "NO_LONGER_PRIORITY_CANDIDATE": "כבר לא בעדיפות",
    "DROPPED_FROM_CURRENT_SET": "יצאה מהסט הנוכחי",
    "NEW_SINCE_HISTORICAL_REPLAY": "חדשה ביחס לשנה ההיסטורית",
    "NO_LONGER_CURRENT_OPPORTUNITY": "הייתה בריפליי ההיסטורי ואינה נוכחית",
    "PERSISTING_STABLE": "נמשכת ללא שינוי מהותי",
    "STRENGTHENED": "התחזקה",
    "WEAKENED": "נחלשה",
}

st.set_page_config(page_title="TIOE | תיק הסככות", page_icon="🚏", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&display=swap');
:root { --tioe-font: "Heebo", "Noto Sans Hebrew", Arial, sans-serif; }
html, body, [class*="css"] { direction: rtl; }
html { font-size: 18px; }
body, .stApp, .stApp * { font-family: var(--tioe-font) !important; }
[data-testid="stAppViewContainer"] { background: #f5f8fc; }
[data-testid="stSidebar"] { background: #102d4d; }
[data-testid="stSidebar"] * { color: white !important; }
.block-container { padding-top: 1.35rem; padding-bottom: 2rem; max-width: 1650px; }

/* Global Streamlit typography */
h1 { font-size: 2.25rem !important; line-height:1.2 !important; font-weight:800 !important; }
h2 { font-size: 1.85rem !important; line-height:1.25 !important; font-weight:800 !important; }
h3 { font-size: 1.45rem !important; line-height:1.3 !important; font-weight:750 !important; }
h4 { font-size: 1.22rem !important; font-weight:750 !important; }
p, li, label, [data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"] { font-size: 1rem; line-height:1.65; }
[data-testid="stCaptionContainer"] { font-size:.88rem !important; color:#68788b; }
[data-baseweb="select"] * { font-size:1rem !important; }
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] div { font-size:1rem !important; }
[data-testid="stTabs"] button { font-size:1rem !important; font-weight:700 !important; }
[data-testid="stDataFrame"] { font-size:.94rem !important; }
button[kind="primary"], button[kind="secondary"] { font-size:1rem !important; font-weight:700 !important; }

.tioe-title {font-size: 2.45rem; line-height:1.15; font-weight: 800; color:#102d4d; margin-bottom:6px;}
.tioe-sub {font-size:1.05rem; color:#62758a; margin-bottom:14px; line-height:1.55;}
.hero-card {background:white;border:1px solid #dce5ef;border-radius:16px;padding:20px 22px;min-height:158px;box-shadow:0 3px 12px rgba(16,45,77,.06)}
.hero-kicker {font-size:.92rem;font-weight:800;color:#718096;margin-bottom:9px}
.hero-value {font-size:2.55rem;font-weight:800;color:#102d4d;line-height:1.04}
.hero-label {font-size:1.12rem;font-weight:800;color:#263d56;margin-top:10px;line-height:1.45}
.hero-note {font-size:.91rem;color:#708093;margin-top:9px;line-height:1.55}
.hero-red .hero-value {color:#e53935}.hero-blue .hero-value{color:#1769e0}.hero-green .hero-value{color:#14915f}
.metric-card {background:white;border:1px solid #dde6f0;border-radius:14px;padding:16px 17px;min-height:122px;box-shadow:0 2px 9px rgba(16,45,77,.05)}
.metric-value {font-size:1.92rem;font-weight:800;color:#102d4d;line-height:1.05}
.metric-label {font-size:1.04rem;font-weight:700;color:#263d56;margin-top:9px;line-height:1.45}
.metric-note {font-size:.87rem;color:#718096;margin-top:8px;line-height:1.5}
.coverage-note {background:#fff8e8;border:1px solid #f0d69a;border-radius:12px;padding:12px 14px;font-size:.96rem;color:#6f5718;line-height:1.55}
.case-card {background:white;border:1px solid #dce5ef;border-radius:14px;padding:19px 20px;box-shadow:0 2px 9px rgba(16,45,77,.05);min-height:150px}
.case-card h3 { margin-top:8px !important; margin-bottom:10px !important; font-size:1.42rem !important; }
.case-title {font-size:1.12rem;font-weight:800;color:#102d4d;line-height:1.4}
.case-big {font-size:2.15rem;font-weight:800;color:#14915f;line-height:1.1}
.note {font-size:.91rem;color:#718096;line-height:1.55}
.decision-card {background:#eef5ff;border:1px solid #cddff8;border-radius:14px;padding:19px 20px;min-height:154px}
.review-card {background:#fff8e8;border:1px solid #f0d69a;border-radius:14px;padding:19px 20px;min-height:154px}
.prov-card {background:white;border:1px solid #dce5ef;border-radius:14px;padding:16px 18px}
.check-row {padding:10px 0;border-bottom:1px solid #edf1f5;font-size:1rem;line-height:1.55}
.check-row:last-child{border-bottom:none}
.formula-box {background:#f7fafc;border:1px solid #dfe7ef;border-radius:10px;padding:13px 14px;font-family:"Consolas","Courier New",monospace !important;font-size:.98rem;direction:ltr;text-align:left;line-height:1.55}

/* v0.9.4 Historical Comparison Visual Layer */
.compare-hero {background:linear-gradient(135deg,#ffffff 0%,#f4f8ff 100%);border:1px solid #d7e3f2;border-radius:18px;padding:22px 24px;margin:8px 0 18px;box-shadow:0 4px 14px rgba(16,45,77,.06)}
.compare-hero-title {font-size:1.7rem;font-weight:800;color:#102d4d;line-height:1.25;margin-bottom:8px}
.compare-hero-summary {font-size:1.08rem;color:#40556d;line-height:1.65}
.sim-tag {display:inline-block;background:#e8f1ff;color:#1456a0;border:1px solid #c6daf6;border-radius:999px;padding:5px 10px;font-size:.82rem;font-weight:800;margin-bottom:10px}
.delta-card {background:white;border:1px solid #dce5ef;border-radius:15px;padding:17px 18px;min-height:148px;box-shadow:0 2px 9px rgba(16,45,77,.05)}
.delta-label {font-size:.92rem;color:#6c7f93;font-weight:800;margin-bottom:8px}
.delta-current {font-size:2rem;color:#102d4d;font-weight:800;line-height:1.05}
.delta-baseline {font-size:.9rem;color:#7c8da0;margin-top:8px}
.delta-change {font-size:1.08rem;font-weight:800;margin-top:8px}
.delta-good {color:#14865b}.delta-bad {color:#d64545}.delta-neutral {color:#1769e0}
.status-strip {display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 18px}
.status-chip {background:white;border:1px solid #dce5ef;border-radius:999px;padding:8px 12px;font-size:.9rem;font-weight:800;color:#31465d}
.status-chip strong {font-size:1.05rem;margin-right:4px}
.status-new {border-color:#bcd6fb;background:#eef5ff}.status-up {border-color:#bfe5d2;background:#effaf4}.status-stable {background:#f6f8fb}.status-down {border-color:#f1d7a8;background:#fff8e9}.status-out {border-color:#e1dce7;background:#f6f3f8}
.mover-card {background:white;border:1px solid #dce5ef;border-radius:14px;padding:17px 18px;min-height:185px;box-shadow:0 2px 9px rgba(16,45,77,.05)}
.mover-kicker {font-size:.84rem;font-weight:800;color:#6e8092;margin-bottom:7px}
.mover-title {font-size:1.12rem;font-weight:800;color:#102d4d;line-height:1.4;margin-bottom:8px}
.mover-gain {font-size:1.65rem;font-weight:800;line-height:1.1;margin:7px 0}
.mover-note {font-size:.88rem;color:#728296;line-height:1.5}
.section-kicker {font-size:.88rem;color:#6d7f92;font-weight:800;margin-top:3px}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_data(path: str):
    # v0.7: analytical load does not silently create history. Snapshot saving is explicit.
    return run_portfolio(path, save_history=False)


@st.cache_data(show_spinner=False, ttl=86400)
def load_historical_api(year: int, station_ids: tuple[str, ...]):
    # v0.9.3: cache is scoped to year + selected city's StationIds. The API
    # query never needs to load the full national historical resource.
    return load_historical_station_demand(int(year), station_ids=station_ids)


with st.sidebar:
    st.markdown("# TIOE")
    st.caption("Transit Infrastructure Optimization Engine")
    st.markdown("---")
    st.markdown("### תיק הסככות")
    st.markdown("**תמונת מצב**")
    st.markdown("הזדמנויות")
    st.markdown("מה השתנה?")
    st.markdown("היסטוריה")
    st.markdown("---")
    st.caption("Optimize existing infrastructure before adding infrastructure.")

path_candidates = [
    os.environ.get("TIOE_STATIONS_PATH"),
    os.path.join(os.path.dirname(__file__), "data", "Stations.xlsx"),
    os.path.join(os.path.dirname(__file__), "Stations.xlsx"),  # backward-compatible with user's current repo
    "/content/drive/MyDrive/TIOE_Data/Stations.xlsx",
    "/content/drive/MyDrive/TIOE DATA/Stations.xlsx",
]
source_path = next((p for p in path_candidates if p and os.path.exists(p)), None)
if source_path is None:
    st.error("לא נמצא Stations.xlsx. שים אותו בתיקיית data או בשורש האפליקציה.")
    st.stop()

with st.spinner("טוען נתוני תשתית ומחשב את תיק הסככות..."):
    result = load_data(source_path)

cities = result.stations.groupby("city_name").size().sort_values(ascending=False).index.tolist()
default_idx = cities.index("באר שבע") if "באר שבע" in cities else 0

header_left, header_city = st.columns([4, 1])
with header_left:
    st.markdown('<div class="tioe-title">תיק הסככות העירוני</div>', unsafe_allow_html=True)
    st.markdown('<div class="tioe-sub">מצב נוכחי → פוטנציאל אופטימיזציה → השפעה שמומשה</div>', unsafe_allow_html=True)
with header_city:
    city = st.selectbox("עיר", cities, index=default_idx)

metrics = city_metrics(result, city)
opps = city_opportunities(result, city)
hist = city_history(result, city)
points = map_points(result, city)
coverage = metrics["coverage"]
impact = metrics["impact"]

tab_overview, tab_opps, tab_changes, tab_history = st.tabs(["תמונת מצב", "הזדמנויות", "מה השתנה?", "היסטוריה"])

with tab_overview:
    hero_cols = st.columns(3)
    hero_data = [
        ("hero-red", "מצב נוכחי", f"{metrics['unsheltered_boardings']:,.0f}", "עליות ביום בתחנות ללא סככה", "CALCULATED · סכום OnDay בתחנות עמוד פעילות ורגילות"),
        ("hero-blue", "פוטנציאל אופטימיזציה", f"+{metrics['net_reallocation_gain']:,.0f}", "תוספת כיסוי נטו מהקצאה מחדש", "CALCULATED · one-to-one · בכפוף לבדיקת היתכנות פיזית"),
        ("hero-green", "השפעה שמומשה", "—" if not impact.get("available") else f"{impact['infra_coverage_gain']:+,.0f}", "שינוי כיסוי המזוהה עם שינויי תשתית", "UNAVAILABLE עד שיש לפחות שני snapshots" if not impact.get("available") else "CALCULATED מהשוואת סיווג תשתית בין snapshots"),
    ]
    for col, (cls, kicker, val, label, note) in zip(hero_cols, hero_data):
        col.markdown(f'<div class="hero-card {cls}"><div class="hero-kicker">{kicker}</div><div class="hero-value">{val}</div><div class="hero-label">{label}</div><div class="hero-note">{note}</div></div>', unsafe_allow_html=True)

    st.write("")
    cols = st.columns(5)
    support = [
        (f"{metrics['shelters']:,}", "סככות קיימות", "CALCULATED · from OBSERVED infrastructure field"),
        (f"{metrics['poles']:,}", "תחנות עמוד", "CALCULATED · from OBSERVED infrastructure field"),
        (f"{metrics['active_opportunities']:,}", "הזדמנויות פעילות", "one-to-one · REVIEW"),
        (f"{100*metrics['unsheltered_share']:.1f}%" if pd.notna(metrics['unsheltered_share']) else "—", "שיעור העליות ללא סככה", "מתוך עליות בתחנות שסוג התשתית שלהן ידוע"),
        (f"{100*coverage['infra_coverage']:.1f}%" if pd.notna(coverage['infra_coverage']) else "—", "כיסוי מידע על סוג התחנה", f"{coverage['infra_known_stops']:.0f}/{coverage['active_regular_stops']:.0f} תחנות" if pd.notna(coverage['active_regular_stops']) else "UNAVAILABLE"),
    ]
    for col, (val, label, note) in zip(cols, support):
        col.markdown(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{label}</div><div class="metric-note">{note}</div></div>', unsafe_allow_html=True)

    if pd.notna(coverage.get("infra_unknown_stops")) and coverage["infra_unknown_stops"] > 0:
        st.markdown(f'<div class="coverage-note">⚠️ {int(coverage["infra_unknown_stops"]):,} תחנות פעילות רגילות בעיר אינן מסווגות כעמוד/סככה במקור. הן אינן נספרות כ״ללא סככה״.</div>', unsafe_allow_html=True)

    st.write("")
    c1, c2, c3 = st.columns([1.05, .78, 1.55])
    with c1:
        st.markdown("#### מגמת עליות בתחנות ללא סככה")
        if len(hist) >= 2:
            h = hist.copy()
            h["תאריך הרצה"] = h["snapshot_time"].dt.strftime("%d.%m.%Y")
            fig = px.line(h, x="תאריך הרצה", y="unsheltered_boardings", markers=True, labels={"unsheltered_boardings":"עליות/יום"})
            fig.update_layout(margin=dict(l=10,r=10,t=10,b=10), height=305, showlegend=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("אין עדיין שני snapshots. שמור baseline דרך לשונית היסטוריה; לאחר מקור נתונים חדש תופיע כאן מגמה.")
            fig = go.Figure(go.Indicator(mode="number", value=metrics["unsheltered_boardings"], number={"valueformat":",.0f"}, title={"text":"מצב נוכחי — עליות/יום ללא סככה"}))
            fig.update_layout(height=235, margin=dict(l=10,r=10,t=20,b=10))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with c2:
        st.markdown("#### התפלגות עליות לפי סוג תחנה")
        pie = go.Figure(data=[go.Pie(labels=["תחנות עם סככה", "תחנות עמוד"], values=[metrics["sheltered_boardings"], metrics["unsheltered_boardings"]], hole=.62, textinfo="percent")])
        pie.update_layout(height=305, margin=dict(l=5,r=5,t=10,b=5), legend=dict(orientation="h", y=-.1))
        st.plotly_chart(pie, use_container_width=True, config={"displayModeBar": False})

    with c3:
        st.markdown(f"#### מפת מצב תשתיתי — {city}")
        if not points.empty:
            center_lat, center_lon = float(points["lat"].median()), float(points["lon"].median())
            color_map = {"DONOR": [23,105,224,220], "RECIPIENT": [239,68,68,225], "OTHER": [111,130,145,75]}
            map_df = points[["lat","lon","station_name","OnDay","infrastructure_type","point_role","radius"]].copy()
            map_df["color"] = map_df["point_role"].map(color_map)
            map_df["demand_label"] = pd.to_numeric(map_df["OnDay"], errors="coerce").map(lambda x: "אין נתון" if pd.isna(x) else f"{x:,.1f}")
            map_df["role_he"] = map_df["point_role"].map({"DONOR":"סככה מוצעת להעברה", "RECIPIENT":"תחנת עמוד יעד", "OTHER":"תחנה אחרת"})
            layer = pdk.Layer("ScatterplotLayer", data=map_df, get_position="[lon, lat]", get_fill_color="color", get_radius="radius", radius_min_pixels=2, radius_max_pixels=15, pickable=True, stroked=True, get_line_color=[255,255,255,180], line_width_min_pixels=1)
            deck = pdk.Deck(map_style="light", initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=11.5, pitch=0), layers=[layer], tooltip={"html":"<b>{station_name}</b><br>{role_he}<br>עליות/יום: {demand_label}"})
            st.pydeck_chart(deck, use_container_width=True)
            st.caption("כחול = סככה מוצעת להעברה · אדום = תחנת עמוד יעד · אין קווי התאמה; המרחק אינו חלק מההמלצה.")

    st.markdown("### הזדמנויות מרכזיות")
    if opps.empty:
        st.info("לא נמצאו הזדמנויות one-to-one בעיר לפי פרופיל ה-Discovery הנוכחי.")
    else:
        display = opps.head(8).copy()
        display["סטטוס מערכת"] = display["system_status"].map(SYSTEM_HE).fillna(display["system_status"])
        display["סטטוס טיפול"] = display["user_status"]
        display["תחנת עמוד יעד"] = display["recipient_name"]
        display["סככה מוצעת להעברה"] = display["donor_name"]
        display["עליות יעד"] = display["recipient_demand"].round(1)
        display["עליות מקור"] = display["donor_demand"].round(1)
        display["תוספת כיסוי נטו"] = display["relocation_gain"].round(1)
        st.dataframe(display[["opportunity_id","סטטוס מערכת","סטטוס טיפול","תחנת עמוד יעד","סככה מוצעת להעברה","עליות יעד","עליות מקור","תוספת כיסוי נטו"]], use_container_width=True, hide_index=True)

with tab_opps:
    st.markdown("## Pipeline הזדמנויות")
    st.caption("כל הזדמנות היא תיק בדיקה: פעולה מוצעת → ראיות → היתכנות → החלטה. לוגיקת ה-discovery וה-one-to-one allocation לא השתנתה ב-v0.8.")
    if opps.empty:
        st.info("אין הזדמנויות פעילות בעיר.")
    else:
        filter_status = st.multiselect("סינון לפי סטטוס טיפול", USER_STATUSES, default=USER_STATUSES)
        filtered = opps[opps["user_status"].isin(filter_status)].copy()
        if filtered.empty:
            st.info("אין הזדמנויות המתאימות לסינון.")
        else:
            labels = {str(r["opportunity_id"]): f"{r['recipient_name']} · +{r['relocation_gain']:.1f} עליות/יום" for _, r in filtered.iterrows()}
            selected_id = st.selectbox("בחר הזדמנות", list(labels.keys()), format_func=lambda x: labels[x])
            row = filtered[filtered["opportunity_id"] == selected_id].iloc[0]

            st.markdown("### תיק הזדמנות")
            st.caption(f"Opportunity ID: {selected_id}")

            action_col, tier_col = st.columns([2.1, 1])
            with action_col:
                st.markdown(
                    f'<div class="decision-card"><div class="hero-kicker">הפעולה המוצעת</div><div class="case-title">להעביר סככה קיימת מ־{row["donor_name"]} אל {row["recipient_name"]}</div><div class="hero-note">Reuse / reallocation של נכס קיים לפני בנייה חדשה. המרחק בין התחנות מוצג כמידע בלבד ואינו משפיע על ההתאמה.</div></div>',
                    unsafe_allow_html=True,
                )
            with tier_col:
                st.markdown(
                    f'<div class="review-card"><div class="hero-kicker">רמת החלטה</div><div class="hero-value" style="font-size:1.65rem;color:#9a6700">{row.get("decision_tier", "REVIEW")}</div><div class="hero-note">ישימות פיזית: <b>{row.get("physical_feasibility", "UNAVAILABLE")}</b><br>כל עוד הישימות אינה ידועה, ההזדמנות נשארת REVIEW.</div></div>',
                    unsafe_allow_html=True,
                )

            st.write("")
            a, b, c = st.columns([1.15, 1.15, .85])
            with a:
                routes = "—" if pd.isna(row.get("recipient_routes")) else int(row["recipient_routes"])
                deps = "—" if pd.isna(row.get("recipient_departures")) else int(row["recipient_departures"])
                st.markdown(f'<div class="case-card"><div class="case-title">תחנת עמוד יעד</div><h3>{row["recipient_name"]}</h3><b>{row["recipient_demand"]:,.1f}</b> עליות/יום<br>קווים: {routes}<br>נסיעות/עצירות: {deps}<br><span class="note">OBSERVED · Stations.xlsx:OnDay</span></div>', unsafe_allow_html=True)
            with b:
                st.markdown(f'<div class="case-card"><div class="case-title">סככה קיימת מוצעת להעברה</div><h3>{row["donor_name"]}</h3><b>{row["donor_demand"]:,.1f}</b> עליות/יום<br><span class="note">מרחק: {row["distance_m"]:,.0f} מ׳ — מידע בלבד</span><br><span class="note">OBSERVED · Stations.xlsx:OnDay</span></div>', unsafe_allow_html=True)
            with c:
                st.markdown(f'<div class="case-card"><div class="case-title">תוספת כיסוי נטו</div><div class="case-big">+{row["relocation_gain"]:,.1f}</div><span class="note">CALCULATED · recipient OnDay − donor OnDay</span></div>', unsafe_allow_html=True)

            formula_left, formula_right = st.columns([1, 1])
            with formula_left:
                st.markdown("#### איך חושבה התועלת?")
                st.markdown(
                    f'<div class="formula-box">Net Coverage Gain = {row["recipient_demand"]:,.1f} - {row["donor_demand"]:,.1f} = {row["relocation_gain"]:,.1f} boardings/day</div>',
                    unsafe_allow_html=True,
                )
                st.caption("המדד הוא עליות ביום שמקבלות כיסוי סככה נוסף נטו; הוא אינו מספר נוסעים ייחודיים ואינו realized impact.")
            with formula_right:
                st.markdown("#### בדיקות היתכנות נדרשות")
                st.markdown(
                    '<div class="prov-card"><div class="check-row">⬜ האם הסככה הקיימת ניתנת לפירוק ולהעברה?</div><div class="check-row">⬜ האם קיים שטח פיזי מתאים בתחנת היעד?</div><div class="check-row">⬜ האם תנאי נגישות/בטיחות/הצבה מאפשרים התקנה?</div><div class="check-row">⬜ האם הסרת הסככה מתחנת המקור מקובלת מקצועית?</div></div>',
                    unsafe_allow_html=True,
                )
                st.caption("כל ארבע הבדיקות הן UNAVAILABLE במקור הנוכחי; המערכת אינה מניחה שהן עברו.")

            st.markdown("#### מפת תיק ההזדמנות")
            pair_map = pd.DataFrame([
                {"lat": float(row["donor_lat"]), "lon": float(row["donor_lon"]), "name": str(row["donor_name"]), "role": "סככה קיימת מוצעת להעברה", "demand": float(row["donor_demand"]), "color": [23,105,224,230]},
                {"lat": float(row["recipient_lat"]), "lon": float(row["recipient_lon"]), "name": str(row["recipient_name"]), "role": "תחנת עמוד יעד", "demand": float(row["recipient_demand"]), "color": [239,68,68,235]},
            ])
            center_lat = float(pair_map["lat"].mean())
            center_lon = float(pair_map["lon"].mean())
            pair_layer = pdk.Layer(
                "ScatterplotLayer", data=pair_map, get_position="[lon, lat]",
                get_fill_color="color", get_radius=110, radius_min_pixels=8, radius_max_pixels=18,
                pickable=True, stroked=True, get_line_color=[255,255,255,220], line_width_min_pixels=2,
            )
            pair_deck = pdk.Deck(
                map_style="light",
                initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=12.5, pitch=0),
                layers=[pair_layer],
                tooltip={"html":"<b>{name}</b><br>{role}<br>עליות/יום: {demand}"},
            )
            st.pydeck_chart(pair_deck, use_container_width=True)
            st.caption(f"כחול = סככה קיימת · אדום = תחנת יעד · אין קו התאמה על המפה · מרחק אווירי מחושב: {row['distance_m']:,.0f} מ׳ (מידע בלבד).")

            st.markdown("#### מקור הנתונים ועקבות החישוב")
            prov = pd.DataFrame([
                ["סוג תשתית — מקור", "OBSERVED", "Stations.xlsx · shed_structure=2"],
                ["סוג תשתית — יעד", "OBSERVED", "Stations.xlsx · shed_structure=1"],
                ["OnDay — מקור", "OBSERVED", "Stations.xlsx · OnDay"],
                ["OnDay — יעד", "OBSERVED", "Stations.xlsx · OnDay"],
                ["Net Coverage Gain", "CALCULATED", "recipient OnDay − donor OnDay"],
                ["מרחק", "CALCULATED", "קואורדינטות · מידע בלבד"],
                ["הקצאת סככה", "CALCULATED", "one-to-one allocation; כל סככה פעם אחת בלבד"],
                ["Physical feasibility", "UNAVAILABLE", "נדרשת בדיקת שטח/תשתית"],
                ["Decision Tier", "CALCULATED", "REVIEW כל עוד physical feasibility = UNAVAILABLE"],
            ], columns=["מדד", "מקור", "בסיס החישוב"])
            st.dataframe(prov, use_container_width=True, hide_index=True)

            status_col, note_col = st.columns([1, 2])
            with status_col:
                current_status = row.get("user_status", "טרם נבדקה")
                idx = USER_STATUSES.index(current_status) if current_status in USER_STATUSES else 0
                new_status = st.selectbox("סטטוס טיפול", USER_STATUSES, index=idx, key=f"status_{selected_id}")
            with note_col:
                new_note = st.text_input("הערת בדיקה", value=str(row.get("review_note", "")), key=f"note_{selected_id}")
            if st.button("שמור סטטוס", type="primary", key=f"save_{selected_id}"):
                save_opportunity_state(result, selected_id, city, str(row["recipient_station_key"]), new_status, new_note)
                st.success("סטטוס ההזדמנות נשמר.")
                st.cache_data.clear()
                st.rerun()

            st.markdown("#### כל ההזדמנויות בעיר")
            table = opps.copy()
            # v0.8.1 UI stability: older/mixed snapshots may not yet contain the
            # inspector-only fields. Missing feasibility data must never crash the
            # page; it remains explicitly UNAVAILABLE and therefore REVIEW.
            defaults = {
                "system_status": "CURRENT",
                "user_status": "טרם נבדקה",
                "recipient_name": "—",
                "donor_name": "—",
                "relocation_gain": np.nan,
                "decision_tier": "REVIEW",
                "physical_feasibility": "UNAVAILABLE",
            }
            for col_name, default_value in defaults.items():
                if col_name not in table.columns:
                    table[col_name] = default_value
                else:
                    table[col_name] = table[col_name].fillna(default_value)

            table["מערכת"] = table["system_status"].map(SYSTEM_HE).fillna(table["system_status"])
            table["טיפול"] = table["user_status"]
            table["יעד"] = table["recipient_name"]
            table["סככה מוצעת"] = table["donor_name"]
            table["תוספת נטו"] = pd.to_numeric(table["relocation_gain"], errors="coerce").round(1)
            table["רמת החלטה"] = table["decision_tier"]
            table["ישימות פיזית"] = table["physical_feasibility"]
            st.dataframe(
                table[["opportunity_id","מערכת","טיפול","יעד","סככה מוצעת","תוספת נטו","רמת החלטה","ישימות פיזית"]],
                use_container_width=True,
                hide_index=True,
            )

with tab_changes:
    st.markdown("## מה השתנה?")
    st.write("השוואה ויזואלית בין ביקוש היסטורי לבין המצב הנוכחי, לצד שינויי Snapshot של TIOE.")

    hist_year = st.selectbox("שנת ביקוש להשוואה", [2025, 2024], index=0, key="historical_year")
    load_hist = st.checkbox("טען נתוני API היסטוריים", value=False, key="load_historical_api")
    if not load_hist:
        st.info("בחר שנה וסמן את טעינת ה-API כדי לבנות Historical Demand Replay. לאחר הטעינה התוצאה נשמרת ב-cache למשך 24 שעות.")
    else:
        try:
            with st.spinner(f"מוריד ומעבד את מאגר התיקופים לשנת {hist_year}..."):
                city_station_ids = tuple(sorted({
                    str(v).replace(".0", "").strip()
                    for v in result.stations.loc[
                        result.stations["city_name"].astype(str) == str(city),
                        "station_demand_id",
                    ].tolist()
                    if str(v).strip() not in {"", "nan", "None"}
                }))
                if not city_station_ids:
                    raise RuntimeError("No station-demand IDs are available for the selected city")
                hist_source = load_historical_api(int(hist_year), city_station_ids)
                replay = historical_city_replay(result, city, int(hist_year), historical=hist_source)
                hist_cmp = compare_historical_replay_to_current(result, city, replay)

            if replay.get("status") != "AVAILABLE":
                st.warning(f"לא ניתן לבנות ריפליי לשנת {hist_year}: {replay.get('message','UNAVAILABLE')}")
            else:
                hm = replay["metrics"]
                cov = replay.get("coverage", {})
                current_m = metrics
                hist_uns = float(hm.get("unsheltered_boardings_replay", 0) or 0)
                current_uns = float(current_m["unsheltered_boardings"] or 0)
                delta_uns = current_uns - hist_uns
                hist_opps = int(hm.get("active_opportunities_replay", 0) or 0)
                current_opps = int(current_m["active_opportunities"] or 0)
                delta_opps = current_opps - hist_opps

                if delta_uns < -0.5:
                    uns_sentence = f"כיום יש {abs(delta_uns):,.0f} פחות עליות ביום בתחנות ללא סככה לעומת ריפליי {hist_year}."
                    uns_cls = "delta-good"
                elif delta_uns > 0.5:
                    uns_sentence = f"כיום יש {abs(delta_uns):,.0f} יותר עליות ביום בתחנות ללא סככה לעומת ריפליי {hist_year}."
                    uns_cls = "delta-bad"
                else:
                    uns_sentence = f"היקף העליות ללא סככה כמעט ללא שינוי לעומת ריפליי {hist_year}."
                    uns_cls = "delta-neutral"

                opp_sentence = (
                    f"מספר ההזדמנויות השתנה מ-{hist_opps} ל-{current_opps}."
                    if hist_opps != current_opps else
                    f"מספר ההזדמנויות נשאר {current_opps}."
                )
                st.markdown(
                    f'<div class="compare-hero"><div class="sim-tag">SIMULATED · ביקוש היסטורי על מלאי תשתית נוכחי</div>'
                    f'<div class="compare-hero-title">{html.escape(str(city))} — מה השתנה מאז {hist_year}?</div>'
                    f'<div class="compare-hero-summary">{uns_sentence} {opp_sentence}</div></div>',
                    unsafe_allow_html=True,
                )

                if hist_cmp is None or hist_cmp.empty:
                    counts_hist = pd.Series(dtype=int)
                else:
                    counts_hist = hist_cmp["historical_status"].value_counts()

                new_n = int(counts_hist.get("NEW_SINCE_HISTORICAL_REPLAY", 0))
                up_n = int(counts_hist.get("STRENGTHENED", 0))
                stable_n = int(counts_hist.get("PERSISTING_STABLE", 0))
                down_n = int(counts_hist.get("WEAKENED", 0))
                out_n = int(counts_hist.get("NO_LONGER_CURRENT_OPPORTUNITY", 0))

                cards = st.columns(4)
                cards[0].markdown(
                    f'<div class="delta-card"><div class="delta-label">עליות ביום בתחנות ללא סככה</div>'
                    f'<div class="delta-current">{current_uns:,.0f}</div>'
                    f'<div class="delta-baseline">{hist_year}: {hist_uns:,.0f}</div>'
                    f'<div class="delta-change {uns_cls}">{delta_uns:+,.0f} לעומת {hist_year}</div></div>',
                    unsafe_allow_html=True,
                )
                cards[1].markdown(
                    f'<div class="delta-card"><div class="delta-label">הזדמנויות פעילות</div>'
                    f'<div class="delta-current">{current_opps}</div>'
                    f'<div class="delta-baseline">{hist_year}: {hist_opps}</div>'
                    f'<div class="delta-change delta-neutral">{delta_opps:+d} שינוי נטו</div></div>',
                    unsafe_allow_html=True,
                )
                cards[2].markdown(
                    f'<div class="delta-card"><div class="delta-label">נכנסו / יצאו מהסט</div>'
                    f'<div class="delta-current">{new_n} / {out_n}</div>'
                    f'<div class="delta-baseline">חדשות / כבר אינן נוכחיות</div>'
                    f'<div class="delta-change delta-neutral">שינוי בהרכב הפורטפוליו</div></div>',
                    unsafe_allow_html=True,
                )
                matched = int(cov.get("matched_current_stops", 0) or 0)
                total_current = int(cov.get("current_stops", 0) or 0)
                match_pct = (100.0 * matched / total_current) if total_current else np.nan
                match_value = f"{match_pct:.1f}%" if pd.notna(match_pct) else "—"
                match_baseline = f"{matched:,}/{total_current:,} תחנות" if total_current else "UNAVAILABLE"
                match_note = "CALCULATED · matching coverage" if total_current else "אין מכנה תקף"
                cards[3].markdown(
                    f'<div class="delta-card"><div class="delta-label">כיסוי התאמת ביקוש היסטורי</div>'
                    f'<div class="delta-current">{match_value}</div>'
                    f'<div class="delta-baseline">{match_baseline}</div>'
                    f'<div class="delta-change delta-neutral">{match_note}</div></div>',
                    unsafe_allow_html=True,
                )

                st.markdown("### הרכב השינוי בהזדמנויות")
                st.markdown(
                    '<div class="status-strip">'
                    f'<span class="status-chip status-new">חדשות <strong>{new_n}</strong></span>'
                    f'<span class="status-chip status-up">התחזקו <strong>{up_n}</strong></span>'
                    f'<span class="status-chip status-stable">יציבות <strong>{stable_n}</strong></span>'
                    f'<span class="status-chip status-down">נחלשו <strong>{down_n}</strong></span>'
                    f'<span class="status-chip status-out">יצאו <strong>{out_n}</strong></span>'
                    '</div>',
                    unsafe_allow_html=True,
                )

                chart_cols = st.columns(2)
                with chart_cols[0]:
                    st.markdown("#### חשיפה ללא סככה — לפני/היום")
                    df_uns = pd.DataFrame({
                        "תקופה": [str(hist_year), "היום"],
                        "עליות ביום": [hist_uns, current_uns],
                    })
                    fig_uns = px.bar(df_uns, x="תקופה", y="עליות ביום", text="עליות ביום")
                    fig_uns.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
                    fig_uns.update_layout(height=315, margin=dict(l=10, r=10, t=10, b=10), showlegend=False, yaxis_title="עליות/יום", xaxis_title="")
                    st.plotly_chart(fig_uns, use_container_width=True, config={"displayModeBar": False})
                with chart_cols[1]:
                    st.markdown("#### הזדמנויות פעילות — לפני/היום")
                    df_opp = pd.DataFrame({"תקופה": [str(hist_year), "היום"], "הזדמנויות": [hist_opps, current_opps]})
                    fig_opp = px.bar(df_opp, x="תקופה", y="הזדמנויות", text="הזדמנויות")
                    fig_opp.update_traces(textposition="outside")
                    fig_opp.update_layout(height=315, margin=dict(l=10, r=10, t=10, b=10), showlegend=False, yaxis_title="מספר הזדמנויות", xaxis_title="")
                    st.plotly_chart(fig_opp, use_container_width=True, config={"displayModeBar": False})

                if hist_cmp is None or hist_cmp.empty:
                    st.info("אין הזדמנויות להשוואה בעיר שנבחרה.")
                else:
                    st.markdown("### השינויים הבולטים")
                    cmp2 = hist_cmp.copy()
                    cmp2["_delta"] = pd.to_numeric(cmp2["gain_delta"], errors="coerce")
                    priority = {
                        "STRENGTHENED": 0,
                        "WEAKENED": 1,
                        "NEW_SINCE_HISTORICAL_REPLAY": 2,
                        "NO_LONGER_CURRENT_OPPORTUNITY": 3,
                        "PERSISTING_STABLE": 4,
                    }
                    cmp2["_priority"] = cmp2["historical_status"].map(priority).fillna(9)
                    cmp2["_abs_delta"] = cmp2["_delta"].abs().fillna(-1)
                    movers = cmp2.sort_values(["_priority", "_abs_delta"], ascending=[True, False]).head(4)
                    mover_cols = st.columns(min(4, max(1, len(movers))))
                    status_label = {
                        "STRENGTHENED": "התחזקה",
                        "WEAKENED": "נחלשה",
                        "NEW_SINCE_HISTORICAL_REPLAY": "חדשה",
                        "NO_LONGER_CURRENT_OPPORTUNITY": "יצאה מהסט",
                        "PERSISTING_STABLE": "יציבה",
                    }
                    for col, (_, r) in zip(mover_cols, movers.iterrows()):
                        stat = str(r.get("historical_status", ""))
                        target = html.escape(str(r.get("recipient_name", "")))
                        delta = pd.to_numeric(pd.Series([r.get("gain_delta")]), errors="coerce").iloc[0]
                        curr = pd.to_numeric(pd.Series([r.get("current_gain")]), errors="coerce").iloc[0]
                        histg = pd.to_numeric(pd.Series([r.get("historical_gain")]), errors="coerce").iloc[0]
                        donor = r.get("current_donor_name") if pd.notna(r.get("current_donor_name")) else r.get("historical_donor_name")
                        donor = html.escape(str(donor)) if donor is not None and str(donor) != "nan" else "—"
                        delta_text = "—" if pd.isna(delta) else f"{delta:+,.1f}"
                        delta_class = "delta-good" if (pd.notna(delta) and delta > 0) else ("delta-bad" if (pd.notna(delta) and delta < 0) else "delta-neutral")
                        gain_note = f"{hist_year}: {'—' if pd.isna(histg) else f'{histg:,.1f}'} · היום: {'—' if pd.isna(curr) else f'{curr:,.1f}'}"
                        col.markdown(
                            f'<div class="mover-card"><div class="mover-kicker">{status_label.get(stat, SYSTEM_HE.get(stat, stat))}</div>'
                            f'<div class="mover-title">{target}</div>'
                            f'<div class="mover-gain {delta_class}">{delta_text}</div>'
                            f'<div class="mover-note">שינוי בתוספת הכיסוי נטו<br>{gain_note}<br>סככה מוצעת כיום: {donor}</div></div>',
                            unsafe_allow_html=True,
                        )

                    with st.expander("הצג את כל ההזדמנויות והשינויים"):
                        show_hist = hist_cmp.copy()
                        show_hist["שינוי"] = show_hist["historical_status"].map(SYSTEM_HE).fillna(show_hist["historical_status"])
                        show_hist["תחנת יעד"] = show_hist["recipient_name"]
                        show_hist[f"תועלת {hist_year}"] = pd.to_numeric(show_hist["historical_gain"], errors="coerce").round(1)
                        show_hist["תועלת נוכחית"] = pd.to_numeric(show_hist["current_gain"], errors="coerce").round(1)
                        show_hist["שינוי בתועלת"] = pd.to_numeric(show_hist["gain_delta"], errors="coerce").round(1)
                        show_hist["סככה מוצעת אז"] = show_hist["historical_donor_name"]
                        show_hist["סככה מוצעת כיום"] = show_hist["current_donor_name"]
                        st.dataframe(
                            show_hist[["שינוי","תחנת יעד",f"תועלת {hist_year}","תועלת נוכחית","שינוי בתועלת","סככה מוצעת אז","סככה מוצעת כיום"]],
                            use_container_width=True, hide_index=True,
                        )

                st.caption(
                    f"Resource ID: {replay.get('resource_id','')} · "
                    f"שורות מקור שנמשכו: {replay.get('raw_row_count',0):,} · "
                    f"התאמת ביקוש היסטורי: {matched:,}/{total_current:,} תחנות. "
                    "Historical OnDay = CALCULATED from OBSERVED validations; replay = SIMULATED."
                )
                with st.expander("מתודולוגיה ו-Provenance — השוואה היסטורית"):
                    st.markdown(
                        "- `day_1..day_31` הם נתוני מקור נצפים.\n"
                        "- לכל תחנה ותאריך מסכמים את כל חלונות הזמן הזמינים.\n"
                        "- `Historical OnDay` הוא ממוצע ימי א׳–ה׳ ולכן **CALCULATED**.\n"
                        "- סוג הסככה, העיר והקואורדינטות נלקחים מ-`Stations.xlsx` הנוכחי.\n"
                        "- הרצת discovery/allocation עם ביקוש היסטורי על תשתית נוכחית היא **SIMULATED**.\n"
                        "- המרחק נשאר מידע בלבד; one-to-one נשמר; thresholds לא שונו."
                    )
        except Exception as exc:
            st.error(f"טעינת המאגר ההיסטורי נכשלה: {type(exc).__name__}: {exc}")

    st.markdown("---")
    st.markdown("## שינוי מאז Snapshot של TIOE")
    st.caption("שינוי בין שתי ריצות TIOE על מקורות נתונים שונים — נפרד מה-Historical Demand Replay.")
    if result.changes is None or result.changes.empty:
        st.info("עדיין אין בסיס להשוואה. שמור Snapshot ראשון בלשונית היסטוריה, ולאחר עדכון מקור הנתונים שמור Snapshot נוסף.")
    else:
        ch = result.changes[result.changes["city_name"].astype(str) == str(city)].copy()
        if ch.empty:
            st.info("לא זוהו שינויי הזדמנויות בעיר בהשוואה הזמינה.")
        else:
            counts = ch["change_status"].value_counts()
            snap_new = int(counts.get("NEW", 0))
            snap_persist = int(counts.get("PERSISTING", 0))
            snap_infra = int(counts.get("RESOLVED_INFRASTRUCTURE_CHANGED", 0))
            snap_out = int(counts.get("NO_LONGER_PRIORITY_CANDIDATE", 0) + counts.get("DROPPED_FROM_CURRENT_SET", 0))
            st.markdown(
                '<div class="status-strip">'
                f'<span class="status-chip status-new">חדשות <strong>{snap_new}</strong></span>'
                f'<span class="status-chip status-stable">נמשכות <strong>{snap_persist}</strong></span>'
                f'<span class="status-chip status-up">שינוי תשתית <strong>{snap_infra}</strong></span>'
                f'<span class="status-chip status-out">יצאו <strong>{snap_out}</strong></span>'
                '</div>',
                unsafe_allow_html=True,
            )
            show = ch.copy()
            show["סטטוס"] = show["change_status"].map(SYSTEM_HE).fillna(show["change_status"])
            show["תחנת יעד"] = show["recipient_name"]
            show["תועלת קודמת"] = pd.to_numeric(show["previous_gain"], errors="coerce").round(1)
            show["תועלת נוכחית"] = pd.to_numeric(show["current_gain"], errors="coerce").round(1)
            show["שינוי בתועלת"] = pd.to_numeric(show["gain_delta"], errors="coerce").round(1)
            with st.expander("הצג את כל שינויי ה-Snapshot", expanded=snap_new <= 8):
                st.dataframe(
                    show[["סטטוס","תחנת יעד","previous_donor_name","current_donor_name","תועלת קודמת","תועלת נוכחית","שינוי בתועלת"]],
                    use_container_width=True, hide_index=True,
                )

        if impact.get("available"):
            st.markdown("### פירוק השינוי בכיסוי")
            cols = st.columns(3)
            cols[0].metric("שינוי כולל בעליות בתחנות ללא סככה", f"{impact['total_improvement']:+,.0f}")
            cols[1].metric("שינוי כיסוי המזוהה עם שינוי תשתית", f"{impact['infra_coverage_gain']:+,.0f}")
            cols[2].metric("יתר שינוי — ללא ייחוס סיבתי", f"{impact['residual_change']:+,.0f}")

with tab_history:
    st.markdown("## היסטוריה ו-Snapshots")
    st.write("Snapshot שומר את תמונת התשתית וההזדמנויות של מקור הנתונים הנוכחי. אותו קובץ מקור לא נשמר פעמיים.")
    if st.button("שמור Snapshot / Baseline נוכחי", type="primary"):
        saved = save_current_snapshot(result)
        status = saved.get("status", "")
        if status == "SNAPSHOT_SAVED":
            st.success("Snapshot נשמר בהצלחה.")
        elif status == "UNCHANGED_SOURCE_NOT_RESAVED":
            st.info("המקור לא השתנה מאז ה-Snapshot האחרון, ולכן לא נוצר עותק כפול.")
        else:
            st.info(f"סטטוס: {status}")
        st.cache_data.clear()
        st.rerun()

    hist2 = city_history(result, city)
    if hist2.empty:
        st.info("אין עדיין snapshots שמורים לעיר.")
    else:
        h = hist2.copy()
        h["תאריך"] = h["snapshot_time"].dt.strftime("%d.%m.%Y %H:%M")
        h["עליות ללא סככה"] = h["unsheltered_boardings"].round(1)
        h["סככות"] = h["shelters"]
        h["עמודים"] = h["poles"]
        st.dataframe(h[["תאריך","עליות ללא סככה","סככות","עמודים"]], use_container_width=True, hide_index=True)
        if len(h) >= 2:
            fig = px.line(h, x="תאריך", y="עליות ללא סככה", markers=True)
            fig.update_layout(height=330, margin=dict(l=10,r=10,t=20,b=10))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.warning("ב-Community Cloud אחסון קבצים מקומי אינו מסד נתונים קבוע ועלול להימחק בעת redeploy/restart. ב-v0.9.3 זה עדיין מתאים ל-POC; לפני שימוש ארגוני נעביר את ה-registry וה-snapshots לאחסון מתמשך.")

with st.expander("מתודולוגיה ו-Provenance"):
    st.markdown("""
- **סוג התחנה:** `OBSERVED` מתוך `Stations.xlsx:shed_structure`.
- **OnDay:** `OBSERVED` מתוך `Stations.xlsx:OnDay`; עליות/תיקופים ממוצעים ביום חול בהתאם להגדרת המקור.
- **עליות בתחנות ללא סככה:** `CALCULATED` ורק בתחנות שמסווגות כעמוד. תחנות שסוגן אינו ידוע אינן נספרות כעמוד.
- **תוספת כיסוי נטו:** `CALCULATED` = OnDay ביעד פחות OnDay במקור, על סט one-to-one בלבד.
- **ישימות פיזית:** `UNAVAILABLE`; לכן ההזדמנות נשארת `REVIEW` עד בדיקה.
- **מרחק:** מידע בלבד; אינו מסנן ואינו מדרג.
- **סטטוס מערכת:** מחושב מהשוואת snapshots. **סטטוס טיפול:** קלט משתמש נפרד.
""")

st.caption(f"TIOE Shelter Infrastructure Portfolio v{APP_VERSION} · Opportunity Inspector + Pipeline + Snapshot Baseline")
