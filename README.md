# Monad Stake Geography

Where Monad validator stake actually lives: continent / country / hosting-provider concentration plus ICMP round-trip latency, all measured **off-node** and published as **aggregates only**.

**Live:** https://prooflines.org/monad/geo-latency/

## Why

Decentralization is a blast-radius question, not a headcount. What matters is the largest *correlated-failure domain* — the biggest chunk of stake that can go dark together because it shares a hosting provider, a country, or a legal jurisdiction.

On Monad's validator set the encouraging news is that **no single hosting provider and no single country dominates** the stake. The less obvious news is that a large share of stake — on the order of half — sits inside **one legal jurisdiction**. That jurisdiction, not any one datacenter, is the real correlated-failure domain: a single regulatory or legal action there reaches further than any single ASN outage. This tool makes that number visible next to the friendlier country and provider maps.

## How it works

1. **Discovery (off-node).** Validator IP endpoints come from the [monad-sonar](https://github.com/ColinkaMir/monad-sonar) public peer feeds — you read the network's peers without running a node. Both networks (testnet and mainnet) run fully node-independent.
2. **On-chain stake.** The active set, per-validator stake, and secp keys are read from the Monad staking precompile over public RPC (`getEpoch` / `getConsensusSet` / `getValidator`), so every aggregate is stake-weighted by real on-chain stake.
3. **Geolocation.** Each unique IP is enriched via [ip-api.com](https://ip-api.com) → country, city (approximate), and ASN/hosting provider (reliable).
4. **Latency.** ICMP echo (5 packets/host) is measured from a single vantage, re-measured through the epoch and averaged, then reduced to a stake-weighted median per bucket.
5. **Aggregation.** Only continent / country / ASN / city-location aggregates are emitted to the public JSON that powers the map.

## Blast radius / the 33% BFT line

Monad, like other BFT chains, tolerates faults up to (but not including) **1/3 of stake**. Above that 1/3 line, a single *correlated* failure — one provider outage, one country-wide network event, one jurisdiction acting at once — can take enough stake offline to threaten liveness. The map draws this 1/3 line explicitly and shows the largest single provider, the largest country, and the largest jurisdiction against it, so you can see which failure domains are close to the threshold and which are not.

## Pipeline

Two paths, converging on the same GeoIP → RTT → aggregate stages. `measure_rtt.py` opens a raw ICMP socket and needs root / `CAP_NET_RAW`; everything else runs unprivileged.

Both networks share one off-node path (testnet formerly supported reading a local node's `peers.toml` via `parse_peers.py`, kept for reference):

```
fetch_mainnet_validators.py -> enrich_geoip.py -> measure_rtt.py -> accumulate_rtt_samples.py -> build_public_json.py
```

See `pipeline/run_pipeline_mainnet.example.sh` and `pipeline/run_pipeline_testnet.example.sh` for the production runners (copy and edit paths). RTT vantage since 2026-07-11: Prague, Europe (Tokyo before). The live map defaults to the mainnet view; `#testnet` opens the testnet view. Every stage is configured through environment variables:

| Variable | Purpose |
| --- | --- |
| `GEO_DATA_DIR` / `GEO_ACC_DIR` | Where intermediate JSON is written (default `./data`) |
| `GEO_BUILD_OUT` | Public aggregate output path (e.g. `./site/geo-latency-data.json`) |
| `GEO_VANTAGE`, `GEO_VANTAGE_LABEL`, `GEO_VANTAGE_LAT`, `GEO_VANTAGE_LON` | Vantage identity + map marker position |
| `GEO_RTT_MIN_MS` | Sanity floor (ms) below which RTT samples are discarded as router artifacts |
| `GEO_MAP_DROP_NULL_RTT` | Drop map locations with no ICMP reply (used for the mainnet feed) |
| `MONAD_PEERS_TOML` / `MONAD_VALIDATORS_TOML` | Local node config paths (testnet path) |
| `SONAR_MAINNET_FEED` / `MAINNET_RPC` | Off-node sources (mainnet path) |

## Privacy / opsec

This project publishes **continent / country / ASN aggregates and city-level location points only**. Per-validator IP → identity → city mappings are produced in the intermediate `data/` files (gitignored) and are **intentionally never published**. The opsec boundary lives in `build_public_json.py`: it emits location- and bucket-level rows exclusively, never a per-validator row. ICMP echo to peer endpoints is gentle by design (a handful of packets per host, modest concurrency).

## Credits

Off-node peer discovery is powered by [monad-sonar](https://github.com/ColinkaMir/monad-sonar) — read Monad peers without running a node.

## License

GPL-3.0. See [LICENSE](LICENSE).
