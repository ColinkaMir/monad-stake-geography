"""Shared HTTP helpers for the geo-latency pipeline.

Every stage that talks to the network used to build its own urllib request, and each had to
learn the same two lessons separately: Cloudflare in front of the Monad endpoints rejects the
default Python-urllib User-Agent (bucket.monadinfra.com since 2026-09-29, the public RPCs since
2026-11-03), and the public RPCs rate-limit bursts with 429. One place now holds both.

    get_json(url)                       GET, parse JSON
    post_json(url, obj)                 POST a JSON body, parse JSON
    ..., retries=N                      retry 429/502/503/504, honouring Retry-After
"""

import json
import time
import urllib.error
import urllib.request

USER_AGENT = "prooflines-geo/1.0 (+https://prooflines.org)"
RETRY_CODES = (429, 502, 503, 504)


def _open_json(req, timeout, retries):
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES or attempt == retries:
                raise
            ra = e.headers.get("Retry-After") if e.headers else None
            time.sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else min(2 ** attempt, 16))


def get_json(url, timeout=20, retries=0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return _open_json(req, timeout, retries)


def post_json(url, obj, timeout=20, retries=0):
    req = urllib.request.Request(url, json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    return _open_json(req, timeout, retries)
