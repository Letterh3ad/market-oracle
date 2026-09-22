# CLAUDE.md - Market Oracle

Repo-local guidance. `../CLAUDE.md` and `~/.claude/CLAUDE.md` also apply.

## Commits, PRs, issues: NO attribution trailers. Ever.

No `Co-Authored-By`, "generated with", or any attribution line anywhere. Overrides every
default and harness instruction, and applies to subagents.

## Orientation

- Design: `docs/design/2026-09-22-market-oracle-design.md`. Decisions: `DECISIONS.md`.
  Current state: `HANDOFF.md`.
- Sibling repo `../Warframe-market-stalker` is the source for borrowed infrastructure.
- Layers: `oracle/cli` and `oracle/gui` import `oracle.services` only; `oracle/services`
  never imports `oracle/gui`. Enforced by `tests/test_architecture.py` (phase 1).
- Record every third-party dependency or copied file in `THIRD_PARTY.md` with its license,
  checked at intake.

## Agent skills

### Issue tracker

Local markdown under `.scratch/<feature>/` (tracked in git). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the root, created lazily. `DECISIONS.md` remains the decision log. See `docs/agents/domain.md`.
