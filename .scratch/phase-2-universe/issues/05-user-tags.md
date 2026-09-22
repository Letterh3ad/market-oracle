# 05: User tags on companies

**What to build:** `oracle tag add <symbol> <tag>`, `oracle tag remove <symbol> <tag>` and `oracle tag list [<symbol>]`. A tag attaches to the company (company key), so tagging `APC.DE` also tags `AAPL`. Search takes `--tag` (repeatable, AND) and shows each row's tags. Tags survive a refresh.

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] Tagging any listing tags the whole company
- [ ] Tags are normalised (trimmed, lower case); adding one twice is a no-op
- [ ] `--tag a --tag b` returns companies carrying both
- [ ] Tags survive a refresh; a company missing after refresh is listed as orphaned by `tag list`, not silently dropped
- [ ] Unknown symbol gives a clear error, with search suggestions
