# 02: EODHD as a keyed price source and fallback chain

**What to build:** EODHD (paid) as a second `PriceSource`, enabled only when an API key is set (`[keys] eodhd` in `oracle.toml` or `ORACLE_EODHD_KEY`). `[prices] sources` is tried in order per listing: when a source fails or has no data, the next is tried and the bars record which source supplied them. Yahoo symbols map to EODHD codes for the major exchanges; unmapped exchanges skip EODHD. `oracle prices sources` shows each source, whether it is enabled and why not.

**Blocked by:** 01

**Status:** done

- [x] Without a key EODHD is skipped silently and reported as "no key" by `prices sources`
- [x] With Yahoo failing (breaker open or HTTP error) and a key set, bars come from EODHD
- [x] Symbol mapping covers US, LSE, XETRA, Frankfurt, Paris, Amsterdam, Milan, Madrid, Swiss, Toronto, Tokyo, Hong Kong, Korea, India, Australia; others skip
- [x] Network smoke test against EODHD's public demo key (AAPL.US)
- [x] Keys never appear in logs, errors or `prices sources` output

## Comments

Non-US EODHD codes and LSE pence are unverified until `pytest -m network` runs with `ORACLE_EODHD_KEY` set (cross-checks six exchanges against Yahoo).
