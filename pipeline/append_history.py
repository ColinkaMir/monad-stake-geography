#!/usr/bin/env python3
"""Append a compact daily snapshot of the geo aggregates to a rolling history
file, for the time-series charts on prooflines.org/monad/geo-latency.

Reads the just-built PUBLIC feed (geo-latency-data*.json) and stores ONLY the
same aggregate sections it already publishes (continents / top countries / top
providers) — never per-validator data, so this stays inside the same opsec
boundary as the feed itself.

Granularity is one snapshot per UTC day: re-running within the same day REPLACES
that day's entry (keeps the latest), so the cron's 30-min cadence does not bloat
the file. History is pruned to GEO_HIST_DAYS days.

Env:
  GEO_HIST_SRC   path to the built public feed (required)
  GEO_HIST_OUT   history file path (default: SRC with geo-latency-data ->
                 geo-latency-history)
  GEO_HIST_DAYS  retention window in days (default 120)
"""
import json
import os
import time

SRC = os.getenv("GEO_HIST_SRC")
if not SRC:
    raise SystemExit("GEO_HIST_SRC is required")
OUT = os.getenv("GEO_HIST_OUT") or SRC.replace("geo-latency-data", "geo-latency-history")
DAYS = int(os.getenv("GEO_HIST_DAYS", "120"))

TOP_COUNTRIES = 10
TOP_PROVIDERS = 8


def main():
    d = json.load(open(SRC))
    day = time.strftime("%Y-%m-%d", time.gmtime())

    cov = d.get("coverage") or {}
    snap = {
        "date": day,
        "ts": int(time.time()),
        "epoch": d.get("epoch"),
        "total_validators": cov.get("total_validators") or cov.get("geo_located"),
        "continents": [
            {"name": c["name"], "count": c["count"],
             "count_pct": c["count_pct"], "stake_pct": c["stake_pct"]}
            for c in d.get("continents", [])
        ],
        "countries": [
            {"cc": c["cc"], "name": c["name"],
             "count": c["count"], "stake_pct": c["stake_pct"]}
            for c in d.get("countries", [])[:TOP_COUNTRIES]
        ],
        "providers": [
            {"name": p["name"], "count": p["count"], "stake_pct": p["stake_pct"]}
            for p in d.get("providers", [])[:TOP_PROVIDERS]
        ],
    }

    try:
        hist = json.load(open(OUT))
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []

    # Replace today's entry if present (keep the latest run of the day), else append.
    hist = [h for h in hist if h.get("date") != day]
    hist.append(snap)
    hist.sort(key=lambda h: h.get("date", ""))

    # Prune to the retention window by count of distinct days.
    if len(hist) > DAYS:
        hist = hist[-DAYS:]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    json.dump(hist, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"history: {len(hist)} day(s) -> {OUT} (added/updated {day})")


if __name__ == "__main__":
    main()
