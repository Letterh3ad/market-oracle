# 04: ETFs as a second security type

**What to build:** Refresh also imports FinanceDatabase's ETF file through its own column mapping (category group, category and family instead of sector and industry). Search gets `--type equity|etf` and shows each row's type. ETFs collapse to one row per fund like equities do.

**Blocked by:** 01, 03

**Status:** ready-for-agent

- [ ] One refresh loads equities and ETFs in the same atomic swap; `universe status` counts each type
- [ ] `--type etf` returns only ETFs; no `--type` returns both
- [ ] `--sector` and `--industry` never match ETFs; `--category` and `--family` never match equities
- [ ] `facets type`, `facets category` and `facets family` work
