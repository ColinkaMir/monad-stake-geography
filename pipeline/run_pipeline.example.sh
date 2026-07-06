#!/usr/bin/env bash
#
# Example runner for the Monad Stake Geography pipeline.
#
# This is a documented template, not a turnkey script. It shows the stage order
# and the environment variables each stage reads. Copy it, set the vars for your
# environment, and run.
#
# Two paths are supported:
#   - testnet: discover validator IPs from a LOCAL Monad node's config
#   - mainnet: discover validator IPs OFF-NODE via the monad-sonar public feed
#
# Both paths converge on the same GeoIP -> RTT -> aggregate stages.
#
# NOTE: measure_rtt.py opens a raw ICMP socket and must run as root
#       (or with CAP_NET_RAW). Everything else runs unprivileged.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------
export GEO_DATA_DIR="${GEO_DATA_DIR:-./data}"          # intermediate JSON lives here
export GEO_ACC_DIR="${GEO_ACC_DIR:-$GEO_DATA_DIR}"     # RTT accumulator working dir

# Vantage point: where the ICMP latency is measured FROM. Set to your host's
# approximate location so the map can draw the vantage marker.
export GEO_VANTAGE="${GEO_VANTAGE:-My vantage}"
export GEO_VANTAGE_LABEL="${GEO_VANTAGE_LABEL:-My vantage}"
export GEO_VANTAGE_LAT="${GEO_VANTAGE_LAT:-35.6762}"
export GEO_VANTAGE_LON="${GEO_VANTAGE_LON:-139.6503}"

# Sanity floor (ms). RTT samples below this are treated as router ICMP-error
# artifacts and dropped. 0 disables the floor. Set to ~20-30 for a real
# long-haul vantage if you see impossibly-low samples.
export GEO_RTT_MIN_MS="${GEO_RTT_MIN_MS:-0}"

NET="${1:-mainnet}"   # pass "testnet" or "mainnet" (default: mainnet)

# ---------------------------------------------------------------------------
# Stage 1: discovery -> data/validators-with-ips.json
# ---------------------------------------------------------------------------
if [ "$NET" = "testnet" ]; then
  # Reads a local Monad node's peers.toml + validators.toml (may need read
  # access to the node's config dir). Point these at your node's config.
  export MONAD_PEERS_TOML="${MONAD_PEERS_TOML:-/var/lib/monad/config/peers.toml}"
  export MONAD_VALIDATORS_TOML="${MONAD_VALIDATORS_TOML:-/var/lib/monad/config/validators/validators.toml}"
  export GEO_DIRECTORY_FILE="${GEO_DIRECTORY_FILE:-$GEO_DATA_DIR/validator-directory.json}"
  export GEO_PARSE_OUT="$GEO_DATA_DIR/validators-with-ips.json"
  python3 parse_peers.py
else
  # Off-node: pull the active set + stake from the mainnet staking precompile
  # over public RPC, and IPs from the monad-sonar public mainnet peer feed.
  export SONAR_MAINNET_FEED="${SONAR_MAINNET_FEED:-https://prooflines.org/monad/sonar/mainnet-peers.json}"
  export MAINNET_RPC="${MAINNET_RPC:-https://rpc.monad.xyz}"
  export GEO_VALIDATORS_OUT="$GEO_DATA_DIR/validators-with-ips.json"
  python3 fetch_mainnet_validators.py
fi

# ---------------------------------------------------------------------------
# Stage 2: GeoIP enrichment -> data/validators-geoip.json
# ---------------------------------------------------------------------------
export GEO_GEOIP_IN="$GEO_DATA_DIR/validators-with-ips.json"
export GEO_GEOIP_OUT="$GEO_DATA_DIR/validators-geoip.json"
python3 enrich_geoip.py

# ---------------------------------------------------------------------------
# Stage 3: ICMP RTT measurement -> data/rtt-measurements.json  (NEEDS ROOT)
# ---------------------------------------------------------------------------
export GEO_RTT_SRC="$GEO_DATA_DIR/validators-geoip.json"
export GEO_RTT_OUT="$GEO_DATA_DIR/rtt-measurements.json"
# Raw ICMP requires root / CAP_NET_RAW:
sudo -E python3 measure_rtt.py

# ---------------------------------------------------------------------------
# Stage 4: accumulate RTT rounds across the epoch -> data/rtt-averaged.json
# ---------------------------------------------------------------------------
# Derive the current epoch from the discovery output so per-epoch samples reset
# correctly when the epoch rolls over.
EPOCH="$(python3 -c 'import json,os; d=json.load(open(os.environ["GEO_DATA_DIR"]+"/validators-with-ips.json")); print(d.get("generated_at_epoch") or 0)')"
python3 accumulate_rtt_samples.py "$EPOCH"

# ---------------------------------------------------------------------------
# Stage 5: build the PUBLIC aggregate feed -> site/geo-latency-data*.json
# ---------------------------------------------------------------------------
export GEO_BUILD_GEO="$GEO_DATA_DIR/validators-geoip.json"
export GEO_BUILD_RTT="$GEO_DATA_DIR/rtt-averaged.json"
if [ "$NET" = "testnet" ]; then
  export GEO_BUILD_OUT="./site/geo-latency-data.json"
else
  export GEO_BUILD_OUT="./site/geo-latency-data-mainnet.json"
  # Mainnet has near-complete coverage; drop locations with no ICMP reply so the
  # map doesn't render grey "n/a" dots.
  export GEO_MAP_DROP_NULL_RTT="1"
fi
python3 build_public_json.py

echo "done: $NET feed written to $GEO_BUILD_OUT"
