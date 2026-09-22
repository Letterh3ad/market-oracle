# market-oracle

Local global-stock research and paper-trading tool. Private.

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/oracle status
.venv/Scripts/python.exe -m pytest -q
```

Design decisions: `DECISIONS.md`. Planning notes are kept locally, outside the repo.
