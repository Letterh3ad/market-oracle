# 02: Per-source rate limiting and circuit breakers, shared across processes

**What to build:** A rate-limited async HTTP client where each named source (e.g. `yfinance`, `gdelt`) has its own request rate, backoff and circuit breaker. Rate and breaker state persist in SQLite so separate `oracle` processes hitting the same source together stay within its rate, and a tripped breaker survives a restart. `oracle net status` shows each source's rate, recent spend and breaker state.

**Blocked by:** 01

**Status:** done

- [x] Two processes acquiring the same source concurrently respect its combined rate (multi-process test)
- [x] Different sources do not throttle each other
- [x] A run of 429/403/5xx trips that source's breaker; it stays open across a new process until cooldown
- [x] Retry-After and exponential backoff honoured
- [x] Sources and rates configured in `oracle.toml`
- [x] `oracle net status` output covers every configured source
