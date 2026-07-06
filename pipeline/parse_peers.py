#!/usr/bin/env python3
"""Parse peers.toml + validators.toml + validator-directory.json into structured dataset.

Testnet path: sources validator IPs from a local Monad node's config
(peers.toml + validators.toml) and joins them to an optional operator-name
directory. Paths are env-driven; the defaults below are generic examples.
"""
import json
import os
import re
import sys
from pathlib import Path

PEERS_FILE = os.getenv("MONAD_PEERS_TOML", "/var/lib/monad/config/peers.toml")
VALIDATORS_FILE = os.getenv("MONAD_VALIDATORS_TOML", "/var/lib/monad/config/validators/validators.toml")
DIRECTORY_FILE = os.getenv("GEO_DIRECTORY_FILE", "./data/validator-directory.json")
OUT_FILE = os.getenv("GEO_PARSE_OUT", "./data/validators-with-ips.json")


def parse_peers_toml(path):
    """Read peers.toml (may require elevated read access) and return list of {pubkey, address}."""
    content = Path(path).read_text()
    blocks = re.split(r"\[\[peers\]\]", content)
    peers = []
    for block in blocks[1:]:
        addr_m = re.search(r'address\s*=\s*"([^"]+)"', block)
        pk_m = re.search(r'secp256k1_pubkey\s*=\s*"(0x[0-9a-f]+)"', block)
        seq_m = re.search(r"record_seq_num\s*=\s*(\d+)", block)
        if addr_m and pk_m:
            ip, _, port = addr_m.group(1).partition(":")
            peers.append({
                "ip": ip,
                "port": int(port) if port else None,
                "pubkey": pk_m.group(1).lower(),
                "record_seq_num": int(seq_m.group(1)) if seq_m else None,
            })
    return peers


def parse_validators_toml(path):
    """Return list of {node_id, stake, cert_pubkey, epoch} for the ACTIVE set.

    The file usually carries two validator_sets (current epoch + next one);
    we parse each separately and keep only the lowest-epoch (active) set.
    """
    content = Path(path).read_text()
    sets = re.split(r"\[\[validator_sets\]\]", content)[1:]
    parsed = []
    for s in sets:
        epoch_m = re.search(r"epoch\s*=\s*(\d+)", s)
        epoch = int(epoch_m.group(1)) if epoch_m else None
        validators = []
        for block in re.split(r"\[\[validator_sets\.validators\]\]", s)[1:]:
            nid_m = re.search(r'node_id\s*=\s*"(0x[0-9a-f]+)"', block)
            stake_m = re.search(r'stake\s*=\s*"(0x[0-9a-f]+)"', block)
            cert_m = re.search(r'cert_pubkey\s*=\s*"(0x[0-9a-f]+)"', block)
            if nid_m:
                stake_hex = stake_m.group(1) if stake_m else None
                stake_int = int(stake_hex, 16) if stake_hex else None
                validators.append({
                    "node_id": nid_m.group(1).lower(),
                    "stake_wei": stake_int,
                    "cert_pubkey": cert_m.group(1).lower() if cert_m else None,
                    "epoch": epoch,
                })
        if validators:
            parsed.append((epoch, validators))
    if not parsed:
        return []
    parsed.sort(key=lambda t: (t[0] is None, t[0]))
    return parsed[0][1]


def load_directory(path):
    """Validator-directory.json: list of {id, name, secp, website, x, source}.

    Optional. If missing, validators are emitted without operator names.
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = p.read_text(encoding="utf-8-sig")
    return json.loads(raw)


def main() -> int:
    peers = parse_peers_toml(PEERS_FILE)
    validators = parse_validators_toml(VALIDATORS_FILE)
    directory = load_directory(DIRECTORY_FILE)

    # Build lookup: secp pubkey (no 0x prefix) -> directory entry
    dir_by_secp = {entry["secp"].lower(): entry for entry in directory if entry.get("secp")}

    # Build lookup: pubkey -> ip+port from peers
    peer_by_pubkey = {p["pubkey"]: p for p in peers}

    # Merge: for each validator, look up its IP and directory info
    enriched = []
    for v in validators:
        nid = v["node_id"]
        secp_no_prefix = nid[2:] if nid.startswith("0x") else nid
        peer = peer_by_pubkey.get(nid)
        dir_entry = dir_by_secp.get(secp_no_prefix)
        enriched.append({
            "node_id": nid,
            "validator_id": dir_entry["id"] if dir_entry else None,
            "name": dir_entry["name"] if dir_entry else None,
            "website": dir_entry.get("website") if dir_entry else None,
            "x": dir_entry.get("x") if dir_entry else None,
            "ip": peer["ip"] if peer else None,
            "port": peer["port"] if peer else None,
            "stake_wei": v["stake_wei"],
            "epoch": v["epoch"],
            "has_ip": peer is not None,
            "has_directory_entry": dir_entry is not None,
        })

    # Stats
    total = len(enriched)
    with_ip = sum(1 for e in enriched if e["has_ip"])
    with_name = sum(1 for e in enriched if e["has_directory_entry"])
    with_both = sum(1 for e in enriched if e["has_ip"] and e["has_directory_entry"])

    out = {
        "generated_at_epoch": validators[0]["epoch"] if validators else None,
        "total_peers": len(peers),
        "total_validators": total,
        "validators_with_ip": with_ip,
        "validators_with_name": with_name,
        "validators_with_both": with_both,
        "validators": enriched,
    }

    Path(OUT_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(OUT_FILE).write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT_FILE}")
    print(f"  total peers in peers.toml: {len(peers)}")
    print(f"  total validators in epoch {out['generated_at_epoch']}: {total}")
    print(f"  validators with IP discovered: {with_ip} ({100*with_ip/total:.1f}%)")
    print(f"  validators with operator name: {with_name} ({100*with_name/total:.1f}%)")
    print(f"  validators with BOTH (full identity): {with_both} ({100*with_both/total:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
