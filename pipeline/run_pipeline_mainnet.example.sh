#!/usr/bin/env bash
# Build the MAINNET geo-latency dataset from the CZECH (Prague / Europe) vantage.
# The mainnet validator set is EU/NA-heavy, so RTT is measured from this European
# box for a representative vantage (Tokyo overstated it at ~240 ms).
#
# Node-independent: validator IPs come from the monad-sonar mainnet feed, the
# active set + stake from the mainnet staking precompile over public RPC, and
# geoip from ip-api.com -- all reachable from Czech. Only the ICMP RTT step is
# vantage-dependent, and here it runs locally on this Czech host.
#
# Writes the public feed DIRECTLY into the web root (no rsync). Testnet is
# unaffected: it keeps its own JP/Tokyo pipeline on the JP box.
set -euo pipefail
TOOLS=/home/solana/geo-latency
MN=$TOOLS/mainnet
OUTDATA=/var/www/proofline-public/monad/geo-latency/geo-latency-data-mainnet.json
LOG=$TOOLS/refresh-mainnet.log
exec 9>/tmp/geo-refresh-mainnet.lock; flock -n 9 || exit 0
log(){ echo "$(date -u '+%F %T') $*" >>"$LOG"; }
mkdir -p "$MN"; cd "$TOOLS"

# RTT sanity floor (ms): sub-floor RTTs are nearby-router ICMP-error artifacts
# and must be discarded. Kept from the JP mainnet driver (the fix stays in place).
export GEO_RTT_MIN_MS="${GEO_RTT_MIN_MS:-3}"

log "mainnet refresh start (czech vantage)"
GEO_VALIDATORS_OUT=$MN/validators-with-ips.json python3 fetch_mainnet_validators.py >>"$LOG" 2>&1
GEO_GEOIP_IN=$MN/validators-with-ips.json GEO_GEOIP_OUT=$MN/validators-geoip.json python3 enrich_geoip.py >>"$LOG" 2>&1
sudo env GEO_RTT_SRC=$MN/validators-geoip.json GEO_RTT_OUT=$MN/rtt-measurements.json GEO_RTT_MIN_MS="$GEO_RTT_MIN_MS" python3 measure_rtt.py >>"$LOG" 2>&1
epoch=$(python3 -c "import json;print(json.load(open('$MN/validators-with-ips.json'))['generated_at_epoch'])")
GEO_ACC_DIR=$MN python3 accumulate_rtt_samples.py "$epoch" >>"$LOG" 2>&1
GEO_BUILD_GEO=$MN/validators-geoip.json GEO_BUILD_RTT=$MN/rtt-averaged.json GEO_BUILD_OUT=$OUTDATA \
  GEO_MAP_DROP_NULL_RTT=1 \
  GEO_VANTAGE="Czech / Prague (Europe) - mainnet via monad-sonar" \
  GEO_VANTAGE_LAT="50.0755" GEO_VANTAGE_LON="14.4378" \
  GEO_VANTAGE_LABEL="ProofLines node · Prague" \
  python3 build_public_json.py >>"$LOG" 2>&1
# Append today's snapshot to the rolling history feed (time-series charts).
GEO_HIST_SRC=$OUTDATA python3 append_history.py >>"$LOG" 2>&1 || log "history append failed"
log "mainnet refresh done: epoch $epoch"
echo "DONE epoch $epoch"
