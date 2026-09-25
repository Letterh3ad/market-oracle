"""The morning report: `holiday-reports/YYYY-MM-DD.md`, a plain summary of the app's API answers
and the last day's watchdog log, with a short claude summary on top when claude answers."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from string import Template

from holiday_watch.config import HERE
from holiday_watch.seams import HttpError, Watch

DAY = timedelta(hours=24)
FIX_RUN_CHARS = 3000
"""Of each fix run's account, its end: the account comes last."""


def _pct(x: float | None, signed: bool = False) -> str:
    return "n/a" if x is None else f"{x * 100:{'+' if signed else ''}.2f}%"


def _pct1(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def build_report(w: Watch) -> str:
    now = w.now()
    local = now.astimezone(w.tz)
    out = [f"# Holiday report {local:%Y-%m-%d}", "", f"Written {local.isoformat(timespec='minutes')}.", ""]
    status = _get(w, "/api/status")
    out += ["## App", ""]
    if status is None:
        out.append("App: DOWN (no answer from /api/status)")
    else:
        tick = status.get("tick") or {}
        out.append(f"App: up, version {(status.get('server') or {}).get('version')}, automation "
                   f"{'on' if (status.get('automation') or {}).get('enabled') else 'OFF'}")
        out.append(f"Last tick: {tick.get('started_at')} ({tick.get('outcome')})"
                   + (f", first error: {tick['first_error']}" if tick.get("first_error") else ""))
    out += ["", "## Replay study", ""]
    study = (_get(w, "/api/replay") or {}).get("study") if status else None
    if not study:
        out.append("No replay study (or the app is down).")
    else:
        out += _study(study)
    report = (_get(w, "/api/replay/report", timeout=120) or {}).get("report") if study else None
    out += _account("Account", (report or {}).get("account"))
    for size in (report or {}).get("by_size") or []:
        out += _account(f"Account, {size.get('label')}", size.get("account"))
    out += ["", "## Watchdog actions (last 24 h)", ""]
    out += [f"- {line}" for line in _recent_log(w, now)] or ["- none"]
    out += ["", "## Fix runs (last 24 h)", ""]
    runs = _recent_fix_runs(w, now)
    out += runs or ["None."]
    return "\n".join(out).rstrip() + "\n"


def _account(title: str, account: dict | None) -> list[str]:
    """Every line the report returns (the verdicts', the controls, any baseline), not a fixed list."""
    if not account:
        return []
    return ["", f"## {title} ({account.get('currency')} {account.get('capital'):,.0f} notional per line)", "",
            *(f"- {ln['name']} ({ln.get('label') or ln.get('caption')}): {_pct(ln.get('return'), True)},"
              f" max fall {_pct(ln.get('max_fall'))}, {ln.get('trades')} trades, costs {ln.get('costs'):,.0f}"
              for ln in account.get("lines", []))]


def _study(s: dict) -> list[str]:
    lines = [f"State: {s.get('state')}" + (f" ({s['paused_reason']})" if s.get("paused_reason") else ""),
             f"Progress: {s.get('done')} of {s.get('total')} done, {s.get('pending')} pending "
             f"({s.get('retrying')} retrying), {len(s.get('running') or [])} running, "
             f"{len(s.get('failed') or [])} failed"
             + (f", mean replay {s['mean_replay_s']:.0f} s" if s.get("mean_replay_s") else ""),
             f"Pacer: polled at {s.get('polled_at')}" + (f", waiting: {s['waiting']}" if s.get("waiting") else "")]
    if s.get("top_failures"):
        lines.append("Failure reasons: " + "; ".join(f"{e}: {n}" for e, n in s["top_failures"]))
    score = s.get("score")
    if score:
        lines += ["", "Interim score:"]
        for h, v in (score.get("horizons") or {}).items():
            ci = v.get("hit_rate_ci")
            lines.append(f"- {h}: scored {v.get('scored')}, pending {v.get('pending')}, hit rate "
                         f"{_pct1(v.get('hit_rate'))}" + (f" ({_pct1(ci[0])} to {_pct1(ci[1])})" if ci else "")
                         + f", base rate up {_pct1(v.get('base_rate_up'))}, expected "
                         f"{_pct1(v.get('expected_hit_rate'))}")
            lines += [f"  - {name}: n {g.get('n')}, mean return {_pct(g.get('mean_return'))}, as called "
                      f"{_pct(g.get('mean_as_called'))}" for name, g in (v.get("by_verdict") or {}).items()]
        lines += [f"Excluded: {score.get('excluded', 0)}",
                  f"Prices checked at: {score.get('prices_checked_at')}",
                  f"Price failures: {', '.join(score.get('price_failures') or []) or 'none'}"]
    return lines


def _get(w: Watch, path: str, timeout: float = 15.0) -> dict | None:
    try:
        return w.http.get(w.api(path), timeout=timeout)
    except HttpError:
        return None


def _recent_log(w: Watch, now: datetime) -> list[str]:
    try:
        text = w.cfg.log_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    keep = []
    for line in text.splitlines():
        stamp = line.split(" ", 1)[0]
        try:
            at = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if at.tzinfo and now - at <= DAY and "[dry-run]" not in line:
            keep.append(line)
    return keep


def _recent_fix_runs(w: Watch, now: datetime) -> list[str]:
    out = []
    for path in sorted(w.cfg.fixruns.glob("*.md")):
        try:
            at = datetime.strptime(path.name[:15], "%Y-%m-%d-%H%M").replace(tzinfo=now.astimezone(w.tz).tzinfo)
        except ValueError:
            continue
        if now - at <= DAY:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            out += [f"### {path.name}", "", text[-FIX_RUN_CHARS:], ""]
    return out


def summary_command(w: Watch) -> tuple[str, ...]:
    return (w.cfg.claude, "-p", "--model", w.cfg.report_model, "--no-session-persistence", "--tools", "",
            "--strict-mcp-config", "--setting-sources", "project", "--permission-mode", "dontAsk",
            "--disable-slash-commands", "--output-format", "text")


def write_report(w: Watch) -> Path | None:
    """The day's report file; a dry run prints it instead and returns None."""
    plain = build_report(w)
    if w.dry_run:
        w.echo(plain)
        return None
    w.cfg.reports.mkdir(parents=True, exist_ok=True)
    prompt = Template((HERE / "summary_prompt.txt").read_text(encoding="utf-8")).substitute(report=plain)
    ran = w.system.run(summary_command(w), prompt, w.cfg.report_timeout_min * 60, w.cfg.reports)
    title, _, rest = plain.partition("\n\n")
    if ran.code == 0 and not ran.timed_out and ran.out.strip():
        summary = ran.out.strip()
    else:
        why = "timed out" if ran.timed_out else f"claude exit {ran.code}"
        summary = f"Summary unavailable: {why}."
    path = w.cfg.reports / f"{w.now().astimezone(w.tz):%Y-%m-%d}.md"
    path.write_text(f"{title}\n\n## Summary\n\n{summary}\n\n{rest}", encoding="utf-8")
    w.log(f"morning report written: {path}")
    return path
