# 02: EODHD as a keyed price source and fallback chain

**What to build:** EODHD (paid) as a second `PriceSource`, enabled only when an API key is set (`[keys] eodhd` in `oracle.toml` or `ORACLE_EODHD_KEY`). `[prices] sources` is tried in order per listing: when a source fails or has no data, the next is tried and the bars record which source supplied them. Yahoo symbols map to EODHD codes for the major exchanges; unmapped exchanges skip EODHD. `oracle prices sources` shows each source, whether it is enabled and why not.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Without a key EODHD is skipped silently and reported as "no key" by `prices sources`
- [ ] With Yahoo failing (breaker open or HTTP error) and a key set, bars come from EODHD
- [ ] Symbol mapping covers US, LSE, XETRA, Frankfurt, Paris, Amsterdam, Milan, Madrid, Swiss, Toronto, Tokyo, Hong Kong, Korea, India, Australia; others skip
- [ ] Network smoke test against EODHD's public demo key (AAPL.US)
- [ ] Keys never appear in logs, errors or `prices sources` output
