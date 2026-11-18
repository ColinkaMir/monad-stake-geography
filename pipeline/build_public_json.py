#!/usr/bin/env python3
"""Build the PUBLIC geo-latency JSON for prooflines.org/monad/geo-latency.

Contains ONLY aggregates (continent / country / ASN), stake-weighted, plus
stake-weighted-median ICMP RTT from the JP vantage where reachable. NEVER
emits per-validator IP / name / city. Opsec boundary lives here.

Inputs:  validators-geoip.json, rtt-measurements.json
Output:  ../outputs/proofline-public-live/geo-latency/geo-latency-data.json
"""
import json
import os
import re
import time
from collections import defaultdict

GEO = os.getenv("GEO_BUILD_GEO", "validators-geoip.json")
RTT = os.getenv("GEO_BUILD_RTT", "rtt-averaged.json")
OUT = os.getenv("GEO_BUILD_OUT", "/home/admin/monad-knowledge-base/outputs/proofline-public-live/geo-latency/geo-latency-data.json")
# Defensive RTT sanity floor (mirrors measure_rtt.py / accumulate_rtt_samples.py):
# any avg_ms below this is a router ICMP-error artifact, not a real remote hop.
# Treat such a host as having no valid RTT so it pollutes neither the aggregates
# nor the per-point map RTT.
RTT_MIN_MS = float(os.getenv("GEO_RTT_MIN_MS", "0"))
# Drop map points that have no valid RTT (they render as broken grey "n/a" dots).
# Off by default so the testnet feed's map is unchanged; the mainnet refresh
# enables it explicitly.
DROP_NULL_RTT_MAP = os.getenv("GEO_MAP_DROP_NULL_RTT", "0") not in ("0", "", "false", "False")
# Optional: agreement between our off-node crawl and the Foundation's own
# published validator dataset, produced by crosscheck_foundation.py. Included
# verbatim when present so the page can state how checkable its own inputs are.
CROSSCHECK = os.getenv("GEO_BUILD_CROSSCHECK", "")
# Optional: operator names, taken from the Foundation's own public dataset. We
# publish name + location + host and never the IP address: the identity is
# already theirs to disclose, the address stays on their side of the line.
NAMES_FILE = os.getenv("GEO_BUILD_NAMES", "")

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


def _short(name):
    """First token of a provider/ASN name for compact signal text."""
    return str(name or "").split(",")[0].split(" (")[0].strip()


# ── operator grouping ────────────────────────────────────────────────────────
# One company can hold several ASNs, so bucketing by ASN understates how much
# stake sits behind a single decision-maker. Cherry Servers holds six ASNs on
# Monad mainnet: read by ASN the top four providers are ~51% of stake, read by
# operator they are ~56%. The VDP scores ISP/ASN concentration, so the operator
# view is the one that has to be right.
#
# The mapping is deliberately boring and auditable: strip legal-form tokens and
# punctuation from the ASN holder name, and keep an explicit override list for
# the cases where that is not enough. Both the grouping and the ASN members of
# every group are published in the feed so anyone can check the joins.
LEGAL_FORMS = {
    "inc", "llc", "ltd", "limited", "gmbh", "bv", "sas", "sa", "ab", "oy",
    "srl", "uab", "ltda", "pty", "jsc", "plc", "ug", "kg", "as", "corp",
    "corporation", "company", "co", "spzoo", "ooo", "llp", "kft", "doo",
}
# key (after normalisation) -> canonical operator name. Only for joins that
# name-normalisation alone cannot make, each one checked by hand.
# NOTE: keys and values are ALREADY-NORMALISED forms (lowercase, punctuation
# split out), because the override is applied after normalisation. Writing
# "velia.net internetdienste" here would never match, since the dot is split.
OPERATOR_OVERRIDES = {
    "velia net internetdienste": "velia net",
}


def _norm_operator_key(as_name):
    """Normalised join key for an ASN holder name."""
    name = re.sub(r"^AS\d+\s+", "", str(as_name or "").strip())
    name = name.replace("&", " and ")
    tokens = [t for t in re.split(r"[\s,./\"'()]+", name.lower()) if t]
    kept = [t for t in tokens if t not in LEGAL_FORMS]
    key = " ".join(kept) or " ".join(tokens)
    return OPERATOR_OVERRIDES.get(key, key)


def _holder(as_name):
    """The ASN holder name without the leading ASxxxxx."""
    return re.sub(r"^AS\d+\s+", "", str(as_name or "?").strip()) or "?"


def _asn_of(as_name):
    m = re.match(r"^(AS\d+)\b", str(as_name or "").strip())
    return m.group(1) if m else ""


def compute_signals(continents, countries, providers, headline):
    """Derive narrative decentralization signals from the current aggregates.

    Pure function over the already-public aggregate sections (continents,
    countries, providers, headline) — NEVER touches per-validator data, so it
    stays inside the same opsec boundary as the rest of this feed. Deterministic,
    history-free: every signal is a fact about THIS snapshot. Returns a list of
    {level, text} sorted by severity (critical > warn > info > good), capped.
    """
    sig = []
    bft = headline.get("bft_threshold_pct", 33.3)

    # 1) Provider concentration — emit the single strongest read. Keep the page's
    # BFT framing: the 33% line applies to ONE correlated-failure domain (a single
    # provider), so only a lone provider crossing it is a "critical". Top-2/top-4
    # are concentration DEPTH context, never phrased as crossing the BFT line.
    if providers:
        p0 = providers[0]
        top4 = headline.get("top4_provider_stake_pct", 0)
        # depth: how many providers combine to first reach 1/3 of stake
        depth = len(providers)
        for i, p in enumerate(providers):
            if p.get("cum_pct", 0) >= bft:
                depth = i + 1
                break
        if p0["stake_pct"] >= bft:
            sig.append({"level": "critical",
                "text": _short(p0["name"]) + " alone hosts " + str(p0["stake_pct"])
                        + "% of stake — past the " + str(bft)
                        + "% BFT line, so one provider failure could stall consensus."})
        elif depth <= 3:
            sig.append({"level": "warn",
                "text": "Just " + str(depth) + " providers combine to reach 1/3 of stake ("
                        + _short(p0["name"]) + " leads at " + str(p0["stake_pct"])
                        + "%) — shallow provider diversity, though no single host crosses the "
                        + str(bft) + "% line."})
        elif top4 >= 50:
            sig.append({"level": "info",
                "text": "Top 4 providers control " + str(top4)
                        + "% of stake — provider diversity is shallower than the country map suggests."})

    # 2) Largest correlated-failure geography (region holding a majority of nodes).
    if continents:
        top_cont = max(continents, key=lambda c: c["count_pct"])
        if top_cont["count_pct"] >= 50:
            sig.append({"level": "warn",
                "text": top_cont["name"] + " holds " + str(top_cont["count_pct"])
                        + "% of nodes and " + str(top_cont["stake_pct"])
                        + "% of stake — the network's largest geographic failure domain."})

    # 3) Under-represented real region — a decentralization growth area.
    real = [c for c in continents if c["name"] in ("EU", "NA", "APAC") and c["count"] > 0]
    if real:
        low = min(real, key=lambda c: c["count_pct"])
        if low["count_pct"] < 12:
            sig.append({"level": "info",
                "text": low["name"] + " is thin at " + str(low["count_pct"]) + "% of nodes ("
                        + str(low["count"]) + ") — a decentralization growth area."})

    # 4) Country-level single-provider dependence (worst two with real presence).
    dep = [c for c in countries if c["count"] >= 3 and c["top_provider_pct"] >= 60]
    dep.sort(key=lambda c: (c["top_provider_pct"], c["stake_pct"]), reverse=True)
    for c in dep[:2]:
        lvl = "warn" if c["top_provider_pct"] >= 80 else "info"
        sig.append({"level": lvl,
            "text": c["name"] + " leans on one host — " + _short(c["top_provider"])
                    + " holds " + str(c["top_provider_pct"]) + "% of its stake across "
                    + str(c["count"]) + " nodes."})

    # 5) Latency outlier — OUR unique angle (BitCtrl's geo map has no latency layer).
    lat = [c for c in countries
           if c.get("rtt_ms") is not None and c["rtt_ms"] >= 200 and c["stake_pct"] >= 3]
    lat.sort(key=lambda c: c["rtt_ms"], reverse=True)
    if lat:
        c = lat[0]
        sig.append({"level": "info",
            "text": c["name"] + " sits " + str(c["rtt_ms"]) + " ms from the vantage with "
                    + str(c["stake_pct"]) + "% of stake — a high-latency pocket for block propagation."})

    # Positive read when nothing crosses the line — reinforces the healthy case.
    if not any(s["level"] in ("critical", "warn") for s in sig):
        sig.insert(0, {"level": "good",
            "text": "No single provider or country crosses the " + str(bft)
                    + "% BFT line — no lone correlated-failure domain can stall the chain."})

    rank = {"critical": 0, "warn": 1, "info": 2, "good": 3}
    sig.sort(key=lambda s: rank.get(s["level"], 9))
    return sig[:6]


def main():
    names = {}
    if NAMES_FILE and os.path.exists(NAMES_FILE):
        names = (json.load(open(NAMES_FILE)) or {}).get("validators", {})

    gdata = json.load(open(GEO))
    geo = gdata["validators"]
    rtt = json.load(open(RTT))
    rttm = rtt["measurements"]

    vs = [v for v in geo if v.get("geo") and v.get("stake_wei")]
    total_stake = sum(v["stake_wei"] for v in vs)
    total_count = len(vs)

    def who(v):
        """Operator name for a validator, from the Foundation's file if we have it."""
        rec = names.get((v.get("node_id") or "")[2:].lower()) or {}
        return rec.get("name"), bool(rec.get("vdp"))

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

    # Operator identity per ASN holder string, built once and reused by the
    # country breakdown, the map popups and the provider table, so all three
    # agree on who the decision-maker is.
    op_meta = defaultdict(lambda: {"names": defaultdict(int), "asns": set()})
    for v in vs:
        raw = v["geo"].get("as", "?") or "?"
        m = op_meta[_norm_operator_key(raw)]
        m["names"][_holder(raw)] += 1
        if _asn_of(raw):
            m["asns"].add(_asn_of(raw))
    op_display = {}
    for v in vs:
        raw = v["geo"].get("as", "?") or "?"
        # NB: do not call this `names`. That shadowed the operator-name map
        # loaded at the top of main() and silently emptied every validator list
        # on the map, with no error anywhere.
        holder_names = op_meta[_norm_operator_key(raw)]["names"]
        op_display[raw] = min(sorted(holder_names), key=len) if holder_names else _holder(raw)

    # Countries (top 12 by stake)
    ks, kc, kr = buckets(lambda v: (v["geo"].get("country", "?"),
                                    v["geo"].get("country_code", "?")))
    # Per-country provider (ASN) stake, to surface in-country concentration.
    cprov = defaultdict(lambda: defaultdict(int))
    for v in vs:
        ckey = (v["geo"].get("country", "?"), v["geo"].get("country_code", "?"))
        asn = v["geo"].get("as", "?") or "?"
        cprov[ckey][op_display.get(asn, _holder(asn))] += v["stake_wei"]
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

    # Providers, ASN level. Kept in the feed for transparency: it is the raw
    # join, and it is what every other public surface publishes.
    asns, asc, asr = buckets(lambda v: v["geo"].get("as", "?") or "?")
    providers_by_asn = []
    cum = 0.0
    for name, s in sorted(asns.items(), key=lambda kv: kv[1], reverse=True)[:12]:
        pct = round(100 * s / total_stake, 2)
        cum = round(cum + pct, 2)
        providers_by_asn.append({
            "name": _holder(name),
            "asn": _asn_of(name),
            "stake_pct": pct,
            "cum_pct": cum,
            "count": asc[name],
            "rtt_ms": wmedian(asr[name]),
        })

    # Providers, OPERATOR level. This is the primary view: a company holding
    # several ASNs is one decision-maker and one correlated-failure domain, and
    # the ASN view understates it. Every group publishes its ASN members so the
    # join can be checked.
    ops, opc, opr = buckets(lambda v: _norm_operator_key(v["geo"].get("as", "?") or "?"))
    providers = []
    cum = 0.0
    for key, s in sorted(ops.items(), key=lambda kv: kv[1], reverse=True)[:12]:
        pct = round(100 * s / total_stake, 2)
        cum = round(cum + pct, 2)
        meta = op_meta[key]
        # display name: the shortest holder string seen for this operator, which
        # is the one without the legal suffix when both spellings appear.
        display = min(sorted(meta["names"]), key=len) if meta["names"] else key
        asns_sorted = sorted(meta["asns"], key=lambda a: int(a[2:]) if a[2:].isdigit() else 0)
        providers.append({
            "name": display,
            "asn": asns_sorted[0] if len(asns_sorted) == 1 else "",
            "asns": asns_sorted,
            "asn_count": len(asns_sorted),
            "stake_pct": pct,
            "cum_pct": cum,
            "count": opc[key],
            "rtt_ms": wmedian(opr[key]),
        })
    multi_asn = [p for p in providers if p["asn_count"] > 1]

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
        nm, is_vdp = who(v)
        if nm:
            d.setdefault("ops", []).append((nm, v["stake_wei"], is_vdp))
        ms = valid_rtt(v)
        if ms is not None:
            d["rtt_pairs"].append((ms, v["stake_wei"]))
        asn = g.get("as", "?") or "?"
        short = op_display.get(asn, _holder(asn))
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
            "validators": [
                {"name": n, "stake_pct": round(100 * w / total_stake, 2), "vdp": vd}
                for n, w, vd in sorted(d.get("ops", []), key=lambda x: -x[1])[:8]
            ],
            "validators_total": len(d.get("ops", [])),
        })
    map_points.sort(key=lambda p: p["stake_pct"], reverse=True)

    # Headline numbers
    top4 = round(sum(p["stake_pct"] for p in providers[:4]), 2)
    # Top-2 providers by stake, generic (no hardcoded provider names — the frontend labels
    # them dynamically from providers[0..1], so this stays correct as the ranking rotates).
    top2 = round(sum(p["stake_pct"] for p in providers[:2]), 2)

    top4_asn = round(sum(p["stake_pct"] for p in providers_by_asn[:4]), 2)
    top2_asn = round(sum(p["stake_pct"] for p in providers_by_asn[:2]), 2)
    headline = {
        # Primary numbers are OPERATOR level: one company is one decision-maker
        # and one correlated-failure domain, whatever number of ASNs it holds.
        "top4_provider_stake_pct": top4,
        "top2_provider_stake_pct": top2,
        # The raw ASN read is published beside it so the difference is visible
        # rather than hidden: every other public surface reports the ASN number.
        "top4_asn_stake_pct": top4_asn,
        "top2_asn_stake_pct": top2_asn,
        "bft_threshold_pct": 33.3,
    }

    # Per-validator latency bands. map_points cannot express this: a city keeps an
    # RTT even when individual hosts inside it are ICMP-silent, so any band split
    # derived from map_points silently inherits the city median for hosts we never
    # measured (that mistake shipped once, 2026-08-20, as "97.9% coverage" when the
    # per-host truth was 90.2%). Computed here from the same join the pipeline
    # already holds, with the unmeasured share stated instead of absorbed.
    band_edges = [(0, 25), (25, 50), (50, 100), (100, 150), (150, None)]
    band_stake = [0] * len(band_edges)
    unmeasured_stake = 0
    for v in vs:
        ms = valid_rtt(v)
        if ms is None:
            unmeasured_stake += v["stake_wei"]
            continue
        for i, (lo, hi) in enumerate(band_edges):
            if ms >= lo and (hi is None or ms < hi):
                band_stake[i] += v["stake_wei"]
                break
    latency_bands = {
        "note": "share of ALL stake per RTT band from the vantage; hosts that "
                "drop ICMP are reported as unmeasured, never folded into a band",
        "bands": [
            {"range_ms": [lo, hi], "stake_pct": round(100 * s / total_stake, 2)}
            for (lo, hi), s in zip(band_edges, band_stake)
        ],
        "unmeasured_stake_pct": round(100 * unmeasured_stake / total_stake, 2),
        "measured_stake_pct": round(100 * (total_stake - unmeasured_stake) / total_stake, 2),
    }
    # Addresses carrying more than one validator: shared machine, shared failure.
    ip_counts = defaultdict(int)
    for v in vs:
        if v.get("ip"):
            ip_counts[v["ip"]] += 1
    colocated = sorted(n for n in ip_counts.values() if n > 1)
    signals = compute_signals(continents, countries, providers, headline)

    out = {
        "generated_at_epoch": int(time.time()),
        "generated_at_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "epoch": vs[0].get("epoch") if vs else None,
        "vantage": os.getenv("GEO_VANTAGE", "Japan testnet full-node (Tokyo)"),
        "vantage_point": {
            "lat": float(os.getenv("GEO_VANTAGE_LAT", "35.6762")),
            "lon": float(os.getenv("GEO_VANTAGE_LON", "139.6503")),
            "label": os.getenv("GEO_VANTAGE_LABEL", "ProofLines vantage"),
        },
        "rtt_method": "ICMP echo, 5 packets/host, averaged over all rounds this epoch, stake-weighted median",
        "coverage": {
            "total_validators": gdata.get("total_validators"),
            "geo_located": total_count,
            "rtt_reachable_ips": rtt.get("reachable_ips"),
            "rtt_total_ips": rtt.get("total_ips"),
            "rtt_rounds": rtt.get("rounds"),
            "rtt_measured_stake_pct": latency_bands["measured_stake_pct"],
            "rtt_unmeasured_stake_pct": latency_bands["unmeasured_stake_pct"],
            "distinct_addresses": len(ip_counts),
            "addresses_with_multiple_validators": len(colocated),
        },
        "foundation_crosscheck": (json.load(open(CROSSCHECK))
                                  if CROSSCHECK and os.path.exists(CROSSCHECK) else None),
        "latency_bands": latency_bands,
        "headline": headline,
        "signals": signals,
        "continents": continents,
        "countries": countries,
        "providers": providers,
        "providers_by_asn": providers_by_asn,
        "operator_grouping": {
            "note": "Providers are grouped by operator, not by ASN: one company "
                    "can hold several ASNs and is still one decision-maker. Each "
                    "entry lists its ASN members so the join can be checked. The "
                    "raw ASN view is published as providers_by_asn.",
            "method": "strip legal-form tokens and punctuation from the ASN holder "
                      "name, then apply an explicit override list",
            "overrides": OPERATOR_OVERRIDES,
            "multi_asn_operators": [
                {"name": p["name"], "asns": p["asns"], "stake_pct": p["stake_pct"]}
                for p in multi_asn
            ],
        },
        "map_points": map_points,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"wrote {OUT}")
    print(f"  continents={len(continents)} countries={len(countries)} providers={len(providers)} map_points={len(map_points)} signals={len(signals)} (excluded {excluded_null_rtt} null-RTT locations)")
    print(f"  top4={top4}% top2={top2}%")


if __name__ == "__main__":
    main()
