#!/usr/bin/env python3
"""Cross-check our off-node crawl against the Foundation's own validator dataset.

Since 2026-09-07 Monad Foundation publishes bucket.monadinfra.com/validator-data/
mainnet.json with, per validator, the operator name, staking record, peer address
and geo/ASN. We derive the same peer address and ASN without running a node, from
the discovery protocol plus ip-api.

Publishing the agreement between the two is the point. It is what makes every
other number on the geo-latency page checkable rather than asserted, and if the
two ever diverge, that divergence is itself the finding.

Emits ONLY aggregates: counts and percentages, never a per-validator row.

Env:
  GEO_XC_OURS   our validators-geoip.json           (default mainnet/validators-geoip.json)
  GEO_XC_OUT    where to write the summary          (default mainnet/foundation-crosscheck.json)
  GEO_XC_URL    Foundation dataset URL
"""
import json
import os
import time
from netutil import get_json

OURS = os.getenv("GEO_XC_OURS", "mainnet/validators-geoip.json")
OUT = os.getenv("GEO_XC_OUT", "mainnet/foundation-crosscheck.json")
URL = os.getenv("GEO_XC_URL", "https://bucket.monadinfra.com/validator-data/mainnet.json")


def main():
    mf = get_json(URL, timeout=45)
    theirs = {v["secp"].lower(): v for v in mf.get("validators", [])}

    ours = json.load(open(OURS))["validators"]
    mine = {v["node_id"][2:].lower(): v for v in ours if v.get("node_id")}

    ip_same = ip_diff = asn_same = asn_diff = 0
    compared_ip = compared_asn = 0
    for secp, mv in mine.items():
        tv = theirs.get(secp)
        if not tv:
            continue
        peer = (tv.get("peer") or {}).get("address")
        if peer and mv.get("ip"):
            compared_ip += 1
            if peer == mv["ip"]:
                ip_same += 1
            else:
                ip_diff += 1
        t_as = (tv.get("geo") or {}).get("as")
        o_as = (mv.get("geo") or {}).get("as")
        if t_as and o_as:
            compared_asn += 1
            if t_as == o_as:
                asn_same += 1
            else:
                asn_diff += 1

    # VDP share, an aggregate that only their file carries: how much of the
    # active-set stake sits with validators the Foundation flags as VDP.
    act = [v for v in theirs.values() if (v.get("staking") or {}).get("in_consensus")]
    tot = sum(v["staking"].get("consensus_stake", 0) for v in act) or 1
    vdp = sum(v["staking"].get("consensus_stake", 0) for v in act
              if (v.get("validator_info") or {}).get("vdp"))

    out = {
        "generated_at_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "source": URL,
        "source_fetched_at_epoch": mf.get("fetched_at_epoch"),
        "source_expected_version": mf.get("expected_version"),
        "note": "Agreement between an independent off-node crawl and the Foundation's "
                "own published validator data. Aggregates only.",
        "our_validators": len(mine),
        "their_records": len(theirs),
        "peer_ip": {"compared": compared_ip, "agree": ip_same, "differ": ip_diff,
                    "agree_pct": round(100 * ip_same / compared_ip, 2) if compared_ip else None},
        "asn": {"compared": compared_asn, "agree": asn_same, "differ": asn_diff,
                "agree_pct": round(100 * asn_same / compared_asn, 2) if compared_asn else None},
        "vdp_share": {
            "note": "share of active-set consensus stake held by validators the "
                    "Foundation flags as VDP participants",
            "validators_in_consensus": len(act),
            "vdp_validators": sum(1 for v in act if (v.get("validator_info") or {}).get("vdp")),
            "vdp_stake_pct": round(100 * vdp / tot, 2),
        },
    }
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2)

    # Operator identities, straight from the Foundation's own public file. We
    # publish name + location + host, never the IP: the address stays on their
    # side of the line, not ours.
    names_out = os.getenv("GEO_XC_NAMES", "")
    if names_out:
        names = {secp: {"name": v.get("name"),
                        "vdp": bool((v.get("validator_info") or {}).get("vdp"))}
                 for secp, v in theirs.items() if v.get("name")}
        json.dump({"source": URL, "generated_at_utc": out["generated_at_utc"],
                   "validators": names}, open(names_out, "w"), indent=1)
        print(f"  names for {len(names)} validators -> {names_out}")
    print(f"crosscheck: ip {ip_same}/{compared_ip} agree, asn {asn_same}/{compared_asn} agree, "
          f"vdp stake {out['vdp_share']['vdp_stake_pct']}% -> {OUT}")


if __name__ == "__main__":
    main()
