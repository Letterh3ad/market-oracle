# 03: Job lock that survives a hard kill

**What to build:** A database-backed lock with a lease and heartbeat so a scheduled tick and the GUI never run the same job twice. A lock held by a dead process (pid gone, or lease expired) is detected and taken over rather than blocking forever. `oracle jobs status` shows holder, age, and whether it is stale.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Second acquirer of a held job is refused with who holds it
- [ ] Holder killed hard: next acquirer takes over (dead pid or expired lease)
- [ ] Heartbeat extends the lease; release frees it; release by a non-holder is a no-op
- [ ] `oracle jobs status` reports holder, age, stale
