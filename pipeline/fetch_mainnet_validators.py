#!/usr/bin/env python3
"""Build validators-with-ips.json for MAINNET, node-independently.

Testnet gets validator IPs from the local node's peers.toml + validators.toml. We run no
mainnet node, so instead we source:
  - IPs      <- monad-sonar's public mainnet peer feed (secp -> ip:port), no node needed
  - active set + stake + secp <- mainnet staking precompile over public RPC
    (getEpoch / getConsensusSet / getValidator)
Output shape matches parse_peers.py so the rest of the geo-latency pipeline is reused as-is.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

DIR = "/home/admin/monad-knowledge-base/tools/geo-latency"
OUT_FILE = os.getenv("GEO_VALIDATORS_OUT", f"{DIR}/mainnet/validators-with-ips.json")
# Comma-separated sonar peer feeds to UNION. Two vantages (czech + JP/Tokyo) discover
# different subsets (discovery is topology-dependent), lifting geo-located coverage from
# ~63% to ~83% of the active set. First feed to supply an IP for a secp wins.
SONAR_FEEDS = [u.strip() for u in os.getenv(
    "SONAR_MAINNET_FEEDS",
    "https://prooflines.org/monad/sonar/mainnet-peers.json,"
    "https://prooflines.org/monad/sonar/mainnet-peers-jp.json",
).split(",") if u.strip()]
RPC_URL = os.getenv("MAINNET_RPC", "https://rpc.monad.xyz")
CONTRACT = "0x0000000000000000000000000000000000001000"

SEL_EPOCH = "0x757991a8"          # getEpoch() -> word0 = epoch
SEL_CONSENSUS_SET = "0xfb29b729"  # paginated -> [is_done, next_index, offset, len, ids...]
SEL_VALIDATOR = "0x2b6d639a"      # getValidator(id) -> auth_addr, flags, stake..., secp bytes, bls bytes


def rpc_eth_call(data):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": CONTRACT, "data": data}, "latest"]}).encode()
    req = urllib.request.Request(RPC_URL, body, {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    if "error" in d:
        raise RuntimeError(f"rpc error: {d['error']}")
    return d["result"]


def words(hexstr):
    b = hexstr[2:] if hexstr.startswith("0x") else hexstr
    return [b[i:i + 64] for i in range(0, len(b), 64)]


def w2i(w):
    return int(w, 16) if w else 0


def get_epoch():
    return w2i(words(rpc_eth_call(SEL_EPOCH))[0])


def get_consensus_ids():
    """Paginate getConsensusSet. Each page is retried a few times before giving
    up: a short/empty response mid-pagination is an RPC hiccup, not the end of
    the set, and bailing on it truncates the active set (the 200->184 tail loss).
    Retrying the page lets us complete the walk far more often."""
    PAGE_RETRIES = int(os.getenv("GEO_CONSENSUS_PAGE_RETRIES", "4"))
    ids, nxt = [], 0
    while True:
        w = None
        for _ in range(PAGE_RETRIES):
            w = words(rpc_eth_call(SEL_CONSENSUS_SET + f"{nxt:064x}"))
            if len(w) >= 4:
                break
        if not w or len(w) < 4:
            break  # page still malformed after retries -> stop, union will heal
        is_done, nxt = w2i(w[0]), w2i(w[1])
        off = w2i(w[2]) // 32
        if off >= len(w):
            break
        n = w2i(w[off])
        ids += [w2i(w[off + 1 + i]) for i in range(n) if off + 1 + i < len(w)]
        if is_done:
            break
    return ids


def get_validator(vid):
    """Return (secp_0x, stake_wei) for a validator id."""
    w = words(rpc_eth_call(SEL_VALIDATOR + f"{vid:064x}"))
    if len(w) < 13:
        return None, None
    stake_wei = w2i(w[2])                 # total stake (matches the VDP tracker's stake view)
    consensus_stake = w2i(w[6])
    off = w2i(w[10]) // 32                # -> word index of the secp bytes field
    if off >= len(w):
        return None, None
    length = w2i(w[off])                  # expect 33
    if length != 33:
        return None, None
    secp_hex = "".join(w[off + 1:])[:66]  # 33 bytes = 66 hex chars
    return "0x" + secp_hex.lower(), (consensus_stake or stake_wei)


def stabilize_active_set(enriched, epoch, cache_path):
    """Union the active-set reads within an epoch so a partial paginated
    getConsensusSet response never shrinks the published set.

    The active set is FIXED for the duration of an epoch (it only rotates at the
    epoch boundary), so any validator seen in ANY read this epoch genuinely
    belongs. We keep a per-epoch cache keyed by validator_id and union each run
    into it — the current run wins for overlapping ids (fresh IP/stake), cached
    ids the current run missed are retained. Reset when the epoch changes. This
    makes total_validators monotonic within an epoch and kills the 200->183->126
    wobble caused by flaky RPC pagination.
    """
    if os.getenv("GEO_ACTIVESET_STABILIZE", "1") in ("0", "", "false", "False"):
        return enriched
    try:
        cache = json.loads(Path(cache_path).read_text())
        if not isinstance(cache, dict) or cache.get("epoch") != epoch:
            cache = {"epoch": epoch, "validators": {}}
    except Exception:
        cache = {"epoch": epoch, "validators": {}}

    merged = dict(cache.get("validators", {}))   # id(str) -> record from earlier reads this epoch
    for e in enriched:
        merged[str(e["validator_id"])] = e       # current read wins for overlapping ids
    cache = {"epoch": epoch, "validators": merged}
    try:
        Path(cache_path).write_text(json.dumps(cache, separators=(",", ":")))
    except Exception as ex:
        print(f"  active-set cache write failed: {ex}")

    out = list(merged.values())
    if len(out) != len(enriched):
        print(f"  active-set union: this read {len(enriched)}, epoch-best {len(out)} "
              f"(recovered {len(out) - len(enriched)} from partial pagination)")
    return out


def main():
    Path(os.path.dirname(OUT_FILE)).mkdir(parents=True, exist_ok=True)

    # 1) IPs from the sonar mainnet feed(s): secp -> {ip, port}. Union multiple vantages.
    ip_by_secp = {}
    for feed_url in SONAR_FEEDS:
        try:
            with urllib.request.urlopen(feed_url, timeout=20) as r:
                feed = json.load(r)
        except Exception as e:
            print(f"  feed {feed_url} failed: {e}")
            continue
        added = 0
        for p in feed:
            secp = (p.get("secp") or "").lower()
            if secp and secp not in ip_by_secp and p.get("ip"):
                ip_by_secp[secp] = {"ip": p.get("ip"), "port": p.get("port")}
                added += 1
        print(f"sonar feed {feed_url.rsplit(chr(47),1)[-1]}: +{added} new (total {len(ip_by_secp)})")

    # 2) active set + stake + secp from the mainnet precompile
    epoch = get_epoch()
    ids = get_consensus_ids()
    print(f"mainnet epoch {epoch}, consensus set {len(ids)} validators; fetching getValidator...")

    enriched = []
    for vid in ids:
        try:
            secp, stake = get_validator(vid)
        except Exception as e:
            print(f"  skip validator {vid}: {e}")
            continue
        if not secp:
            continue
        peer = ip_by_secp.get(secp)
        enriched.append({
            "node_id": secp,
            "validator_id": vid,
            "name": None,          # no mainnet operator-name directory yet
            "website": None,
            "x": None,
            "ip": peer["ip"] if peer else None,
            "port": peer["port"] if peer else None,
            "stake_wei": stake,
            "epoch": epoch,
            "has_ip": peer is not None,
            "has_directory_entry": False,
        })

    cache_path = os.getenv("GEO_ACTIVESET_CACHE",
                           os.path.join(os.path.dirname(OUT_FILE), "active-set-cache.json"))
    enriched = stabilize_active_set(enriched, epoch, cache_path)

    total = len(enriched)
    with_ip = sum(1 for e in enriched if e["has_ip"])
    out = {
        "generated_at_epoch": epoch,
        "total_peers": len(ip_by_secp),
        "total_validators": total,
        "validators_with_ip": with_ip,
        "validators_with_name": 0,
        "validators_with_both": 0,
        "validators": enriched,
    }
    Path(OUT_FILE).write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT_FILE}")
    print(f"  mainnet epoch {epoch}: {total} validators, {with_ip} with IP "
          f"({100*with_ip/total:.1f}%)" if total else "  no validators")
    return 0


if __name__ == "__main__":
    sys.exit(main())
