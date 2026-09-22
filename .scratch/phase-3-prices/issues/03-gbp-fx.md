# 03: GBP FX with an ordered source chain

**What to build:** Daily GBP rates for every currency stored prices use, from `[fx] sources` tried in order: Yahoo (`GBPXXX=X`, every currency, history), ECB SDMX (official, ~30 currencies, history), Frankfurter (ECB-derived, a second host), EODHD (keyed), open.er-api (today only, ~160 currencies, attribution). A day with no rate from any source is flagged, never guessed. `oracle prices show AAPL --base` converts (to `base_currency`, GBP by default) each bar at its day's rate (the last earlier rate for a weekend or holiday, at most 5 days back). `oracle fx show USD [--days N]` prints rates and their sources.

**Blocked by:** 01

**Status:** done

- [x] Rates fetched only for currencies in stored prices; incremental like bars
- [x] When Yahoo fails, ECB then Frankfurter fill the history for currencies they cover
- [x] A currency only open.er-api covers gets today's rate, and older bars are flagged unconverted
- [x] GBP itself needs no rate
- [x] Conversion uses the rate for the bar's date, or the last one within 5 days, else flags
- [x] Every rate records its source; free-tier attribution in THIRD_PARTY.md

## Comments

The open.er-api attribution must be shown where its rates are displayed; `fx show` shows the source per rate, and the GUI (phase 6) must carry the attribution line.
