"""
data.py — data loading, cleaning, and metric engineering for the Grizzlies
Performance & Load Monitoring dashboard.

This module is deliberately kept free of any Streamlit code so it can be
imported, unit-tested, and reasoned about on its own. The Streamlit app
(app.py) is responsible for caching and presentation.

Cleaning decisions (agreed with stakeholder):
  * The leading unnamed CSV column is a redundant row-id -> loaded as the index.
  * Dates are stored as M/D/YY strings -> parsed to real datetimes.
  * Impossible values (negative event counts, e.g. High_Decel == -1) are
    dropped outright — a single fraudulent row does not move the analysis.
  * The two source tables have DIFFERENT rosters (see get_roster); players
    missing from a table are handled in the UI with an "Insufficient Player
    Data" panel rather than by imputing an entire fake history.
"""

from pathlib import Path
import numpy as np
import pandas as pd

# ── Constants ────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent

TRACK_METRICS = ["High_Accel", "High_Decel", "Distance"]
FP_METRICS = ["Peak_Eccentric_Force", "Peak_Concentric_Force", "Jump_Height"]

# How each aggregation level maps to a pandas resample rule.
FREQ_RULE = {"Day": "D", "Week": "W", "Month": "ME"}

# Limb Symmetry Index flag threshold (%). >= this is flagged as a deficit.
ASYMMETRY_THRESHOLD = 10.0

# Number of weeks used for the ACWR "chronic" (rolling average) window.
CHRONIC_WEEKS = 4


# ── Loading + cleaning ───────────────────────────────────────────────────
def load_and_clean(data_dir: Path = DATA_DIR):
    """Load the three CSVs, clean them, and return them ready for the app.

    Returns
    -------
    tracking : DataFrame  (Player-day, with 'Type' and composite 'Load' added)
    force_plate : DataFrame  (Player-day-leg, with 'Type' added)
    schedule : DataFrame  (Date -> Game/Practice)
    """
    tracking = pd.read_csv(data_dir / "tracking.csv", index_col=0)
    force_plate = pd.read_csv(data_dir / "force_plate.csv", index_col=0)
    schedule = pd.read_csv(data_dir / "schedule.csv", index_col=0)

    # Parse the M/D/YY date strings into real datetimes in every table.
    for df in (tracking, force_plate, schedule):
        df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%y")

    # Drop impossible event counts (an event count can never be negative).
    bad = (tracking["High_Accel"] < 0) | (tracking["High_Decel"] < 0)
    tracking = tracking.loc[~bad].copy()

    # Attach the Game/Practice context to both measurement tables.
    day_type = schedule[["Date", "Type"]]
    tracking = tracking.merge(day_type, on="Date", how="left")
    force_plate = force_plate.merge(day_type, on="Date", how="left")

    # Deterministic ordering makes rolling/time operations safe & predictable.
    tracking = tracking.sort_values(["Player", "Date"]).reset_index(drop=True)
    force_plate = (force_plate
                   .sort_values(["Player", "Date", "Leg"])
                   .reset_index(drop=True))

    # Engineer the composite workload score used by the ACWR module.
    tracking = add_composite_load(tracking)

    return tracking, force_plate, schedule


def composite_force_table(force_plate: pd.DataFrame) -> pd.DataFrame:
    """Per (player, date) composite 'Force_index' (0-5), same recipe as Load.

    Left/Right are averaged per date, then each of the three force-plate metrics
    is min-max normalized within the player's season, summed, and rescaled to
    0-5 — the identical scale as the workload Load.
    """
    per = force_plate.groupby(["Player", "Date"])[FP_METRICS].mean().reset_index()
    norm_cols = []
    for col in FP_METRICS:
        grp = per.groupby("Player")[col]
        lo = grp.transform("min")
        span = grp.transform("max") - lo
        per[f"{col}_norm"] = ((per[col] - lo) / span.replace(0, np.nan)).fillna(0.0)
        norm_cols.append(f"{col}_norm")
    # Scale each 0-1 value by 5/3 so the three add up to a 0-5 index.
    per["Force_index"] = (per[norm_cols] * (5 / 3)).sum(axis=1)
    return per


def add_composite_load(tracking: pd.DataFrame) -> pd.DataFrame:
    """Add a composite 'Load' column (0-5) to the tracking table.

    Each of the three workload metrics is min-max normalized to 0-1 *within
    each player's own season*, the three are summed, then rescaled to 0-5.
    Per-player scaling means every metric contributes relative to that
    player's personal range, and the score is comparable across players.
    """
    df = tracking.copy()
    norm_cols = []
    for col in TRACK_METRICS:
        grp = df.groupby("Player")[col]
        lo = grp.transform("min")
        span = grp.transform("max") - lo
        # Guard against a constant metric (span == 0) -> contributes 0.
        norm = ((df[col] - lo) / span.replace(0, np.nan)).fillna(0.0)
        ncol = f"{col}_norm"
        df[ncol] = norm
        norm_cols.append(ncol)
    # Scale each 0-1 value by 5/3 so the three add up to a 0-5 index.
    df["Load"] = (df[norm_cols] * (5 / 3)).sum(axis=1)
    return df


# ── Roster / availability helpers ────────────────────────────────────────
def get_roster(tracking: pd.DataFrame, force_plate: pd.DataFrame) -> list[str]:
    """Full player list = the UNION of both tables, so nobody is dropped."""
    players = set(tracking["Player"]) | set(force_plate["Player"])
    return sorted(players)


def player_availability(player: str, tracking: pd.DataFrame,
                        force_plate: pd.DataFrame) -> dict:
    """What data exists for a player, used to drive graceful missing-data UI."""
    trk = tracking[tracking["Player"] == player]
    fp = force_plate[force_plate["Player"] == player]
    return {
        "has_tracking": len(trk) > 0,
        "has_force_plate": len(fp) > 0,
        "n_tracking_sessions": int(len(trk)),
        "n_force_plate_dates": int(fp["Date"].nunique()),
    }


# ── Time-series aggregation (day / week / month) ─────────────────────────
def resample_player(df_player: pd.DataFrame, freq_label: str,
                    agg_map: dict) -> pd.DataFrame:
    """Resample one player's rows to Day/Week/Month using agg_map.

    Empty periods (no sessions) are dropped so charts don't show phantom
    zero-bars. 'Day' simply returns the per-day rows unchanged.
    """
    rule = FREQ_RULE[freq_label]
    out = (df_player.set_index("Date")
           .resample(rule)
           .agg(agg_map)
           .dropna(how="all")
           .reset_index())
    return out


# ── ACWR (Acute:Chronic Workload Ratio) ──────────────────────────────────
def weekly_load(tracking_player: pd.DataFrame) -> pd.Series:
    """Total composite Load per calendar week for one player."""
    return (tracking_player.set_index("Date")["Load"]
            .sort_index()
            .resample("W")
            .sum())


def build_acwr(weekly: pd.Series, chronic_weeks: int = CHRONIC_WEEKS) -> pd.DataFrame:
    """Rolling-average ACWR from a weekly-load series.

    acute   = this week's total load
    chronic = average weekly load over the trailing `chronic_weeks` weeks
    acwr    = acute / chronic   (undefined until `chronic_weeks` weeks exist)
    """
    df = weekly.rename("acute").to_frame()
    df["chronic"] = df["acute"].rolling(chronic_weeks, min_periods=chronic_weeks).mean()
    df["acwr"] = (df["acute"] / df["chronic"]).replace([np.inf, -np.inf], np.nan)
    return df


def acwr_zone(value: float) -> tuple[str, str]:
    """Map an ACWR value to a (label, hex-color) risk zone.

    Green 0.90-1.20 | Yellow 0.80-0.90 & 1.20-1.30 | Red <0.80 or >1.30.
    (Intentionally more conservative than the >1.5 'danger' line in the
    literature, per stakeholder preference.)
    """
    if pd.isna(value):
        return ("Building baseline", "#707271")
    if value < 0.80:
        return ("Under-loading (elevated risk)", "#CB3234")
    if value < 0.90:
        return ("Caution — low", "#E7B416")
    if value <= 1.20:
        return ("Sweet spot", "#2E9E5B")
    if value <= 1.30:
        return ("Caution — high", "#E7B416")
    return ("Danger — spike (high risk)", "#CB3234")


# ── Force-plate asymmetry (Limb Symmetry Index) ──────────────────────────
def asymmetry_table(fp_player: pd.DataFrame, metric: str = "Jump_Height") -> pd.DataFrame:
    """One row per date with Left, Right, |asymmetry| %, and a category label.

    Asymmetry % is expressed relative to the stronger limb (a common LSI form).
    """
    wide = fp_player.pivot_table(index="Date", columns="Leg", values=metric)
    wide = wide.dropna(subset=[c for c in ("Left", "Right") if c in wide.columns])
    if not {"Left", "Right"}.issubset(wide.columns) or wide.empty:
        return pd.DataFrame(columns=["Left", "Right", "asym_pct", "category"])
    stronger = wide[["Left", "Right"]].max(axis=1)
    wide["asym_pct"] = (wide["Left"] - wide["Right"]).abs() / stronger * 100
    wide["category"] = np.where(
        wide["asym_pct"] >= ASYMMETRY_THRESHOLD,
        "Asymmetrical / Deficit",
        "Symmetrical / Balanced",
    )
    return wide.reset_index()
