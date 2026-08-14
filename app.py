"""
Memphis Grizzlies Performance Dashboard.

Audience: Medical & Performance practitioners
Three sub-tabs, each a full view of one graph:
  1. Workload    — accel/decel per session as dots (color = Game/Practice,
                   bubble size = distance) + a click-through session detail.
  2. Force Plate — concentric/eccentric force trend (Left/Right/Mean/Both),
                   a per-session 2x2 force matrix, and limb-symmetry screening.
  3. ACWR        — workload-vs-force stacked bars + Acute:Chronic Workload Ratio.

All loading/cleaning/metrics live in data.py; this file is UI only.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import data

# ── Grizzlies palette ────────────────────────────────────────────────────
NAVY = "#12173F"       # Memphis Midnight Blue — Games / concentric / workload
BEALE = "#5D76A9"      # Beale Street Blue — neutral accent
BABY = "#BED4E9"       # light blue — Practices
GOLD = "#F5B112"       # Grizzlies Gold — distance/jump bubbles / eccentric / force
SMOKE = "#707271"      # smoke gray
GREEN, YELLOW, RED = "#2E9E5B", "#E7B416", "#CB3234"  # ACWR / symmetry zones

RULE = data.FREQ_RULE                 # {"Day":"D","Week":"W","Month":"ME"}
CONC, ECC = "Peak_Concentric_Force", "Peak_Eccentric_Force"
ASSETS = Path(__file__).parent / "assets"
LOGO = ASSETS / "grizzlies_logo.png"

# ── Page setup ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Memphis Grizzlies Performance Dashboard",
    page_icon=str(LOGO) if LOGO.exists() else None,
    layout="wide",
)

# CSS polish: tighter padding, navy headings, pill + matrix styles.
st.markdown(
    f"""
    <style>
      .block-container {{ padding-top: 2rem; }}
      h1, h2, h3 {{ color: {NAVY}; }}
      .pill {{ display:inline-block; padding:2px 10px; border-radius:12px;
               font-size:0.8rem; font-weight:600; color:white; }}
      .caption-sm {{ color:{SMOKE}; font-size:0.85rem; }}
      table.fmx {{ border-collapse:collapse; width:100%; }}
      table.fmx th, table.fmx td {{ border:1px solid #d7dced; padding:8px 10px;
               text-align:center; font-size:0.9rem; }}
      table.fmx th {{ background:{NAVY}; color:white; }}
      table.fmx td.rowlab {{ background:{BABY}; color:{NAVY}; font-weight:600; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data
def get_data():
    """Load + clean once (cached). Also precompute the per-session force index."""
    tracking, force_plate, schedule = data.load_and_clean()
    force_sessions = data.composite_force_table(force_plate)
    return tracking, force_plate, schedule, force_sessions


tracking, force_plate, schedule, force_sessions = get_data()
roster = data.get_roster(tracking, force_plate)
date_by_day = schedule.set_index("Date")["Type"]  # Date -> Game/Practice lookup


# ── Small UI helpers ─────────────────────────────────────────────────────
def pill(text, color):
    return f"<span class='pill' style='background:{color}'>{text}</span>"


def detail_legend():
    """Color key for the session-detail average lines (non-technical friendly)."""
    def line(color, dash="solid"):
        return (f"<span style='display:inline-block;width:26px;height:0;"
                f"vertical-align:middle;border-top:3px {dash} {color};'></span>")
    st.markdown(
        "<div class='caption-sm' style='margin:2px 0 8px'><b>Reference lines:</b> "
        f"{line('black')} Season avg &nbsp;&nbsp; "
        f"{line(NAVY, 'dotted')} Month avg &nbsp;&nbsp; "
        f"{line(GOLD, 'dotted')} Week avg (events) &nbsp;&nbsp; "
        f"{line(BEALE, 'dashed')} Week avg (distance)</div>",
        unsafe_allow_html=True)


def insufficient_panel(what, player):
    """Graceful 'no data' state for the roster-mismatch players."""
    st.warning(
        f"### Insufficient Player Data\n\n**{player}** has no **{what}** records, "
        f"so this view can't be shown. Pick another player, or use the tabs where "
        f"this player does have data."
    )


def header():
    c1, c2 = st.columns([1, 9])
    with c1:
        if LOGO.exists():
            st.image(str(LOGO), width=84)
    with c2:
        st.markdown(
            "<h1 style='margin-bottom:0'>Memphis Grizzlies Performance Dashboard</h1>"
            "<div class='caption-sm'>Medical &amp; Performance dashboard · "
            "2023-24 season</div>",
            unsafe_allow_html=True,
        )


def selected_date(state_key):
    """Read a click selection out of session_state (set by the plotly charts)."""
    return st.session_state.get(state_key)


def read_click(event, state_key):
    """Store the x (date) of a clicked point so the detail panels can react."""
    if event and getattr(event, "selection", None):
        pts = event.selection.get("points", [])
        if pts:
            st.session_state[state_key] = pd.Timestamp(pts[0]["x"]).normalize()


def period_subset(df, sel_date, agg):
    """Rows of df falling in the Day/Week/Month that contains sel_date."""
    if agg == "Day":
        return df[df["Date"] == sel_date]
    if agg == "Week":
        iso = sel_date.isocalendar()
        wk = df["Date"].dt.isocalendar()
        return df[(wk.week == iso.week) & (wk.year == iso.year)]
    return df[(df["Date"].dt.month == sel_date.month) &
              (df["Date"].dt.year == sel_date.year)]


def period_title(sel_date, agg):
    """Dynamic label like 'Week of Mar 10, 2024'."""
    if agg == "Month":
        return f"Month of {sel_date.strftime('%b %Y')}"
    return f"{agg} of {sel_date.strftime('%b %d, %Y')}"


def bubble_sizeref(series, max_px=22.0):
    """Plotly area-scaling so the biggest bubble is ~max_px pixels."""
    return 2.0 * max(series.max(), 1e-9) / (max_px ** 2)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 · Workload
# ═══════════════════════════════════════════════════════════════════════════
def wl_aggregate(df, agg):
    """Player workload rows -> per Day/Week/Month totals, with an Events column."""
    if agg == "Day":
        out = df.copy()                       # already one row per day
    else:
        out = data.resample_player(
            df, agg, {"High_Accel": "sum", "High_Decel": "sum", "Distance": "sum"})
    out["Events"] = out["High_Accel"] + out["High_Decel"]
    return out


def team_events_avg(agg, type_filter, lo, hi):
    """Team-wide average of total events at the chosen grain (for comparison)."""
    df = tracking[(tracking["Date"] >= lo) & (tracking["Date"] <= hi)]
    if type_filter != "Both":
        df = df[df["Type"] == type_filter]
    if df.empty:
        return None
    if agg == "Day":
        return (df["High_Accel"] + df["High_Decel"]).mean()
    per = (df.set_index("Date").groupby("Player")
           .resample(RULE[agg])[["High_Accel", "High_Decel"]].sum().dropna(how="all"))
    return (per["High_Accel"] + per["High_Decel"]).mean()


def workload_scatter(frame, agg, type_filter, team_val, season_val):
    """Dots: y = total events, color = Game/Practice, bubble size = distance."""
    fig = go.Figure()
    sizeref = bubble_sizeref(frame["Distance"])

    def add_dots(sub, color):
        # Real data trace; legend is provided separately via fixed-size swatches.
        fig.add_scatter(
            x=sub["Date"], y=sub["Events"], mode="markers", showlegend=False,
            marker=dict(size=sub["Distance"], sizemode="area", sizeref=sizeref,
                        sizemin=4, color=color, opacity=0.85,
                        line=dict(width=1, color="white")),
            # Keep unselected dots readable (don't grey the whole chart on click).
            unselected=dict(marker=dict(opacity=0.45)),
            customdata=np.stack([sub["High_Accel"], sub["High_Decel"], sub["Distance"]], -1),
            hovertemplate=("<b>%{x|%b %d, %Y}</b><br>"
                           "High_Accel: %{customdata[0]:.0f}<br>"
                           "High_Decel: %{customdata[1]:.0f}<br>"
                           "Distance: %{customdata[2]:.2f} mi<extra></extra>"))

    def legend_swatch(name, color, size=15):
        # Big fixed marker shown ONLY in the legend, so the color key is readable.
        fig.add_scatter(x=[None], y=[None], mode="markers", name=name,
                        marker=dict(size=size, color=color, opacity=0.9,
                                    line=dict(width=1, color="white")))

    # Color by session type only at Day grain (a week/month bucket mixes types).
    if agg == "Day" and "Type" in frame:
        for tname, color in [("Game", NAVY), ("Practice", BABY)]:
            sub = frame[frame["Type"] == tname]
            if not sub.empty:
                add_dots(sub, color)
                legend_swatch(tname, color)
    else:
        color = {"Both": BEALE, "Game": NAVY, "Practice": BABY}[type_filter]
        add_dots(frame, color)
        legend_swatch("Sessions", color)

    # Legend-only entry that documents the bubble-size encoding.
    legend_swatch("Distance (mi) (Bubble Size)", SMOKE, size=12)
    # Optional reference lines: team average and the player's own season average.
    if team_val is not None:
        fig.add_hline(y=team_val, line=dict(color=SMOKE, dash="dash", width=1.2),
                      annotation_text=f"Team avg events ({team_val:.0f})",
                      annotation_position="top right")
    if season_val is not None:
        fig.add_hline(y=season_val, line=dict(color=GOLD, dash="dash", width=1.4),
                      annotation_text=f"Player season avg ({season_val:.0f})",
                      annotation_position="bottom right")

    fig.update_layout(
        height=520, hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=60, b=40, l=40, r=20), plot_bgcolor="white",
        yaxis_title="High-intensity events", xaxis_title="Date")
    return fig


def detail_bar(title, v, bar_color, week_color, week_dash, fmt):
    """One horizontal bar for the clicked period with a FIXED 0..axmax range and
    season/month/week average lines; hover reports those averages.
    v = dict(value, season, month, week, axmax)."""
    fig = go.Figure()
    fig.add_bar(
        x=[v["value"]], y=[""], orientation="h",
        marker_color=bar_color, text=[fmt.format(v["value"])],
        textposition="outside", showlegend=False,
        hovertemplate=(f"{title}: {fmt.format(v['value'])}<br>"
                       f"Week avg: {fmt.format(v['week'])}<br>"
                       f"Month avg: {fmt.format(v['month'])}<br>"
                       f"Season avg: {fmt.format(v['season'])}<extra></extra>"))
    # Season=black solid, Month=navy dotted, Week=metric-specific.
    for x, color, dash in [(v["season"], "black", "solid"),
                           (v["month"], NAVY, "dot"),
                           (v["week"], week_color, week_dash)]:
        fig.add_vline(x=x, line=dict(color=color, dash=dash, width=1.8))
    fig.update_xaxes(range=[0, v["axmax"]])
    fig.update_layout(height=185, margin=dict(t=45, b=20, l=10, r=45),
                      plot_bgcolor="white", bargap=0.5,
                      title=dict(text=title, x=0.5, xanchor="center",
                                 font=dict(size=14, color=NAVY)))
    return fig


def render_tab_workload(player, avail):
    st.subheader("High-intensity workload per session")
    if not avail["has_tracking"]:
        insufficient_panel("tracking / workload", player)
        return
    trk_p = tracking[tracking["Player"] == player].copy()

    # Controls — session type is now a selectbox to match 'Aggregate by'.
    c1, c2, c3, c4 = st.columns([1.1, 1.2, 2.4, 1.3])
    agg = c1.selectbox("Aggregate by", ["Day", "Week", "Month"], key="agg1")
    type_filter = c2.selectbox("Session type", ["Both", "Game", "Practice"], key="type1")
    dmin, dmax = trk_p["Date"].min().date(), trk_p["Date"].max().date()
    dr = c3.slider("Date range", min_value=dmin, max_value=dmax,
                   value=(dmin, dmax), key="dr1")
    compare = c4.checkbox("Compare to team avg", key="cmp1")
    show_season = c4.checkbox("Player season avg", key="seasonavg1")

    # Apply the session-type filter, then aggregate the FULL season (for detail),
    # and slice to the visible date range for the chart itself.
    tview = trk_p if type_filter == "Both" else trk_p[trk_p["Type"] == type_filter]
    agg_full = wl_aggregate(tview, agg)
    lo, hi = pd.Timestamp(dr[0]), pd.Timestamp(dr[1])
    chart_frame = agg_full[(agg_full["Date"] >= lo) & (agg_full["Date"] <= hi)]

    if chart_frame.empty:
        st.info("No sessions match the current filters.")
        return

    team_val = team_events_avg(agg, type_filter, lo, hi) if compare else None
    season_val = agg_full["Events"].mean() if show_season else None
    fig = workload_scatter(chart_frame, agg, type_filter, team_val, season_val)
    # on_select='rerun' makes the chart return the point the user clicks.
    event = st.plotly_chart(fig, key="wl_chart", on_select="rerun",
                            use_container_width=True)
    read_click(event, "wl_date")

    # ── Click-through session detail (two horizontal bar charts) ──
    st.divider()
    sel = selected_date("wl_date")
    row = agg_full[agg_full["Date"] == sel] if sel is not None else agg_full.iloc[0:0]
    if row.empty:
        st.info("Click any dot above to break that session down below.")
        return
    r = row.iloc[0]
    st.markdown(f"**Session detail — {period_title(pd.Timestamp(sel), agg)}**")
    detail_legend()                       # color key right under the heading

    # Season/month/week windows around the clicked period (current grain).
    same_month = agg_full[(agg_full["Date"].dt.month == sel.month) &
                          (agg_full["Date"].dt.year == sel.year)]
    iso = sel.isocalendar()
    wk = agg_full["Date"].dt.isocalendar()
    same_week = agg_full[(wk.week == iso.week) & (wk.year == iso.year)]

    def stat(col, axmax):
        return dict(value=r[col], season=agg_full[col].mean(),
                    month=same_month[col].mean(), week=same_week[col].mean(),
                    axmax=axmax)
    # Fixed axes: counts round up to the next 10 above the player's grain-max,
    # distance up to the next 0.5 mile — so a full bar means their hardest.
    ceil10 = lambda s: float(np.ceil(s.max() / 10.0) * 10)
    dist = stat("Distance", float(np.ceil(agg_full["Distance"].max() / 0.5) * 0.5))
    accel = stat("High_Accel", ceil10(agg_full["High_Accel"]))
    decel = stat("High_Decel", ceil10(agg_full["High_Decel"]))

    # Accelerations (left) and Decelerations (right) on top; one Distance bar
    # centered below -> the three form an inverted triangle.
    d1, d2 = st.columns(2)
    d1.plotly_chart(detail_bar("Accelerations", accel, BEALE, GOLD, "dot", "{:.0f}"),
                    use_container_width=True)
    d2.plotly_chart(detail_bar("Decelerations", decel, BEALE, GOLD, "dot", "{:.0f}"),
                    use_container_width=True)
    _, mid, _ = st.columns([1, 2, 1])
    mid.plotly_chart(detail_bar("Distance (mi)", dist, GOLD, BEALE, "dash", "{:.2f}"),
                     use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 · Force Plate
# ═══════════════════════════════════════════════════════════════════════════
def force_series(df, metric, leg, agg):
    """Per-period mean of a force metric for one leg ('Mean' averages L+R)."""
    d = df if leg == "Mean" else df[df["Leg"] == leg]
    s = d.groupby("Date")[metric].mean().resample(RULE[agg]).mean().dropna()
    return s


def force_timeseries(fp_view, agg, foot, show_jump, team_view):
    """Force trend: 2 lines (Mean/Left/Right) or 4 (Both); jump height optional
    as bubble size; hover shows everything at that date at once."""
    fig = go.Figure()

    def add_line(metric, leg, color, dash, name):
        s = force_series(fp_view, metric, leg, agg)
        fig.add_scatter(x=s.index, y=s.values, mode="lines+markers", name=name,
                        line=dict(color=color, dash=dash, width=2),
                        marker=dict(size=7),   # bigger = easier to click-select
                        hovertemplate=f"{name}: %{{y:.0f}} N<extra></extra>")

    # 2 lines for a single condition, 4 lines under 'Both' (color=metric, dash=leg).
    if foot == "Both":
        add_line(CONC, "Left", NAVY, "solid", "Concentric L")
        add_line(CONC, "Right", NAVY, "dash", "Concentric R")
        add_line(ECC, "Left", GOLD, "solid", "Eccentric L")
        add_line(ECC, "Right", GOLD, "dash", "Eccentric R")
    else:
        add_line(CONC, foot, NAVY, "solid", f"Concentric ({foot})")
        add_line(ECC, foot, GOLD, "solid", f"Eccentric ({foot})")

    # Jump height as bubble size, positioned on the mean-concentric line.
    if show_jump:
        jh = (fp_view.groupby("Date")["Jump_Height"].mean()
              .resample(RULE[agg]).mean().dropna())
        base = force_series(fp_view, CONC, "Mean", agg)
        idx = jh.index.intersection(base.index)
        if len(idx):
            fig.add_scatter(
                x=idx, y=base.loc[idx], mode="markers",
                name="Jump height (Bubble Size)",
                marker=dict(size=jh.loc[idx], sizemode="area",
                            sizeref=bubble_sizeref(jh, 26), sizemin=4,
                            color=GOLD, opacity=0.35, line=dict(width=1, color="white")),
                customdata=jh.loc[idx].values.reshape(-1, 1),
                hovertemplate="Jump height: %{customdata[0]:.1f} in<extra></extra>")

    # Optional team-average concentric/eccentric (mean condition) lines.
    if team_view is not None:
        for metric, color in [(CONC, NAVY), (ECC, GOLD)]:
            s = force_series(team_view, metric, "Mean", agg)
            fig.add_scatter(x=s.index, y=s.values, mode="lines", name=f"Team {metric.split('_')[1]}",
                            line=dict(color=SMOKE, dash="dot", width=1.2),
                            hoverinfo="skip")

    fig.update_layout(
        height=460, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=60, b=40, l=40, r=20), plot_bgcolor="white",
        yaxis_title="Mean Force (N)", xaxis_title="Date")
    return fig


def force_matrix_html(sub, title):
    """2x2 table: rows Concentric/Eccentric, cols Left/Right — session means."""
    def cell(metric, leg):
        v = sub.loc[sub["Leg"] == leg, metric].mean()
        return "—" if pd.isna(v) else f"{v:.0f} N"
    return f"""
    <div style='font-weight:600;color:{NAVY};margin-bottom:6px'>{title}</div>
    <table class='fmx'>
      <tr><th></th><th>Left</th><th>Right</th></tr>
      <tr><td class='rowlab'>Concentric</td><td>{cell(CONC,'Left')}</td><td>{cell(CONC,'Right')}</td></tr>
      <tr><td class='rowlab'>Eccentric</td><td>{cell(ECC,'Left')}</td><td>{cell(ECC,'Right')}</td></tr>
    </table>"""


def asymmetry_figure(asym, highlight_dates=None):
    """Compact |L-R| jump-height asymmetry over time with the 10% flag. Dots in
    the selected Day/Week/Month get a cyan outline so it's clear which ones feed
    the matrix and the symmetry cell."""
    colors = np.where(asym["asym_pct"] >= data.ASYMMETRY_THRESHOLD, RED, GREEN)
    if highlight_dates is not None:
        hit = asym["Date"].isin(list(highlight_dates))
        line_color = np.where(hit, "#22D3EE", "white")     # cyan on selected
        line_width = np.where(hit, 3.0, 0.5)
    else:
        line_color, line_width = "white", 0.5
    fig = go.Figure()
    fig.add_scatter(x=asym["Date"], y=asym["asym_pct"], mode="markers",
                    marker=dict(size=8, color=colors, opacity=0.85,
                                line=dict(color=line_color, width=line_width)),
                    hovertemplate="<b>%{x|%b %d}</b><br>Asymmetry: %{y:.1f}%<extra></extra>")
    fig.add_hline(y=data.ASYMMETRY_THRESHOLD, line=dict(color=RED, dash="dash"),
                  annotation_text="10% flag", annotation_position="top left")
    fig.update_layout(height=300, margin=dict(t=30, b=30, l=40, r=10),
                      plot_bgcolor="white", showlegend=False,
                      yaxis_title="|L − R| asymmetry (%)", xaxis_title="Date")
    return fig


def render_tab_force(player, avail):
    st.subheader("Force-plate jumps")
    st.caption("Click a point on the trend below to update the matrix and "
               "symmetry cell to that session.")
    if not avail["has_force_plate"]:
        insufficient_panel("force-plate", player)
        return
    fp_p = force_plate[force_plate["Player"] == player].copy()

    # Controls mirror the Workload tab, plus a foot selector and jump toggle.
    c1, c2, c3, c4, c5 = st.columns([1.1, 1.2, 1.1, 2.0, 1.3])
    agg = c1.selectbox("Aggregate by", ["Day", "Week", "Month"], key="agg2")
    type_filter = c2.selectbox("Session type", ["Both", "Game", "Practice"], key="type2")
    foot = c3.selectbox("Foot", ["Mean", "Left", "Right", "Both"], key="foot2")
    dmin, dmax = fp_p["Date"].min().date(), fp_p["Date"].max().date()
    dr = c4.slider("Date range", min_value=dmin, max_value=dmax,
                   value=(dmin, dmax), key="dr2")
    show_jump = c5.checkbox("Jump height bubbles", value=False, key="jump2")
    compare = c5.checkbox("Compare to team avg", key="cmp2")

    # Filter by type + date range; keep an unfiltered-by-date copy for detail.
    fpv = fp_p if type_filter == "Both" else fp_p[fp_p["Type"] == type_filter]
    lo, hi = pd.Timestamp(dr[0]), pd.Timestamp(dr[1])
    chart_view = fpv[(fpv["Date"] >= lo) & (fpv["Date"] <= hi)]
    team_view = None
    if compare:
        tv = force_plate if type_filter == "Both" else force_plate[force_plate["Type"] == type_filter]
        team_view = tv[(tv["Date"] >= lo) & (tv["Date"] <= hi)]

    if chart_view.empty:
        st.info("No sessions match the current filters.")
        return

    fig = force_timeseries(chart_view, agg, foot, show_jump, team_view)
    event = st.plotly_chart(fig, key="fp_chart", on_select="rerun",
                            use_container_width=True)
    read_click(event, "fp_date")
    # Selected session (default: latest) drives the matrix, symmetry cell & highlight.
    sel = pd.Timestamp(selected_date("fp_date") or fpv["Date"].max())

    # ── Below the trend: 2x2 force matrix (+ symmetry) and asymmetry chart ──
    st.divider()
    left, right = st.columns([1, 1.3])
    with left:
        sub = period_subset(fpv, sel, agg)
        st.markdown(force_matrix_html(sub, period_title(sel, agg)),
                    unsafe_allow_html=True)
        # Symmetrical / Asymmetrical cell for the selected session (jump height).
        jl = sub.loc[sub["Leg"] == "Left", "Jump_Height"].mean()
        jr = sub.loc[sub["Leg"] == "Right", "Jump_Height"].mean()
        if pd.notna(jl) and pd.notna(jr) and max(jl, jr) > 0:
            asym_pct = abs(jl - jr) / max(jl, jr) * 100
            deficit = asym_pct >= data.ASYMMETRY_THRESHOLD
            label = "Asymmetrical / Deficit" if deficit else "Symmetrical / Balanced"
            st.markdown("<br>" + pill(f"{label} · {asym_pct:.1f}%",
                        RED if deficit else GREEN), unsafe_allow_html=True)
            st.caption("Inter-limb jump-height difference is at/above the 10% flag."
                       if deficit else
                       "Inter-limb jump-height difference is within the 10% flag.")
    with right:
        st.markdown("**Inter-limb asymmetry over time**")
        asym_df = data.asymmetry_table(fp_p)   # full-season jump-height asymmetry
        if asym_df.empty:
            st.info("Not enough paired Left/Right data.")
        else:
            # Cyan-outline the dots in the selected period (they feed the cells).
            highlight = period_subset(asym_df, sel, agg)["Date"]
            st.plotly_chart(asymmetry_figure(asym_df, highlight_dates=highlight),
                            use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 · ACWR
# ═══════════════════════════════════════════════════════════════════════════
def stacked_balance(m, agg):
    """Workload vs force as stacked bars of the two 0-5 composite indices."""
    fig = go.Figure()
    # Workload first -> leftmost in the legend (it's the bottom of each stack).
    fig.add_bar(x=m["Date"], y=m["Load"], name="Workload index",
                marker_color=NAVY, legendrank=1,
                hovertemplate="Workload: %{y:.2f} / 5<extra></extra>")
    fig.add_bar(x=m["Date"], y=m["Force_index"], name="Force index",
                marker_color=GOLD, legendrank=2,
                hovertemplate="Force: %{y:.2f} / 5<extra></extra>")
    fig.update_layout(
        barmode="stack", height=440, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    traceorder="normal"),
        margin=dict(t=60, b=40, l=40, r=20), plot_bgcolor="white",
        yaxis_title="Composite Index", xaxis_title="Date")
    return fig


def acwr_figure(acwr, window):
    """ACWR over weeks with green/amber/red bands and the selected 4-week window."""
    ymax = float(np.nanmax([acwr["acwr"].max(), 1.6])) + 0.15
    bands = [(0, 0.80, RED), (0.80, 0.90, YELLOW), (0.90, 1.20, GREEN),
             (1.20, 1.30, YELLOW), (1.30, ymax, RED)]
    fig = go.Figure()
    for lo, hi, color in bands:
        fig.add_hrect(y0=lo, y1=hi, fillcolor=color, opacity=0.12,
                      line_width=0, layer="below")
    if window is not None:                    # shade the chosen 4-week window
        fig.add_vrect(x0=window[0], x1=window[1], fillcolor=BEALE, opacity=0.18,
                      line_width=0, annotation_text="4-week window",
                      annotation_position="top left")
    valid = acwr.dropna(subset=["acwr"])
    fig.add_scatter(x=valid.index, y=valid["acwr"], mode="lines+markers",
                    line=dict(color=NAVY, width=2), marker=dict(size=6, color=NAVY),
                    hovertemplate="<b>Week of %{x|%b %d}</b><br>ACWR: %{y:.2f}<extra></extra>")
    fig.update_layout(height=380, margin=dict(t=30, b=40, l=40, r=20),
                      plot_bgcolor="white", showlegend=False,
                      yaxis_title="Acute : Chronic Workload Ratio",
                      xaxis_title="Week", yaxis_range=[0, ymax])
    return fig


def render_tab_acwr(player, avail):
    if not avail["has_tracking"] and not avail["has_force_plate"]:
        insufficient_panel("tracking or force-plate", player)
        return
    trk_p = tracking[tracking["Player"] == player].copy()
    fsess_p = force_sessions[force_sessions["Player"] == player].copy()

    # ── Workload vs force balance (stacked bars of the two 0-3 indices) ──
    st.subheader("Workload vs. force balance")
    merged = (trk_p[["Date", "Load"]].merge(
        fsess_p[["Date", "Force_index"]], on="Date", how="outer").sort_values("Date"))
    merged["Type"] = merged["Date"].map(date_by_day)

    c1, c2, c3 = st.columns([1.1, 1.2, 2.4])
    agg = c1.selectbox("Aggregate by", ["Day", "Week", "Month"], key="agg3")
    type_filter = c2.selectbox("Session type", ["Both", "Game", "Practice"], key="type3")
    dmin, dmax = merged["Date"].min().date(), merged["Date"].max().date()
    dr = c3.slider("Date range", min_value=dmin, max_value=dmax,
                   value=(dmin, dmax), key="dr3")

    view = merged if type_filter == "Both" else merged[merged["Type"] == type_filter]
    lo, hi = pd.Timestamp(dr[0]), pd.Timestamp(dr[1])
    view = view[(view["Date"] >= lo) & (view["Date"] <= hi)]
    if agg != "Day":                          # indices are averaged per period
        view = (view.set_index("Date").resample(RULE[agg])[["Load", "Force_index"]]
                .mean().dropna(how="all").reset_index())
    st.plotly_chart(stacked_balance(view, agg), use_container_width=True)
    st.markdown("<span class='caption-sm'>Both bars are unitless 0-5 composite "
                "indices (0 = a player's easiest session, 5 = their hardest)."
                "</span>", unsafe_allow_html=True)
    if not avail["has_tracking"]:
        st.info("No workload data for this player, only the force data is shown.")
    if not avail["has_force_plate"]:
        st.info("No force-plate data for this player, only the workload data is shown.")

    # ── Acute : Chronic Workload Ratio ──
    st.divider()
    st.subheader("Acute : Chronic Workload Ratio (ACWR)")
    if not avail["has_tracking"]:
        st.info("ACWR is built from workload data, which this player doesn't have.")
        return
    acwr = data.build_acwr(data.weekly_load(trk_p))
    valid = acwr.dropna(subset=["acwr"])
    if valid.empty:
        st.info("Not enough weeks to form a 4-week chronic baseline yet.")
        return

    # A fixed 4-week window the user slides along the timeline: pick the END week,
    # the window is that week plus the prior 3 (exactly what 'chronic' averages).
    weeks = list(valid.index)
    end = st.select_slider(
        "Slide the 4-week window (pick its ending week):", options=weeks,
        value=weeks[-1], format_func=lambda d: pd.Timestamp(d).strftime("%b %d, %Y"))
    start = end - pd.Timedelta(weeks=3)
    st.plotly_chart(acwr_figure(acwr, (start, end)), use_container_width=True)

    r = acwr.loc[end]
    label, color = data.acwr_zone(r["acwr"])
    st.markdown(f"**4-week window: {start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}**")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Acute (window's last week)", f"{r['acute']:.2f}")
    m2.metric("Chronic (4-week average)", f"{r['chronic']:.2f}")
    m3.metric("ACWR", f"{r['acwr']:.2f}")
    m4.markdown("**Grade**")
    m4.markdown(pill(label, color), unsafe_allow_html=True)

    # Plain-language formulas for a non-technical reader.
    with st.expander("How these metrics are calculated"):
        st.markdown(
            "**Composite workload & force (0–5):** each raw metric is "
            "**min-max normalized** (*every data point is rescaled so a "
            "player's lowest value becomes 0 and their highest becomes 1*, or "
            "where a session sits between that player's easiest and hardest day. "
            "Each of the three metrics are then scaled so together they **add up to a "
            "maximum of 5** (0 = easy on all three, 5 = maxed all three). "
            "Per-player metrics keeps comparisons fair.")
        st.latex(r"Load_d=\sum_m \tfrac{5}{3}\cdot\frac{x_{m,d}-\min_m}{\max_m-\min_m}"
                 r"\quad(0\le Load_d\le 5)")
        st.markdown(
            "**Acute** = this week's total workload (recent fatigue). "
            "**Chronic** = the average weekly workload over the last 4 weeks "
            "(the fitness base the body is used to). **ACWR** = Acute / Chronic: "
            "how the recent load compares to what the athlete is conditioned for.")
        st.latex(r"Acute_w=\sum_{d\in w}Load_d \qquad "
                 r"Chronic_w=\tfrac{1}{4}\sum_{i=0}^{3}Acute_{w-i} \qquad "
                 r"ACWR_w=\frac{Acute_w}{Chronic_w}")
        st.markdown(
            "**Reading it:** ~**0.9–1.2** is the sweet spot 🟢. **0.8–0.9** "
            "or **1.2–1.3** is caution 🟡. **Below 0.8** (undertrained) or "
            "**above 1.3** (a sharp spike) is elevated injury risk 🔴.")


# ═══════════════════════════════════════════════════════════════════════════
# App body
# ═══════════════════════════════════════════════════════════════════════════
header()

with st.sidebar:
    if LOGO.exists():
        st.image(str(LOGO), width=64)
    st.markdown("### Player")
    player = st.selectbox("Select a player", roster, key="player",
                          label_visibility="collapsed")
    avail = data.player_availability(player, tracking, force_plate)
    st.markdown("**Data available**")
    st.markdown(
        pill("Tracking data" if avail["has_tracking"] else "No tracking",
             GREEN if avail["has_tracking"] else RED) + " " +
        pill("Force-plate data" if avail["has_force_plate"] else "No force plate",
             GREEN if avail["has_force_plate"] else RED),
        unsafe_allow_html=True)
    st.caption(f"{avail['n_tracking_sessions']} tracking sessions · "
               f"{avail['n_force_plate_dates']} force-plate dates")
    st.divider()
    st.caption("Built for Medical & Performance staff. Data spans the 2023-24 "
               "season (Oct–Jun).")

tab1, tab2, tab3 = st.tabs(["Workload", "Force Plate", "ACWR"])
with tab1:
    render_tab_workload(player, avail)
with tab2:
    render_tab_force(player, avail)
with tab3:
    render_tab_acwr(player, avail)
