# 05: User tags on companies

**What to build:** `oracle tag add <symbol> <tag>`, `oracle tag remove <symbol> <tag>` and `oracle tag list [<symbol>]`. A tag is stored against the symbol tagged and applies to that listing's company, so tagging `APC.DE` also tags `AAPL`. Search takes `--tag` (repeatable, AND) and shows each row's tags. Tags survive a refresh.

**Blocked by:** 03

**Status:** done

- [x] Tagging any listing tags the whole company
- [x] Tags are normalised (trimmed, lower case); adding one twice is a no-op
- [x] `--tag a --tag b` returns companies carrying both
- [x] Tags survive a refresh; a company missing after refresh is listed as orphaned by `tag list`, not silently dropped
- [x] Unknown symbol gives a clear error, with search suggestions
