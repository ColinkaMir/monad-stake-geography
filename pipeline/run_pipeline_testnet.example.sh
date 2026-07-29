#!/usr/bin/env bash
# Testnet geo pipeline, OFF-NODE (czech vantage). IPs from testnet sonar feed, active set from
# public testnet RPC precompile, RTT from Czech/Prague. Clone of refresh_geo_mainnet.sh.
set -euo pipefail
TOOLS=/home/solana/geo-latency
TN=$TOOLS/testnet
OUTDATA="${GEO_TESTNET_OUT:-$TN/geo-latency-data.staging.json}"
LOG=$TOOLS/refresh-testnet.log
FEED="https://prooflines.org/monad/sonar/testnet-peers.json,https://prooflines.org/monad/sonar/testnet-peers-union.json"
TRPC="https://testnet-rpc.monad.xyz"
exec 9>/tmp/geo-refresh-testnet.lock; flock -n 9 || exit 0
log(){ echo "$(date -u '+%F %T') $*" >>"$LOG"; }
mkdir -p "$TN"; cd "$TOOLS"
export GEO_RTT_MIN_MS="${GEO_RTT_MIN_MS:-3}"
log "testnet refresh start (czech vantage) -> $OUTDATA"

do_fetch(){ SONAR_MAINNET_FEEDS="$FEED" MAINNET_RPC="$TRPC" \
  GEO_VALIDATORS_OUT=$TN/validators-with-ips.json python3 fetch_mainnet_validators.py >>"$LOG" 2>&1; }
do_fetch
# guard: getConsensusSet pagination is occasionally partial (RPC hiccup) -> re-fetch once if the
# active set came back suspiciously small, so we never publish a degraded map.
tv=$(python3 -c "import json;print(json.load(open('$TN/validators-with-ips.json'))['total_validators'])" 2>/dev/null || echo 0)
if [ "${tv:-0}" -lt 150 ]; then log "partial active set ($tv<150) -> re-fetch"; sleep 2; do_fetch; fi

GEO_GEOIP_IN=$TN/validators-with-ips.json GEO_GEOIP_OUT=$TN/validators-geoip.json python3 enrich_geoip.py >>"$LOG" 2>&1
sudo env GEO_RTT_SRC=$TN/validators-geoip.json GEO_RTT_OUT=$TN/rtt-measurements.json GEO_RTT_MIN_MS="$GEO_RTT_MIN_MS" python3 measure_rtt.py >>"$LOG" 2>&1
epoch=$(python3 -c "import json;print(json.load(open('$TN/validators-with-ips.json'))['generated_at_epoch'])")
GEO_ACC_DIR=$TN python3 accumulate_rtt_samples.py "$epoch" >>"$LOG" 2>&1
GEO_BUILD_GEO=$TN/validators-geoip.json GEO_BUILD_RTT=$TN/rtt-averaged.json GEO_BUILD_OUT=$OUTDATA \
  GEO_MAP_DROP_NULL_RTT=1 \
  GEO_VANTAGE="Czech / Prague (Europe) - testnet via monad-sonar" \
  GEO_VANTAGE_LAT="50.0755" GEO_VANTAGE_LON="14.4378" \
  GEO_VANTAGE_LABEL="ProofLines node · Prague" \
  python3 build_public_json.py >>"$LOG" 2>&1
# Append today's snapshot to the rolling history feed (time-series charts).
GEO_HIST_SRC=$OUTDATA python3 append_history.py >>"$LOG" 2>&1 || log "history append failed"
log "testnet refresh done: epoch $epoch tv=$tv"
echo "DONE epoch $epoch (tv=$tv) -> $OUTDATA"

# rebuild + deploy the MRC-13 observed feed from the fresh testnet geoip
python3 /home/solana/observed-feed/build_observed_feed.py >>"$LOG" 2>&1 &&   cp /home/solana/observed-feed/observed.json /var/www/proofline-public/monad/observed/observed.json 2>>"$LOG" &&   log "observed feed rebuilt+deployed" || log "observed feed step failed"
