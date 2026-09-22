# 03: Filter and sort the universe

**What to build:** `oracle universe search` (query optional) takes `--country --sector --industry --exchange --market-cap` filters, `--sort` (name, country, sector, market cap band, symbol) with `--desc`, and `--limit/--offset`. `oracle universe facets <field>` lists each field's values with counts. A filter value that matches nothing suggests the closest real values.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] Filters combine with AND; the same filter repeated combines with OR (`--country Japan --country Germany`)
- [ ] Filter values match case-insensitively
- [ ] Market cap sorts by band order (Mega > Large > Mid > Small > Micro > Nano > unknown), not alphabetically
- [ ] `oracle universe facets country` lists countries with company counts
- [ ] A typo such as `--country Germny` suggests "Germany"
- [ ] Filtering with no query lists companies in sort order
