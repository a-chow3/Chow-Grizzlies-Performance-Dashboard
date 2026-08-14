# Grizzlies Performance Dashboard

Created by: Adam Chow

A Streamlit dashboard that exposes and visualizes player workload and
force-plate data for **Medical & Performance practitioners** (physical
therapists, trainers, strength coaches).

## What it shows
Three full-screen sub-tabs, navigable per player:

1. **Workload** — high-intensity accelerations + decelerations per session as
   bars (dark blue = Game, light blue = Practice), with **distance encoded as
   bubble size** and mean-accel / mean-decel reference lines. Day / Week / Month
   aggregation, date-range and session-type filters, and an optional
   team-average comparison.
2. **Force Plate** — Left vs. Right jumps for jump height and peak
   eccentric/concentric force, a **limb-diagram**, and an **inter-limb
   asymmetry** screen (Balanced <10% / Deficit ≥10%).
3. **Combined & ACWR** — composite workload vs. jump-height trend, plus the
   **Acute:Chronic Workload Ratio** with color-coded risk zones, a per-week
   grade, and a formulas reference.

Missing data is handled gracefully: players absent from a table (the two source
files have different rosters) get an **"Insufficient Player Data"** panel
instead of an error.

## Project structure
```
├── app.py              # Streamlit UI (all layout + charts)
├── data.py             # loading, cleaning, and metric engineering (no Streamlit)
├── eda.ipynb           # exploratory analysis behind the design decisions
├── tracking.csv        # workload data (player-day)
├── force_plate.csv     # jump data (player-day-leg)
├── schedule.csv        # Game/Practice per date
├── assets/             # Grizzlies logo
├── requirements.txt
└── .streamlit/config.toml   # theme
```

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
Then open the local URL Streamlit prints (http://localhost:8501).

## Deploy (public link)

The app is also currently live at this link, (https://chow-grizzlies-performance-dashboard.streamlit.app/), for anyone to view immediately, without logins or downloading files.
