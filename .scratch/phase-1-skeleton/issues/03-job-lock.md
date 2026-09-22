# 03: Job lock that survives a hard kill

**What to build:** A database-backed lock with a lease and heartbeat so a scheduled tick and the GUI never run the same job twice. A lock held by a dead process (pid gone, or lease expired) is detected and taken over rather than blocking forever. `oracle jobs status` shows holder, age, and whether it is stale.

**Blocked by:** 01

**Status:** done

- [x] Second acquirer of a held job is refused with who holds it
- [x] Holder killed hard: next acquirer takes over (dead pid or expired lease)
- [x] Heartbeat extends the lease; release frees it; release by a non-holder is a no-op
- [x] `oracle jobs status` reports holder, age, stale
