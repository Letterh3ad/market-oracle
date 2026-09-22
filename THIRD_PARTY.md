# Third-party and borrowed code

Every dependency, copied file, and reimplemented idea, with its license checked at intake.

## Copied from Warframe Market Stalker (own code, `../Warframe-market-stalker`)

| Here | From | Changes |
|---|---|---|
| `oracle/clock.py` | `wfm/clock.py` | none |
| `tests/fakes/clock.py`, `tests/test_clock.py` | same paths | import paths |
| `oracle/store/db.py` | `wfm/store/db.py` | savepoint prefix |
| `oracle/store/migrate.py` | `wfm/store/migrate.py` | `SchemaTooNew` type |
| `oracle/store/migrations/run_ddl` | `wfm/store/migrations/m0001_initial.up` | extracted as a shared helper |
| `tests/store/test_db.py`, `test_migrate.py` | same paths | rewritten against `MetaRepo` and synthetic migrations |
| `tests/test_architecture.py` | same path | allowlist instead of denylist |

## Runtime dependencies

| Package | License | Use |
|---|---|---|
| httpx | BSD-3-Clause | async HTTP (`oracle/net/client.py`) |

## Data

| Dataset | License | Use |
|---|---|---|
| [FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) (compressed CSVs, fetched at a pinned commit) | MIT | offline universe: names, classification, exchanges (`oracle/universe/financedatabase.py`) |

## Dev dependencies

| Package | License |
|---|---|
| pytest | MIT |
| pytest-asyncio | Apache-2.0 |
