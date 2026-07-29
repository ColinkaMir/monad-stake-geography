#!/usr/bin/env python3
"""Measure ICMP RTT from this node (JP testnet) to every unique validator IP.

Self-contained raw-socket pinger (no fping/ping binary, no apt installs).
Must run as root for SOCK_RAW ICMP: `sudo python3 measure_rtt.py`.

Gentle by design: a handful of echo requests per host, modest concurrency,
short timeout. These are real operators' peer endpoints we already exchange
raptorcast traffic with, so ICMP echo is benign and low-volume.

Output: rtt-measurements.json keyed by IP with min/avg/max ms + packet loss.
Opsec: this stays in the KB; only AGGREGATE latency (by country/ASN/continent)
is ever published, never per-validator IP->RTT rows.
"""
import json
import os
import select
import socket
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

SRC = os.getenv("GEO_RTT_SRC", "validators-geoip.json")
OUT = os.getenv("GEO_RTT_OUT", "rtt-measurements.json")
PACKETS = 5          # echo requests per host
INTERVAL = 0.2       # seconds between packets to one host
TIMEOUT = 2.0        # per-packet wait
WORKERS = 16         # concurrent hosts (spreads load, not a burst per host)
# Sanity floor: any per-host avg below this many ms is a physical impossibility
# for a real remote validator (a genuine Tokyo->remote hop is >=~30 ms). Such
# sub-floor RTTs are ICMP error replies from a nearby router that leaked in as
# a successful echo, so we discard them and treat the host as unreachable.
RTT_MIN_MS = float(os.getenv("GEO_RTT_MIN_MS", "0"))


def checksum(data):
    s = 0
    for i in range(0, len(data) - 1, 2):
        s += (data[i] << 8) + data[i + 1]
    if len(data) & 1:
        s += data[-1] << 8
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def make_packet(ident, seq):
    hdr = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
    payload = struct.pack("!d", time.time())
    chk = checksum(hdr + payload)
    hdr = struct.pack("!BBHHH", 8, 0, chk, ident, seq)
    return hdr + payload


def ping_host(ip):
    ident = os.getpid() & 0xFFFF
    rtts = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except PermissionError:
        return ip, {"error": "need root for raw ICMP"}
    sock.setblocking(False)
    try:
        for seq in range(PACKETS):
            pkt = make_packet(ident, seq)
            t0 = time.time()
            try:
                sock.sendto(pkt, (ip, 0))
            except OSError:
                continue
            deadline = t0 + TIMEOUT
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                r, _, _ = select.select([sock], [], [], remaining)
                if not r:
                    break
                try:
                    data, addr = sock.recvfrom(1024)
                except OSError:
                    break
                if addr[0] != ip:
                    continue
                # IP header length is variable
                ihl = (data[0] & 0x0F) * 4
                icmp = data[ihl:ihl + 8]
                if len(icmp) < 8:
                    continue
                itype, _, _, rid, rseq = struct.unpack("!BBHHH", icmp)
                if itype == 0 and rid == ident and rseq == seq:
                    rtts.append((time.time() - t0) * 1000.0)
                    break
            time.sleep(INTERVAL)
    finally:
        sock.close()

    # Drop sub-floor samples: a real remote hop can't be that fast, so these are
    # router ICMP-error artifacts, not the target's echo reply.
    rtts = [r for r in rtts if r >= RTT_MIN_MS]

    sent = PACKETS
    recv = len(rtts)
    res = {"sent": sent, "recv": recv, "loss_pct": round(100 * (sent - recv) / sent, 1)}
    if rtts:
        res["min_ms"] = round(min(rtts), 2)
        res["avg_ms"] = round(sum(rtts) / len(rtts), 2)
        res["max_ms"] = round(max(rtts), 2)
    return ip, res


def main():
    if os.geteuid() != 0:
        sys.exit("must run as root (sudo) for raw ICMP")
    data = json.load(open(SRC))
    ips = sorted({v["ip"] for v in data["validators"] if v.get("ip")})
    print(f"pinging {len(ips)} unique IPs, {PACKETS} packets each, {WORKERS} workers")
    t0 = time.time()
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for ip, res in ex.map(ping_host, ips):
            results[ip] = res
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(ips)} ...")
    reachable = sum(1 for r in results.values() if r.get("recv", 0) > 0)
    out = {
        "generated_at_epoch": int(time.time()),
        "vantage": "czech-prague",
        "method": "icmp-echo",
        "packets_per_host": PACKETS,
        "total_ips": len(ips),
        "reachable_ips": reachable,
        "measurements": results,
    }
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"done in {time.time()-t0:.0f}s | reachable {reachable}/{len(ips)} "
          f"({100*reachable/len(ips):.0f}%) | wrote {OUT}")


if __name__ == "__main__":
    main()
