#!/usr/bin/env python3
"""Build the PUBLIC geo-latency JSON for the map frontend.

Contains ONLY aggregates (continent / country / ASN), stake-weighted, plus
stake-weighted-median ICMP RTT from the vantage where reachable. NEVER emits
per-validator IP / name / city. The opsec boundary lives here.

Inputs:  validators-geoip.json, rtt-averaged.json
Output:  ./site/geo-latency-data.json
"""
import json
import os
import time
from collections import defaultdict

GEO = os.getenv("GEO_BUILD_GEO", "./data/validators-geoip.json")
RTT = os.getenv("GEO_BUILD_RTT", "./data/rtt-averaged.json")
OUT = os.getenv("GEO_BUILD_OUT", "./site/geo-latency-data.json")
# Defensive RTT sanity floor (mirrors measure_rtt.py / accumulate_rtt_samples.py):
# any avg_ms below this is a router ICMP-error artifact, not a real remote hop.
# Treat such a host as having no valid RTT so it pollutes neither the aggregates
# nor the per-point map RTT.
RTT_MIN_MS = float(os.getenv("GEO_RTT_MIN_MS", "0"))
# Drop map points that have no valid RTT (they render as broken grey "n/a" dots).
# Off by default so a testnet feed's map is unchanged; enable it explicitly for
# the mainnet refresh.
DROP_NULL_RTT_MAP = os.getenv("GEO_MAP_DROP_NULL_RTT", "0") not in ("0", "", "false", "False")

EU = {"DE","FI","PL","FR","NL","GB","IE","SE","NO","DK","ES","IT","PT","AT",
      "CH","BE","CZ","RO","BG","HU","SK","LT","LV","EE","GR","HR","SI","LU",
      "UA","RS","MD","IS","CY","MT"}
NA = {"US","CA","MX"}
APAC = {"JP","SG","KR","HK","TW","AU","IN","ID","TH","VN","MY","PH","NZ","CN"}


def continent(cc):
    if cc in EU: return "EU"
    if cc in NA: return "NA"
    if cc in APAC: return "APAC"
    return "Other"


def wmedian(pairs):
    if not pairs:
        return None
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return round(v)
    return round(pairs[-1][0])


def main():
    gdata = json.load(open(GEO))
    geo = gdata["validators"]
    rtt = json.load(open(RTT))
    rttm = rtt["measurements"]

    vs = [v for v in geo if v.get("geo") and v.get("stake_wei")]
    total_stake = sum(v["stake_wei"] for v in vs)
    total_count = len(vs)

    def valid_rtt(v):
        """Return the sanity-checked avg RTT for a validator's IP, or None if
        it is unreachable or a sub-floor artifact."""
        r = rttm.get(v.get("ip"))
        if not r:
            return None
        ms = r.get("avg_ms")
        if ms is None or ms < RTT_MIN_MS:
            return None
        return ms

    def buckets(keyfn):
        stake = defaultdict(int)
        cnt = defaultdict(int)
        rtts = defaultdict(list)
        for v in vs:
            k = keyfn(v)
            stake[k] += v["stake_wei"]
            cnt[k] += 1
            ms = valid_rtt(v)
            if ms is not None:
                rtts[k].append((ms, v["stake_wei"]))
        return stake, cnt, rtts

    # Continents
    cs, cc_, cr = buckets(lambda v: continent(v["geo"].get("country_code", "")))
    continents = []
    for name in ("EU", "NA", "APAC", "Other"):
        continents.append({
            "name": name,
            "stake_pct": round(100 * cs[name] / total_stake, 2),
            "count": cc_[name],
            "count_pct": round(100 * cc_[name] / total_count, 1),
            "rtt_ms": wmedian(cr[name]),
        })

    # Countries (top 12 by stake)
    ks, kc, kr = buckets(lambda v: (v["geo"].get("country", "?"),
                                    v["geo"].get("country_code", "?")))
    # Per-country provider (ASN) stake, to surface in-country concentration.
    cprov = defaultdict(lambda: defaultdict(int))
    for v in vs:
        ckey = (v["geo"].get("country", "?"), v["geo"].get("country_code", "?"))
        asn = v["geo"].get("as", "?") or "?"
        short = asn.split(" ", 1)[1] if asn.startswith("AS") and " " in asn else asn
        cprov[ckey][short] += v["stake_wei"]
    countries = []
    for (name, cc), s in sorted(ks.items(), key=lambda kv: kv[1], reverse=True)[:12]:
        pv = sorted(cprov[(name, cc)].items(), key=lambda kv: kv[1], reverse=True)
        tp_name, tp_stake = pv[0]
        countries.append({
            "name": name,
            "cc": cc,
            "stake_pct": round(100 * s / total_stake, 2),
            "count": kc[(name, cc)],
            "rtt_ms": wmedian(kr[(name, cc)]),
            "top_provider": tp_name,
            "top_provider_pct": round(100 * tp_stake / s, 1),
            "provider_count": len(pv),
        })

    # Providers / ASN (top 12 by stake) with cumulative
    asns, asc, asr = buckets(lambda v: v["geo"].get("as", "?") or "?")
    providers = []
    cum = 0.0
    for name, s in sorted(asns.items(), key=lambda kv: kv[1], reverse=True)[:12]:
        pct = round(100 * s / total_stake, 2)
        cum = round(cum + pct, 2)
        short = name.split(" ", 1)[1] if name.startswith("AS") and " " in name else name
        providers.append({
            "name": short,
            "asn": name.split(" ", 1)[0] if name.startswith("AS") else "",
            "stake_pct": pct,
            "cum_pct": cum,
            "count": asc[name],
            "rtt_ms": wmedian(asr[name]),
        })

    # Map points: aggregate by (country_code, city). Location-level only,
    # never per-validator. Each point carries count, stake share, RTT, and a
    # provider breakdown within the location.
    loc = {}
    for v in vs:
        g = v["geo"]
        lat, lon = g.get("lat"), g.get("lon")
        if lat is None or lon is None:
            continue
        key = (g.get("country_code", "?"), g.get("city", "?"))
        d = loc.setdefault(key, {
            "city": g.get("city", "?"),
            "country": g.get("country", "?"),
            "cc": g.get("country_code", "?"),
            "lat": lat, "lon": lon,
            "stake": 0, "count": 0,
            "rtt_pairs": [], "prov": defaultdict(lambda: {"stake": 0, "count": 0}),
        })
        d["stake"] += v["stake_wei"]
        d["count"] += 1
        ms = valid_rtt(v)
        if ms is not None:
            d["rtt_pairs"].append((ms, v["stake_wei"]))
        asn = g.get("as", "?") or "?"
        short = asn.split(" ", 1)[1] if asn.startswith("AS") and " " in asn else asn
        d["prov"][short]["stake"] += v["stake_wei"]
        d["prov"][short]["count"] += 1

    map_points = []
    excluded_null_rtt = 0
    for d in loc.values():
        rtt_ms = wmedian(d["rtt_pairs"])
        # Optionally exclude locations with no valid RTT from the RTT-colored map
        # layer: they would otherwise render as broken/uncolored (grey "n/a") dots.
        if rtt_ms is None and DROP_NULL_RTT_MAP:
            excluded_null_rtt += 1
            continue
        provs = sorted(d["prov"].items(), key=lambda kv: kv[1]["stake"], reverse=True)
        map_points.append({
            "city": d["city"],
            "country": d["country"],
            "cc": d["cc"],
            "lat": round(d["lat"], 4),
            "lon": round(d["lon"], 4),
            "count": d["count"],
            "stake_pct": round(100 * d["stake"] / total_stake, 2),
            "rtt_ms": rtt_ms,
            "providers": [
                {"name": name,
                 "count": p["count"],
                 "loc_pct": round(100 * p["stake"] / d["stake"], 1)}
                for name, p in provs[:4]
            ],
        })
    map_points.sort(key=lambda p: p["stake_pct"], reverse=True)

    # Headline numbers
    top4 = round(sum(p["stake_pct"] for p in providers[:4]), 2)
    # Top-2 providers by stake, generic (no hardcoded provider names — the frontend labels
    # them dynamically from providers[0..1], so this stays correct as the ranking rotates).
    top2 = round(sum(p["stake_pct"] for p in providers[:2]), 2)

    out = {
        "generated_at_epoch": int(time.time()),
        "generated_at_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "epoch": vs[0].get("epoch") if vs else None,
        "vantage": os.getenv("GEO_VANTAGE", "vantage"),
        "vantage_point": {
            "lat": float(os.getenv("GEO_VANTAGE_LAT", "35.6762")),
            "lon": float(os.getenv("GEO_VANTAGE_LON", "139.6503")),
            "label": os.getenv("GEO_VANTAGE_LABEL", "vantage"),
        },
        "rtt_method": "ICMP echo, 5 packets/host, averaged over all rounds this epoch, stake-weighted median",
        "coverage": {
            "total_validators": gdata.get("total_validators"),
            "geo_located": total_count,
            "rtt_reachable_ips": rtt.get("reachable_ips"),
            "rtt_total_ips": rtt.get("total_ips"),
            "rtt_rounds": rtt.get("rounds"),
        },
        "headline": {
            "top4_provider_stake_pct": top4,
            "top2_provider_stake_pct": top2,
            "bft_threshold_pct": 33.3,
        },
        "continents": continents,
        "countries": countries,
        "providers": providers,
        "map_points": map_points,
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"wrote {OUT}")
    print(f"  continents={len(continents)} countries={len(countries)} providers={len(providers)} map_points={len(map_points)} (excluded {excluded_null_rtt} null-RTT locations)")
    print(f"  top4={top4}% top2={top2}%")


if __name__ == "__main__":
    main()
