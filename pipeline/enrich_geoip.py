#!/usr/bin/env python3
"""Enrich validators-with-ips.json using ip-api.com batch endpoint (free tier).
Up to 100 IPs per batch request; we'll throttle ~1 batch per 4 seconds to stay safe.
"""
import json
import os
import time
import urllib.request
from pathlib import Path

IN_FILE = os.getenv("GEO_GEOIP_IN", "/home/admin/monad-knowledge-base/tools/geo-latency/validators-with-ips.json")
OUT_FILE = os.getenv("GEO_GEOIP_OUT", "/home/admin/monad-knowledge-base/tools/geo-latency/validators-geoip.json")
API_BATCH = "http://ip-api.com/batch?fields=status,country,countryCode,regionName,city,lat,lon,isp,org,as,query"


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def batch_query(ips):
    payload = json.dumps(ips).encode("utf-8")
    req = urllib.request.Request(
        API_BATCH,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def main():
    data = json.loads(Path(IN_FILE).read_text())
    validators = data["validators"]
    ips_to_query = sorted({v["ip"] for v in validators if v.get("ip")})
    print(f"unique IPs to enrich: {len(ips_to_query)}")
    geo_by_ip = {}
    for batch in chunk(ips_to_query, 100):
        try:
            results = batch_query(batch)
        except Exception as exc:
            print(f"batch failed: {exc}")
            continue
        for r in results:
            geo_by_ip[r.get("query")] = r
        print(f"  enriched {len(geo_by_ip)}/{len(ips_to_query)}")
        time.sleep(4)
    # join back into validators
    for v in validators:
        ip = v.get("ip")
        if ip and ip in geo_by_ip:
            g = geo_by_ip[ip]
            if g.get("status") == "success":
                v["geo"] = {
                    "country": g.get("country"),
                    "country_code": g.get("countryCode"),
                    "region": g.get("regionName"),
                    "city": g.get("city"),
                    "lat": g.get("lat"),
                    "lon": g.get("lon"),
                    "isp": g.get("isp"),
                    "org": g.get("org"),
                    "as": g.get("as"),
                }
            else:
                v["geo"] = None
        else:
            v["geo"] = None
    # rebuild output
    with_geo = sum(1 for v in validators if v.get("geo"))
    data["validators_with_geo"] = with_geo
    Path(OUT_FILE).write_text(json.dumps(data, indent=2))
    print(f"wrote {OUT_FILE}")
    print(f"  validators with full geo: {with_geo}/{len(validators)} ({100*with_geo/len(validators):.1f}%)")


if __name__ == "__main__":
    main()
