from __future__ import annotations

import os
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

APP_VERSION = "0.8"
USER_STATUSES = ["טרם נבדקה", "בבדיקה", "נדרשת בדיקת שטח", "אושרה", "יושמה", "לא רלוונטית"]
SYSTEM_HE = {
    "NEW": "חדשה",
    "PERSISTING": "נמשכת",
    "CURRENT": "נוכחית",
    "RESOLVED_INFRASTRUCTURE_CHANGED": "שינוי תשתית זוהה",
    "NO_LONGER_PRIORITY_CANDIDATE": "כבר לא בעדיפות",
    "DROPPED_FROM_CURRENT_SET": "יצאה מהסט הנוכחי",
}

st.set_page_config(page_title="TIOE | תיק הסככות", page_icon="🚏", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
html, body, [class*="css"] { direction: rtl; }
[data-testid="stAppViewContainer"] { background: #f5f8fc; }
[data-testid="stSidebar"] { background: #102d4d; }
[data-testid="stSidebar"] * { color: white !important; }
.block-container { padding-top: 1.8rem; padding-bottom: 2rem; max-width: 1650px; }
h1,h2,h3,h4,p,div,span,label { font-family: Arial, "Noto Sans Hebrew", sans-serif; }
.tioe-title {font-size: 2.0rem; font-weight: 800; color:#102d4d; margin-bottom:2px;}
.tioe-sub {color:#62758a; margin-bottom:12px;}
.hero-card {background:white;border:1px solid #dce5ef;border-radius:16px;padding:17px 19px;min-height:145px;box-shadow:0 3px 12px rgba(16,45,77,.06)}
.hero-kicker {font-size:.78rem;font-weight:800;color:#718096;margin-bottom:8px}
.hero-value {font-size:2.05rem;font-weight:850;color:#102d4d;line-height:1.05}
.hero-label {font-size:1rem;font-weight:800;color:#263d56;margin-top:9px}
.hero-note {font-size:.80rem;color:#7c8b99;margin-top:8px;line-height:1.4}
.hero-red .hero-value {color:#e53935}.hero-blue .hero-value{color:#1769e0}.hero-green .hero-value{color:#14915f}
.metric-card {background:white;border:1px solid #dde6f0;border-radius:14px;padding:13px 15px;min-height:108px;box-shadow:0 2px 9px rgba(16,45,77,.05)}
.metric-value {font-size:1.58rem;font-weight:800;color:#102d4d;line-height:1.05}
.metric-label {font-size:.93rem;font-weight:700;color:#263d56;margin-top:8px}
.metric-note {font-size:.76rem;color:#7c8b99;margin-top:7px}
.coverage-note {background:#fff8e8;border:1px solid #f0d69a;border-radius:12px;padding:10px 12px;font-size:.83rem;color:#6f5718}
.case-card {background:white;border:1px solid #dce5ef;border-radius:14px;padding:16px 18px;box-shadow:0 2px 9px rgba(16,45,77,.05)}
.case-title {font-size:1.13rem;font-weight:800;color:#102d4d}
.case-big {font-size:1.75rem;font-weight:850;color:#14915f}
.note {font-size:.80rem;color:#718096}
.decision-card {background:#eef5ff;border:1px solid #cddff8;border-radius:14px;padding:16px 18px;min-height:142px}
.review-card {background:#fff8e8;border:1px solid #f0d69a;border-radius:14px;padding:16px 18px;min-height:142px}
.prov-card {background:white;border:1px solid #dce5ef;border-radius:14px;padding:14px 16px}
.check-row {padding:7px 0;border-bottom:1px solid #edf1f5;font-size:.9rem}
.check-row:last-child{border-bottom:none}
.formula-box {background:#f7fafc;border:1px solid #dfe7ef;border-radius:10px;padding:10px 12px;font-family:monospace;direction:ltr;text-align:left}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_data(path: str):
    # v0.7: analytical load does not silently create history. Snapshot saving is explicit.
    return run_portfolio(path, save_history=False)


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
                    f'<div class="review-card"><div class="hero-kicker">Decision Tier</div><div class="hero-value" style="font-size:1.65rem;color:#9a6700">{row.get("decision_tier", "REVIEW")}</div><div class="hero-note">Physical feasibility: <b>{row.get("physical_feasibility", "UNAVAILABLE")}</b><br>לכן ההזדמנות אינה ACTIONABLE בשלב זה.</div></div>',
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
                st.markdown("#### איך חושב ה-benefit?")
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

            st.markdown("#### Provenance / עקבות חישוב")
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
            ], columns=["Metric", "Provenance", "Basis"])
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
            table["מערכת"] = table["system_status"].map(SYSTEM_HE).fillna(table["system_status"])
            table["טיפול"] = table["user_status"]
            table["יעד"] = table["recipient_name"]
            table["סככה מוצעת"] = table["donor_name"]
            table["תוספת נטו"] = table["relocation_gain"].round(1)
            table["Decision Tier"] = table["decision_tier"]
            table["Physical Feasibility"] = table["physical_feasibility"]
            st.dataframe(table[["opportunity_id","מערכת","טיפול","יעד","סככה מוצעת","תוספת נטו","Decision Tier","Physical Feasibility"]], use_container_width=True, hide_index=True)

with tab_changes:
    st.markdown("## מה השתנה מאז ה-Snapshot הקודם?")
    if result.changes is None or result.changes.empty:
        st.info("עדיין אין בסיס להשוואה. שמור Snapshot ראשון בלשונית היסטוריה, ולאחר עדכון מקור הנתונים שמור Snapshot נוסף.")
    else:
        ch = result.changes[result.changes["city_name"].astype(str) == str(city)].copy()
        if ch.empty:
            st.info("לא זוהו שינויי הזדמנויות בעיר בהשוואה הזמינה.")
        else:
            counts = ch["change_status"].value_counts()
            cs = st.columns(4)
            vals = [
                (int(counts.get("NEW",0)), "הזדמנויות חדשות"),
                (int(counts.get("PERSISTING",0)), "הזדמנויות נמשכות"),
                (int(counts.get("RESOLVED_INFRASTRUCTURE_CHANGED",0)), "שינוי תשתית זוהה"),
                (int(counts.get("NO_LONGER_PRIORITY_CANDIDATE",0)+counts.get("DROPPED_FROM_CURRENT_SET",0)), "יצאו מהסט הנוכחי"),
            ]
            for col,(v,lbl) in zip(cs,vals):
                col.metric(lbl,v)
            show = ch.copy()
            show["סטטוס"] = show["change_status"].map(SYSTEM_HE).fillna(show["change_status"])
            show["תחנת יעד"] = show["recipient_name"]
            show["Gain קודם"] = pd.to_numeric(show["previous_gain"], errors="coerce").round(1)
            show["Gain נוכחי"] = pd.to_numeric(show["current_gain"], errors="coerce").round(1)
            show["שינוי Gain"] = pd.to_numeric(show["gain_delta"], errors="coerce").round(1)
            st.dataframe(show[["סטטוס","תחנת יעד","previous_donor_name","current_donor_name","Gain קודם","Gain נוכחי","שינוי Gain"]], use_container_width=True, hide_index=True)

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

    st.warning("ב-Community Cloud אחסון קבצים מקומי אינו מסד נתונים קבוע ועלול להימחק בעת redeploy/restart. ב-v0.8 זה עדיין מתאים ל-POC; לפני שימוש ארגוני נעביר את ה-registry וה-snapshots לאחסון מתמשך.")

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
