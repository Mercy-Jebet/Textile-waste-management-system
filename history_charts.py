"""Turns the bins' fill-level history into a CSV and charts for the project report.

    python history_charts.py                 every bin, all history
    python history_charts.py --bin bin_001   one bin
    python history_charts.py --days 2        only the last 2 days

Reads readings/<bin_id> from Firebase (saved by app.py on every sensor reading and
every time a collector empties a bin) and writes into the reports/ folder:
    fill_history.csv        every point, for tables or your own analysis
    fill_<bin_id>.png       fill level over time: volume, weight and the 90% alert line
    summary.csv             per bin: readings, peak fill, times it reached 90%, times emptied
"""
import argparse
import os

import matplotlib

matplotlib.use("Agg")  # draw to files, no window needed
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

import app

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def load_history(only_bin=None, days=None):
    rows = []
    for bin_id, points in (app.store.get("readings") or {}).items():
        if only_bin and bin_id != only_bin:
            continue
        for p in (points or {}).values():
            rows.append({"bin_id": bin_id, **p})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Africa/Nairobi").dt.tz_localize(None)
    if days:
        df = df[df["time"] >= df["time"].max() - pd.Timedelta(days=days)]
    return df.sort_values(["bin_id", "time"]).reset_index(drop=True)


def chart_bin(bin_id, df):
    location = (app.store.get(f"bins/{bin_id}") or {}).get("location", bin_id)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(df["time"], df["volume_pct"], label="Volume (ultrasonic sensor)", color="#7c3aed", linewidth=1.8)
    ax.plot(df["time"], df["weight_pct"], label=f"Weight (% of {app.BIN_CAPACITY_KG:g} kg)", color="#db2777", linewidth=1.8)
    ax.axhline(app.ALERT_THRESHOLD, color="#dc2626", linestyle="--", linewidth=1, label=f"Alert at {app.ALERT_THRESHOLD}%")
    emptied = df[df["event"] == "emptied"]
    for t in emptied["time"]:
        ax.axvline(t, color="#6b7280", linestyle=":", linewidth=1)
    if not emptied.empty:
        ax.plot([], [], color="#6b7280", linestyle=":", label="Emptied by collector")
    ax.set_title(f"Fill level over time: {location} ({bin_id})")
    ax.set_ylabel("Fill level (%)")
    ax.set_ylim(0, 105)
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %H:%M"))
    fig.autofmt_xdate()
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    path = os.path.join(OUT_DIR, f"fill_{bin_id}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def times_reached_alert(df):
    """How many separate times the bin crossed the alert threshold."""
    above = df["fill_pct"] >= app.ALERT_THRESHOLD
    return int((above & ~above.shift(fill_value=False)).sum())


def main():
    parser = argparse.ArgumentParser(description="Make fill-level charts for the report")
    parser.add_argument("--bin", help="only this bin, e.g. bin_001")
    parser.add_argument("--days", type=float, help="only the last N days")
    args = parser.parse_args()

    df = load_history(args.bin, args.days)
    if df.empty:
        raise SystemExit("No fill-level history yet. Run the bin (or smart-bin/simulator.py) for a while first.")
    os.makedirs(OUT_DIR, exist_ok=True)
    df.drop(columns=["ts"]).to_csv(os.path.join(OUT_DIR, "fill_history.csv"), index=False)

    summary = []
    for bin_id, group in df.groupby("bin_id"):
        print("Saved", chart_bin(bin_id, group))
        summary.append({
            "bin_id": bin_id,
            "readings": int((group["event"] == "reading").sum()),
            "from": group["time"].min(),
            "to": group["time"].max(),
            "peak_fill_pct": group["fill_pct"].max(),
            "peak_weight_kg": group["weight_kg"].max(),
            "times_reached_alert": times_reached_alert(group),
            "times_emptied": int((group["event"] == "emptied").sum()),
        })
    pd.DataFrame(summary).to_csv(os.path.join(OUT_DIR, "summary.csv"), index=False)
    print(pd.DataFrame(summary).to_string(index=False))
    print("CSV files and charts are in", OUT_DIR)


if __name__ == "__main__":
    main()
