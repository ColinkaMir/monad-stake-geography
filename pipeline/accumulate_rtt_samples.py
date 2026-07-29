#!/usr/bin/env python3
"""Accumulate per-epoch RTT rounds and emit averaged measurements.

measure_rtt.py writes one round (rtt-measurements.json). This appends that
round to rtt-epoch-samples.json (reset when the epoch changes) and writes
rtt-averaged.json in the shape build_public_json.py expects, with avg_ms
averaged across all rounds taken this epoch.

Usage: accumulate_rtt_samples.py <epoch>
"""
import json
import os
import sys
import time

DIR = os.getenv("GEO_ACC_DIR", "/home/admin/monad-knowledge-base/tools/geo-latency")
ROUND = f"{DIR}/rtt-measurements.json"
SAMPLES = f"{DIR}/rtt-epoch-samples.json"
OUT = f"{DIR}/rtt-averaged.json"
# Defensive RTT sanity floor (mirrors measure_rtt.py): sub-floor avg_ms values
# are router ICMP-error artifacts, not real remote latencies. Drop them so they
# never enter the per-epoch samples or the averaged feed.
RTT_MIN_MS = float(os.getenv("GEO_RTT_MIN_MS", "0"))


def main():
    epoch = int(sys.argv[1])
    rnd = json.load(open(ROUND))
    sample = {
        "ts": int(time.time()),
        "avg_ms": {ip: m["avg_ms"] for ip, m in rnd["measurements"].items()
                   if m.get("avg_ms") is not None and m["avg_ms"] >= RTT_MIN_MS},
    }
    try:
        acc = json.load(open(SAMPLES))
    except Exception:
        acc = {"epoch": None, "samples": []}
    if acc.get("epoch") != epoch:
        acc = {"epoch": epoch, "samples": []}
    acc["samples"].append(sample)
    json.dump(acc, open(SAMPLES, "w"))

    ips = set()
    for s in acc["samples"]:
        ips.update(s["avg_ms"])
    measurements = {}
    for ip in sorted(ips):
        # Defensive floor again here so any sub-floor value carried in older
        # samples this epoch is scrubbed before averaging.
        vals = [s["avg_ms"][ip] for s in acc["samples"]
                if ip in s["avg_ms"] and s["avg_ms"][ip] >= RTT_MIN_MS]
        if not vals:
            continue
        measurements[ip] = {"avg_ms": round(sum(vals) / len(vals), 2),
                            "rounds": len(vals)}
    out = {
        "generated_at_epoch": int(time.time()),
        "epoch": epoch,
        "rounds": len(acc["samples"]),
        "total_ips": rnd.get("total_ips"),
        "reachable_ips": sum(1 for ip in measurements if ip in rnd.get("measurements", {})),
        "measurements": measurements,
    }
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"rtt rounds this epoch: {len(acc['samples'])} | averaged IPs: {len(measurements)}")


if __name__ == "__main__":
    main()
