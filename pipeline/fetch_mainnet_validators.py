#!/usr/bin/env python3
"""Build validators-with-ips.json for MAINNET, node-independently.

Testnet gets validator IPs from a local node's peers.toml + validators.toml.
For mainnet, no node is required; instead we source:
  - IPs      <- monad-sonar's public mainnet peer feed (secp -> ip:port), off-node
  - active set + stake + secp <- mainnet staking precompile over public RPC
    (getEpoch / getConsensusSet / getValidator)
Output shape matches parse_peers.py so the rest of the geo-latency pipeline is reused as-is.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

DIR = os.getenv("GEO_DATA_DIR", "./data")
OUT_FILE = os.getenv("GEO_VALIDATORS_OUT", f"{DIR}/validators-with-ips.json")
SONAR_FEED = os.getenv("SONAR_MAINNET_FEED", "https://prooflines.org/monad/sonar/mainnet-peers.json")
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
    ids, nxt = [], 0
    while True:
        w = words(rpc_eth_call(SEL_CONSENSUS_SET + f"{nxt:064x}"))
        if len(w) < 4:
            break
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
    stake_wei = w2i(w[2])                 # total stake
    consensus_stake = w2i(w[6])
    off = w2i(w[10]) // 32                # -> word index of the secp bytes field
    if off >= len(w):
        return None, None
    length = w2i(w[off])                  # expect 33
    if length != 33:
        return None, None
    secp_hex = "".join(w[off + 1:])[:66]  # 33 bytes = 66 hex chars
    return "0x" + secp_hex.lower(), (consensus_stake or stake_wei)


def main():
    Path(os.path.dirname(OUT_FILE) or ".").mkdir(parents=True, exist_ok=True)

    # 1) IPs from the sonar mainnet feed: secp -> {ip, port}
    with urllib.request.urlopen(SONAR_FEED, timeout=20) as r:
        feed = json.load(r)
    ip_by_secp = {}
    for p in feed:
        secp = (p.get("secp") or "").lower()
        if secp:
            ip_by_secp[secp] = {"ip": p.get("ip"), "port": p.get("port")}
    print(f"sonar mainnet feed: {len(ip_by_secp)} peers")

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
