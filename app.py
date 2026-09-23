from __future__ import annotations

import os
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk

from portfolio import (
    run_portfolio,
    city_metrics,
    city_history,
    city_opportunities,
    map_points,
)

st.set_page_config(page_title="TIOE | תיק הסככות", page_icon="🚏", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
html, body, [class*="css"] { direction: rtl; }
[data-testid="stAppViewContainer"] { background: #f5f8fc; }
[data-testid="stSidebar"] { background: #102d4d; }
[data-testid="stSidebar"] * { color: white !important; }
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1600px; }
h1,h2,h3,h4,p,div,span,label { font-family: Arial, "Noto Sans Hebrew", sans-serif; }
.tioe-title {font-size: 2.0rem; font-weight: 800; color:#102d4d; margin-bottom:2px;}
.tioe-sub {color:#62758a; margin-bottom:18px;}
.hero-card {background:white;border:1px solid #dce5ef;border-radius:16px;padding:18px 20px;min-height:150px;box-shadow:0 3px 12px rgba(16,45,77,.06)}
.hero-kicker {font-size:.78rem;font-weight:800;color:#718096;letter-spacing:.03em;margin-bottom:8px}
.hero-value {font-size:2.05rem;font-weight:850;color:#102d4d;line-height:1.05}
.hero-label {font-size:1.02rem;font-weight:800;color:#263d56;margin-top:9px}
.hero-note {font-size:.82rem;color:#7c8b99;margin-top:8px;line-height:1.4}
.hero-red .hero-value {color:#e53935}.hero-blue .hero-value{color:#1769e0}.hero-green .hero-value{color:#14915f}
.metric-card {background:white;border:1px solid #dde6f0;border-radius:14px;padding:14px 16px;min-height:115px;box-shadow:0 2px 9px rgba(16,45,77,.05)}
.metric-value {font-size:1.65rem;font-weight:800;color:#102d4d;line-height:1.05}
.metric-label {font-size:.95rem;font-weight:700;color:#263d56;margin-top:8px}
.metric-note {font-size:.78rem;color:#7c8b99;margin-top:7px}
.section-card {background:white;border:1px solid #dde6f0;border-radius:14px;padding:15px 17px;box-shadow:0 2px 9px rgba(16,45,77,.04)}
.small-muted {font-size:.82rem;color:#718096}
.badge {display:inline-block;padding:4px 9px;border-radius:999px;font-size:.78rem;font-weight:700;background:#e8f1ff;color:#1769e0}
.coverage-note {background:#fff8e8;border:1px solid #f0d69a;border-radius:12px;padding:10px 12px;font-size:.83rem;color:#6f5718}
</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_data(path: str | None, save_history: bool):
    return run_portfolio(path, save_history=save_history)


with st.sidebar:
    st.markdown("# TIOE")
    st.caption("Transit Infrastructure Optimization Engine")
    st.markdown("---")
    st.markdown("### תיק הסככות")
    st.markdown("תמונת מצב")
    st.markdown("הזדמנויות")
    st.markdown("מה השתנה?")
    st.markdown("מפה")
    st.markdown("היסטוריה")
    st.markdown("---")
    st.caption("Optimize existing infrastructure before adding infrastructure.")


path_candidates = [
    os.environ.get("TIOE_STATIONS_PATH"),
    os.path.join(os.path.dirname(__file__), "data", "Stations.xlsx"),
    "/content/drive/MyDrive/TIOE_Data/Stations.xlsx",
    "/content/drive/MyDrive/TIOE DATA/Stations.xlsx",
    "/mnt/data/stations_inspect/Stations.xlsx",
]
source_path = next((p for p in path_candidates if p and os.path.exists(p)), None)
if source_path is None:
    st.error("לא נמצא Stations.xlsx. שים את הקובץ בתיקיית data ליד האפליקציה, או הגדר TIOE_STATIONS_PATH.")
    st.stop()

with st.spinner("טוען נתוני תשתית ומחשב את תיק הסככות..."):
    result = load_data(source_path, save_history=True)

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

# -----------------------------------------------------------------------------
# Three core product metrics
# -----------------------------------------------------------------------------
hero_cols = st.columns(3)
hero_data = [
    (
        "hero-red",
        "מצב נוכחי",
        f"{metrics['unsheltered_boardings']:,.0f}",
        "עליות ביום בתחנות ללא סככה",
        "CALCULATED · סכום OnDay בתחנות עמוד פעילות ורגילות",
    ),
    (
        "hero-blue",
        "פוטנציאל אופטימיזציה",
        f"+{metrics['net_reallocation_gain']:,.0f}",
        "תוספת כיסוי נטו מהקצאה מחדש",
        "CALCULATED · סט one-to-one בלבד · בכפוף לבדיקת היתכנות פיזית",
    ),
    (
        "hero-green",
        "השפעה שמומשה",
        "—" if not impact.get("available") else f"{impact['infra_coverage_gain']:+,.0f}",
        "שינוי כיסוי המזוהה עם שינויי תשתית",
        "UNAVAILABLE עד שיש לפחות שני snapshots" if not impact.get("available") else "CALCULATED מהשוואת סיווג תשתית בין snapshots · לא טענה סיבתית",
    ),
]
for col, (cls, kicker, val, label, note) in zip(hero_cols, hero_data):
    col.markdown(
        f'<div class="hero-card {cls}"><div class="hero-kicker">{kicker}</div><div class="hero-value">{val}</div><div class="hero-label">{label}</div><div class="hero-note">{note}</div></div>',
        unsafe_allow_html=True,
    )

st.write("")

# Supporting portfolio metrics
cols = st.columns(5)
support = [
    (f"{metrics['shelters']:,}", "סככות קיימות", "REPORTED"),
    (f"{metrics['poles']:,}", "תחנות עמוד", "REPORTED"),
    (f"{metrics['active_opportunities']:,}", "הזדמנויות פעילות", "one-to-one · REVIEW"),
    (f"{100*metrics['unsheltered_share']:.1f}%" if pd.notna(metrics['unsheltered_share']) else "—", "שיעור העליות ללא סככה", "מתוך עליות בתחנות שסוג התשתית שלהן ידוע"),
    (f"{100*coverage['infra_coverage']:.1f}%" if pd.notna(coverage['infra_coverage']) else "—", "כיסוי מידע על סוג התחנה", f"{coverage['infra_known_stops']:.0f}/{coverage['active_regular_stops']:.0f} תחנות פעילות רגילות" if pd.notna(coverage['active_regular_stops']) else "UNAVAILABLE"),
]
for col, (val, label, note) in zip(cols, support):
    col.markdown(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{label}</div><div class="metric-note">{note}</div></div>', unsafe_allow_html=True)

if pd.notna(coverage.get("infra_unknown_stops")) and coverage["infra_unknown_stops"] > 0:
    st.markdown(
        f'<div class="coverage-note">⚠️ {int(coverage["infra_unknown_stops"]):,} תחנות פעילות רגילות בעיר אינן מסווגות כעמוד/סככה במקור. הן אינן נספרות כ"ללא סככה".</div>',
        unsafe_allow_html=True,
    )

st.write("")

# -----------------------------------------------------------------------------
# Trend + coverage + map
# -----------------------------------------------------------------------------
c1, c2, c3 = st.columns([1.15, .85, 1.55])
with c1:
    st.markdown("#### מגמת עליות בתחנות ללא סככה")
    if len(hist) >= 2:
        h = hist.copy()
        h["תאריך הרצה"] = h["snapshot_time"].dt.strftime("%d.%m.%Y")
        fig = px.line(h, x="תאריך הרצה", y="unsheltered_boardings", markers=True, labels={"unsheltered_boardings":"עליות/יום"})
        fig.update_layout(margin=dict(l=10,r=10,t=10,b=10), height=310, showlegend=False)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("נשמר Snapshot ראשון. לאחר עדכון נתונים נוסף תוצג כאן מגמה היסטורית.")
        fig = go.Figure(go.Indicator(mode="number", value=metrics["unsheltered_boardings"], number={"valueformat":",.0f"}, title={"text":"מצב נוכחי — עליות/יום ללא סככה"}))
        fig.update_layout(height=245, margin=dict(l=10,r=10,t=20,b=10))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with c2:
    st.markdown("#### התפלגות עליות לפי סוג תחנה")
    pie = go.Figure(data=[go.Pie(
        labels=["תחנות עם סככה", "תחנות עמוד"],
        values=[metrics["sheltered_boardings"], metrics["unsheltered_boardings"]],
        hole=.62,
        textinfo="percent",
    )])
    pie.update_layout(height=310, margin=dict(l=5,r=5,t=10,b=5), legend=dict(orientation="h", y=-.1))
    st.plotly_chart(pie, use_container_width=True, config={"displayModeBar": False})

with c3:
    st.markdown(f"#### מפת מצב תשתיתי — {city}")
    if not points.empty:
        center_lat = float(points["lat"].median())
        center_lon = float(points["lon"].median())
        color_map = {"DONOR": [23,105,224,220], "RECIPIENT": [239,68,68,225], "OTHER": [111,130,145,85]}
        map_df = points[["lat","lon","station_name","OnDay","infrastructure_type","point_role","radius"]].copy()
        map_df["color"] = map_df["point_role"].map(color_map)
        map_df["demand_label"] = pd.to_numeric(map_df["OnDay"], errors="coerce").map(lambda x: "אין נתון" if pd.isna(x) else f"{x:,.1f}")
        map_df["role_he"] = map_df["point_role"].map({"DONOR":"סככה מוצעת להעברה", "RECIPIENT":"תחנת עמוד יעד", "OTHER":"תחנה אחרת"})
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position="[lon, lat]",
            get_fill_color="color",
            get_radius="radius",
            radius_min_pixels=2,
            radius_max_pixels=15,
            pickable=True,
            stroked=True,
            get_line_color=[255,255,255,180],
            line_width_min_pixels=1,
        )
        deck = pdk.Deck(
            map_style="light",
            initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=11.7, pitch=0),
            layers=[layer],
            tooltip={"html":"<b>{station_name}</b><br>{role_he}<br>עליות/יום: {demand_label}"},
        )
        st.pydeck_chart(deck, use_container_width=True, height=360)
        st.caption("כחול = סככה מוצעת להעברה · אדום = תחנת עמוד יעד · אפור = תחנה אחרת. אין קווי התאמה: המרחק אינו חלק מההמלצה.")

# -----------------------------------------------------------------------------
# Realized impact / change attribution
# -----------------------------------------------------------------------------
st.markdown("#### מה השתנה מאז העדכון הקודם?")
if impact.get("available"):
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("שינוי כולל בעליות ללא סככה", f"{impact['total_improvement']:+,.0f}", help="חיובי = ירידה בעליות בתחנות ללא סככה")
    a2.metric("שינוי כיסוי המזוהה עם תשתית", f"{impact['infra_coverage_gain']:+,.0f}", help="מחושב מתחנות שעברו עמוד↔סככה, לפי OnDay הנוכחי")
    a3.metric("יתר השינוי", f"{impact['residual_change']:+,.0f}", help="עשוי לנבוע מביקוש, שירות, תחנות חדשות/שהוסרו או שינויי מאגר; לא מופרד בשלב זה")
    a4.metric("עמוד → סככה", int(impact["pole_to_shelter_count"]), delta=f"סככה → עמוד: {int(impact['shelter_to_pole_count'])}", delta_color="off")
else:
    st.info("אין עדיין שני snapshots להשוואה. לאחר מקור נתונים חדש המערכת תפריד בין שינויי תשתית לבין יתר השינוי, בלי לייחס סיבתיות שאין לנו נתונים להוכיח.")

chg_cols = st.columns(4)
chg = [
    ("הזדמנויות חדשות", metrics["new_opportunities"]),
    ("הזדמנויות נמשכות", metrics["persisting_opportunities"]),
    ("שינוי תשתית שזוהה", metrics["resolved_infrastructure"]),
    ("כבר לא בסט הנוכחי", metrics["dropped_or_changed"]),
]
for col, (label, val) in zip(chg_cols, chg):
    col.metric(label, val)

# -----------------------------------------------------------------------------
# Opportunities
# -----------------------------------------------------------------------------
st.markdown("#### הזדמנויות עיקריות")
if opps.empty:
    st.info("לא נמצאו כרגע הזדמנויות one-to-one לפי פרופיל ה-Discovery הנוכחי. אין בכך קביעה שאין צורך בסככות.")
else:
    table = opps[["recipient_name","recipient_demand","donor_name","donor_demand","relocation_gain","status_he"]].copy()
    table.columns = ["תחנת עמוד יעד", "עליות/יום ביעד", "סככה מוצעת להעברה", "עליות/יום בתחנת המקור", "תוספת כיסוי נטו", "סטטוס"]
    table["תוספת כיסוי נטו"] = table["תוספת כיסוי נטו"].map(lambda x: f"+{x:,.1f}")
    table["עליות/יום ביעד"] = table["עליות/יום ביעד"].map(lambda x: f"{x:,.1f}")
    table["עליות/יום בתחנת המקור"] = table["עליות/יום בתחנת המקור"].map(lambda x: f"{x:,.1f}")
    st.dataframe(table, use_container_width=True, hide_index=True, height=min(460, 42 + 36*len(table)))

with st.expander("פרובננס, כיסוי נתונים והגדרות"):
    st.markdown(f"""
- **סוג תשתית (סככה/עמוד):** `REPORTED` מתוך `Stations.xlsx → shed_structure`.
- **OnDay:** `REPORTED` מתוך `Stations.xlsx`; לפי מטא-דאטה המקור הוא ממוצע תיקופים/עליות ביום חול.
- **עליות ביום בתחנות ללא סככה:** `CALCULATED` — סכום `OnDay` בתחנות עמוד פעילות ורגילות בלבד.
- **תחנה ללא מידע על סוג תשתית אינה מסווגת כתחנה ללא סככה.** כיסוי סיווג התשתית בעיר: **{100*coverage['infra_coverage']:.1f}%** אם הנתון זמין.
- **שיעור העליות ללא סככה:** מחושב רק מתוך תחנות שסוג התשתית שלהן ידוע וביקושן זמין.
- **תוספת כיסוי נטו מהקצאה מחדש:** `CALCULATED` — `recipient OnDay - donor OnDay`, על סט one-to-one בלבד.
- **השפעה שמומשה:** `CALCULATED` רק כאשר קיימים לפחות שני snapshots. שינוי תשתית מזוהה מסיווג עמוד↔סככה; יתר השינוי אינו מיוחס אוטומטית לביקוש או לשירות.
- **ישימות פיזית להעברה:** `UNAVAILABLE` בשלב זה, ולכן ההזדמנויות נשארות בדרגת `REVIEW`.
- **מרחק:** מידע תיאורי בלבד ואינו מסנן או מדרג התאמות.
""")

st.caption("TIOE Shelter Infrastructure Portfolio v0.6 · Current State / Optimization Potential / Realized Impact")
