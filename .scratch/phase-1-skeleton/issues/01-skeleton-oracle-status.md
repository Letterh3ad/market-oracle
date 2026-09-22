# 01: Skeleton with a working `oracle status`

**What to build:** An installable `oracle` package whose `oracle status` command reports the database schema version, database path and where config came from. Underneath: SQLite connection, reentrant transactions and versioned migrations (borrowed from the stalker with their tests), an injectable clock, config loaded from `oracle.toml` with defaults (base currency GBP), a services layer with an application context, and the architecture test enforcing that frontends reach only services and services never import the GUI.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] `pip install -e .[dev]` in a fresh venv, then `oracle status` prints schema version, DB path, config source
- [x] Running `oracle status` twice is idempotent; a DB newer than the build is refused with a clear message
- [x] Missing `oracle.toml` falls back to defaults; `oracle.toml.example` documents every key
- [x] Architecture test passes and fails when a CLI module imports the store directly
- [x] `THIRD_PARTY.md` records what was borrowed from the stalker
- [x] Full suite green
