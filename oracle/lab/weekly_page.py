"""The research lab's weekly page (research-lab 08, spec 15): one self-contained HTML page from the results
table JSON v1, the papers I'd like, the next candidates and the open questions. Pure: data in, a string out.
Published as a claude.ai artifact whose own database (collection `marks`, one document per candidate's
method ref) keeps the go / drop / dig-deeper marks the next cycle applies with `oracle lab apply-marks`."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlsplit

__all__ = ["CAPABILITIES", "CANDIDATES", "COLLECTION", "Candidate", "Paper", "pick_candidates", "render_page"]

TABLE_VERSION = 1
CANDIDATES = 5
COLLECTION = "marks"
CAPABILITIES = {"db": {"rules": [{"path": COLLECTION, "read": "view", "write": "interact"}]}, "user": {}}
"""What the page must be published with."""
QUALITIES = ("replicated", "peer-reviewed", "working-paper", "practitioner", "abstract-only")
OPEN_STATUSES = ("new", "proposed")
"""Index statuses nobody has decided on yet; go, dropped and dig (triage) already carry a mark."""
NOT_CANDIDATE_FAMILIES = ("C. ", "D. ", "E. ", "F. ", "11. ", "ai-track")
"""Archive families that are not trading methods to test: meta-evidence, evaluation methods, the plan's own fund
and wrapper choices (the Plan tab's), AI tools with no alpha claim, and superseded ai-track tickets. Their archive
ratings score usefulness to the plan, so they would otherwise fill every slot."""
GO_RATING = 3
"""Archive ratings run 1 to 9 (most are 1 to 3); below this a candidate is recommended for drop."""
EVIDENCE_CHARS = 280
MARK_WORDS = (("go", "Go"), ("drop", "Drop"), ("dig", "Dig deeper"))


@dataclass(frozen=True)
class Candidate:
    ref: str
    name: str
    family: str
    rating: int | None
    quality: str | None
    evidence: str
    recommendation: str
    last_mark: str
    """The newest mark in the index, as "go by user 2026-10-04: note", or empty."""


@dataclass(frozen=True)
class Paper:
    title: str
    authors: str
    year: int | None
    url: str | None
    reason: str
    note: str


def _check(table: dict) -> None:
    if not isinstance(table, dict) or table.get("version") != TABLE_VERSION:
        raise ValueError(f"the weekly page reads results table JSON version {TABLE_VERSION}, got "
                         f"{table.get('version') if isinstance(table, dict) else type(table).__name__!r}")


def _quality_rank(q: str | None) -> int:
    return QUALITIES.index(q) if q in QUALITIES else len(QUALITIES)


def _recommend(rating: int | None, quality: str | None) -> str:
    if quality is None or quality == "abstract-only":
        return "dig deeper: no source beyond an abstract yet; read the paper before deciding"
    if quality == "practitioner":
        return "dig deeper: only a practitioner source; find a paper or an OSAP signal for it first"
    rated = "unrated" if rating is None else f"rated {rating}"
    if rating is not None and rating < GO_RATING:
        return f"drop: {rated} in the archive, {quality} evidence; little reason to spend a test on it"
    return f"go: {rated}, {quality} evidence; next is the literature check (OSAP triage) and power"


def _last_mark(row: dict) -> str:
    notes = row.get("notes") or []
    if not notes:
        return ""
    n = notes[0]
    return f"{n['mark']} by {n['by']} {n['at'][:10]}" + (f": {n['note']}" if n.get("note") else "")


def pick_candidates(table: dict, n: int = CANDIDATES) -> list[Candidate]:
    """Methods not yet in the funnel and not yet decided (stage candidate, verdict open, status new or
    proposed) outside the families that are not trading methods, best archive rating first, then best
    source quality, then ref."""
    _check(table)
    rows = [r for r in table["rows"] if r.get("ref") and r["stage"] == "candidate" and r["verdict"] == "open"
            and r["status"] in OPEN_STATUSES and not r["family"].startswith(NOT_CANDIDATE_FAMILIES)]
    rows.sort(key=lambda r: (-(r["rating"] if r["rating"] is not None else -1), _quality_rank(r["quality"]), r["ref"]))
    return [Candidate(r["ref"], r["name"], r["family"], r["rating"], r["quality"], r["evidence"],
                      _recommend(r["rating"], r["quality"]), _last_mark(r)) for r in rows[:n]]


def _e(x: object) -> str:
    return escape("" if x is None else str(x), quote=True)


def _link(url: str | None, text: str) -> str:
    if url and urlsplit(url).scheme in ("http", "https"):
        return f'<a href="{_e(url)}" rel="noopener noreferrer" target="_blank">{_e(text)}</a>'
    return _e(text)


def _short(text: str, n: int = EVIDENCE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 4].rstrip() + " ..."


def _figure(measure: str, x: float) -> str:
    return f"{x:+.4f}" if measure == "IC" else f"{x:+.2%}"


def _line(x: dict) -> str:
    parts = [p for p in (f"h{x['horizon']}" if x["horizon"] is not None else "", x["measure"]) if p]
    for name, value in (("train", x["train"]), ("confirm", x["confirm"])):
        if value is not None:
            parts.append(f"{name} {_figure(x['measure'], value)}")
    if x["t"] is not None:
        parts.append(f"t {x['t']:.2f}")
    return " ".join(parts) + f": {x['verdict']}"


def _tests_cell(row: dict) -> str:
    out = []
    for t in row["tests"]:
        lines = "".join(f"<div class=\"dim\">{_e(_line(x))}</div>" for x in t["lines"])
        out.append(f"<div><span class=\"mono\">{_e(t['key'])}</span> ({_e(t['kind'])}, bar {_e(t['bar'])}): "
                   f"{_e(t['verdict'])}{lines}</div>")
    out += [f"<div class=\"dim\">{_e(u['key'])}: tested under {_e(u['ref'])}</div>" for u in row["tested_under"]]
    return "".join(out) or '<span class="dim">none</span>'


def _results(table: dict) -> str:
    if not table["rows"]:
        return '<p class="empty">No methods or tests in the table yet.</p>'
    body = []
    for r in table["rows"]:
        died = f" at {r['died_at']}" if r["died_at"] else ""
        body.append(
            f"<tr><td class=\"mono\">{_e(r['ref'] or '-')}</td><td>{_e(r['name'])}"
            f"<div class=\"dim\">{_e(r['family'])}</div></td><td>{_e(r['stage'])}</td>"
            f"<td><span class=\"verdict v-{_e(r['verdict'])}\">{_e(r['verdict'])}</span>{_e(died)}</td>"
            f"<td>{_e(r['quality'] or '-')}</td><td class=\"tests\">{_tests_cell(r)}</td>"
            f"<td>{_e(_last_mark(r)) or '<span class=\"dim\">-</span>'}</td></tr>")
    return ('<div class="scroll"><table><thead><tr><th>Ref</th><th>Method</th><th>Stage</th><th>Verdict</th>'
            '<th>Quality</th><th>Tests</th><th>Last mark</th></tr></thead><tbody>' + "".join(body)
            + "</tbody></table></div>")


def _deaths(table: dict) -> str:
    deaths = {stage: int(n) for stage, n in table["deaths"].items()}
    total = sum(deaths.values())
    if not total:
        return '<p class="empty">No method has died yet.</p>'
    top = max(deaths.values())
    rows = "".join(
        f"<li><span class=\"stage\">{_e(stage)}</span><span class=\"bar\"><span style=\"width:{100 * n // top}%\">"
        f"</span></span><span class=\"count\">{_e(n)}</span></li>" for stage, n in deaths.items())
    return f'<ul class="deaths">{rows}</ul><p class="dim">{total} deaths among {len(table["rows"])} rows.</p>'


def _candidate(c: Candidate) -> str:
    facts = " / ".join(_e(x) for x in (c.family, "unrated" if c.rating is None else f"rated {c.rating}",
                                       c.quality or "no source") if x)
    buttons = "".join(f'<button type="button" data-mark="{m}" aria-pressed="false" disabled>{w}</button>'
                      for m, w in MARK_WORDS)
    return (f'<li class="cand" data-key="{_e(c.ref)}"><div class="cand-head"><span class="mono">{_e(c.ref)}</span> '
            f"<strong>{_e(c.name)}</strong></div><div class=\"dim\">{facts}</div>"
            + (f"<p>{_e(_short(c.evidence))}</p>" if c.evidence else "")
            + f"<p class=\"rec\">Recommendation: {_e(c.recommendation)}</p>"
            + (f"<p class=\"dim\">In the index: {_e(c.last_mark)}</p>" if c.last_mark else "")
            + f'<div class="marks" role="group" aria-label="Mark {_e(c.ref)}">{buttons}</div>'
            f'<label class="note">Notes<textarea rows="2" aria-label="Notes on {_e(c.ref)}" disabled></textarea>'
            f'</label><p class="saved dim"></p></li>')


def _candidates(cands: list[Candidate]) -> str:
    if not cands:
        return '<p class="empty">No undecided candidates: every method in the index is marked or in the funnel.</p>'
    return '<p id="db-state" class="dim">Loading saved marks...</p><ol class="cands">' + "".join(
        _candidate(c) for c in cands) + "</ol>"


def _papers(papers: list[Paper]) -> str:
    if not papers:
        return '<p class="empty">None wanted: no recorded source is waiting on a library copy.</p>'
    items = []
    for p in papers:
        who = ", ".join(x for x in (p.authors, str(p.year) if p.year else "") if x)
        items.append(f"<li>{_link(p.url, p.title)}" + (f" <span class=\"dim\">({_e(who)})</span>" if who else "")
                     + f"<div class=\"dim\">{_e(p.reason)}</div>"
                     + (f"<div class=\"dim\">note: {_e(p.note)}</div>" if p.note else "") + "</li>")
    return "<ul class=\"papers\">" + "".join(items) + "</ul>"


def _questions(questions: list[str]) -> str:
    qs = [q.strip() for q in questions if q.strip()]
    if not qs:
        return '<p class="empty">No open questions this week.</p>'
    return "<ol>" + "".join(f"<li>{_e(q)}</li>" for q in qs) + "</ol>"


def _summary(table: dict) -> str:
    said = ", ".join(f"{_e(v)} {_e(n)}" for v, n in table["verdicts"].items() if n)
    return f"{len(table['rows'])} rows" + (f": {said}" if said else "")


STYLE = """
:root{--bg:#f7f6f2;--card:#ffffff;--ink:#1d1d1b;--dim:#5f5e58;--line:#dedcd3;--accent:#1f5fae;--go:#1e7a3c;
--drop:#a8322d;--dig:#8a5a00;--chip:#efede6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;
--dim:#a5a39b;--line:#3a3935;--accent:#7fb0ef;--go:#6cc58a;--drop:#ef8a84;--dig:#e2b55a;--chip:#2a2a27}}
:root[data-theme="dark"]{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--dim:#a5a39b;--line:#3a3935;--accent:#7fb0ef;
--go:#6cc58a;--drop:#ef8a84;--dig:#e2b55a;--chip:#2a2a27}
*{box-sizing:border-box}
html,body{margin:0;overflow-x:hidden}
body{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.6rem;margin:0 0 4px}h2{font-size:1.15rem;margin:0 0 12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:16px 0;min-width:0}
a{color:var(--accent)}.dim{color:var(--dim)}.empty{color:var(--dim);font-style:italic}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:.9em}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:.88rem}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{position:sticky;top:0;background:var(--card)}
td.tests{max-width:360px;overflow-wrap:anywhere}
.verdict{background:var(--chip);border-radius:4px;padding:0 6px;white-space:nowrap}
.v-pass,.v-promising{color:var(--go)}.v-fail,.v-could-not-see,.v-not-testable{color:var(--drop)}
.deaths{list-style:none;margin:0;padding:0}
.deaths li{display:grid;grid-template-columns:7.5rem 1fr 2.5rem;gap:8px;align-items:center;margin:4px 0}
.bar{background:var(--chip);height:10px;border-radius:5px;overflow:hidden}
.bar span{display:block;height:100%;background:var(--drop)}.count{text-align:right}
.cands{list-style:none;margin:0;padding:0}
.cand{border:1px solid var(--line);border-radius:8px;padding:12px;margin:0 0 12px;overflow-wrap:anywhere}
.cand p{margin:6px 0}.rec{font-weight:600}
.marks{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.marks button{font:inherit;padding:6px 14px;border-radius:6px;border:1px solid var(--line);background:var(--chip);
color:var(--ink);cursor:pointer;min-height:40px}
.marks button:disabled{cursor:default;opacity:.6}
.marks button[aria-pressed="true"][data-mark="go"]{background:var(--go);color:var(--card);border-color:var(--go)}
.marks button[aria-pressed="true"][data-mark="drop"]{background:var(--drop);color:var(--card);border-color:var(--drop)}
.marks button[aria-pressed="true"][data-mark="dig"]{background:var(--dig);color:var(--card);border-color:var(--dig)}
.note{display:block;font-size:.9rem;color:var(--dim)}
.note textarea{display:block;width:100%;margin-top:4px;font:inherit;color:var(--ink);background:var(--bg);
border:1px solid var(--line);border-radius:6px;padding:6px}
.papers li,ol li{margin:0 0 8px}
footer{color:var(--dim);font-size:.85rem}
"""

SCRIPT = r"""
async function weeklyMarks(root, claude) {
  const state = root.getElementById("db-state");
  const cards = [...root.querySelectorAll(".cand")];
  if (!cards.length) return;
  const say = (text) => { if (state) state.textContent = text; };
  const buttons = (card) => [...card.querySelectorAll("button[data-mark]")];
  const controls = (card) => [...buttons(card), card.querySelector("textarea")];
  let writable = false;
  let locked = false;
  let ready = false;
  const lock = (why) => {
    writable = false;
    locked = true;
    for (const card of cards) for (const x of controls(card)) x.disabled = true;
    say(why);
  };
  let db = null;
  try { db = claude ? await claude.use("db") : null; } catch { db = null; }
  if (!db) { lock("Read-only here: marks are saved only on the published page."); return; }
  let user = null;
  try { user = await claude.use("user"); } catch { user = null; }
  const me = user ? await user.id() : null;
  writable = !user || (await user.can("data.write")) !== false;
  let saved = {};
  const local = {};
  const queue = {};
  const shown = (key) => local[key] || saved[key];
  const show = (card, problem) => {
    const d = shown(card.dataset.key);
    for (const b of buttons(card)) b.setAttribute("aria-pressed", String(!!d && d.mark === b.dataset.mark));
    const ta = card.querySelector("textarea");
    if (root.activeElement !== ta) ta.value = d && typeof d.note === "string" ? d.note : "";
    const when = d && typeof d.at === "string" ? ` ${d.at.slice(0, 16).replace("T", " ")} UTC` : "";
    card.querySelector(".saved").textContent = problem || (d
      ? (local[card.dataset.key] ? "Saving: " : "") + (d.mark ? `Marked ${d.mark}` : "Note saved") + when
        + (me && d.by === me ? " by you" : "")
      : "Not marked yet.");
  };
  const pause = () => new Promise((r) => setTimeout(r, 200 + Math.random() * 600));
  const send = async (key, body) => {
    try {
      await db.collection("marks").doc(key).set(body);
    } catch (e) {
      if (!(e && e.code === "unavailable")) throw e;
      await pause();
      await db.collection("marks").doc(key).set(body);
    }
  };
  const write = (card, change) => {
    const key = card.dataset.key;
    const prev = shown(key) || {};
    const at = new Date().toISOString();
    const mark = change.mark !== undefined ? change.mark : prev.mark || "";
    const body = { id: key, mark, note: prev.note || "", ...change, by: me, at,
      mark_at: mark !== (prev.mark || "") ? at : prev.mark_at || "" };
    local[key] = body;
    show(card);
    queue[key] = (queue[key] || Promise.resolve()).then(() => send(key, body)).then(
      () => {
        if (local[key] === body) { delete local[key]; saved[key] = body; }
        show(card);
        say("Saved. The next research cycle applies the marks; steer.md overrides any mark.");
      },
      (e) => {
        if (local[key] === body) delete local[key];
        const code = e && e.code;
        const why = code === "quota_exceeded" ? "the page's database is full"
          : code === "invalid_argument" || code === "revoked" ? "this page cannot save marks for you"
            : "the page's database did not answer; try again";
        show(card, `Not saved: ${why}.`);
        if (code === "invalid_argument" || code === "revoked") lock("Read-only: this page could not save your mark.");
        else say(`Not saved: ${why}.`);
      });
    return queue[key];
  };
  for (const card of cards) {
    const ta = card.querySelector("textarea");
    for (const b of buttons(card)) {
      b.addEventListener("click", () => { if (writable && ready) write(card, { mark: b.dataset.mark }); });
    }
    ta.addEventListener("change", () => {
      const before = (shown(card.dataset.key) || {}).note || "";
      if (writable && ready && ta.value !== before) write(card, { note: ta.value });
    });
    show(card);
  }
  say(writable ? "Loading saved marks..." : "Read-only for you: your sharing level cannot save marks.");
  db.collection("marks").onSnapshot((snap) => {
    saved = {};
    for (const d of snap.docs) if (d.exists) saved[d.id] = d.data();
    for (const card of cards) show(card);
    if (!ready && !locked) {
      ready = true;
      for (const card of cards) for (const x of controls(card)) x.disabled = !writable;
      if (writable) say("Pick go, drop or dig deeper; a note saves when you leave its box.");
    }
  }, (e) => {
    const dead = e && (e.code === "revoked" || e.code === "invalid_argument");
    if (dead) lock("Read-only: saved marks are not available here.");
    else say("Saved marks could not be loaded; reload the page to try again.");
  });
}
weeklyMarks(document, window.claude);
"""


def render_page(table: dict, papers: list[Paper], candidates: list[Candidate], questions: list[str],
                generated_at: str) -> str:
    """The whole page. Every value from the table, the papers, the candidates and the questions is escaped."""
    _check(table)
    stamp = generated_at[:16].replace("T", " ") + " UTC"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Lab Weekly</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<header>
<h1>Research Lab Weekly</h1>
<p class="dim">Generated {_e(stamp)} from the results table of {_e(table['as_of'][:10])}. {_summary(table)}.</p>
</header>
<section id="candidates"><h2>Next candidates</h2>
<p class="dim">Mark each go, drop or dig deeper. The next research cycle applies the marks with who and when;
steer.md overrides any mark.</p>
{_candidates(candidates)}</section>
<section id="results"><h2>Results table</h2>{_results(table)}</section>
<section id="deaths"><h2>Deaths by stage</h2>{_deaths(table)}</section>
<section id="papers"><h2>Papers I'd like</h2>{_papers(papers)}</section>
<section id="questions"><h2>Open questions</h2>{_questions(questions)}</section>
<footer><p>Generated {_e(stamp)} by oracle lab page (results table JSON v{TABLE_VERSION}).</p></footer>
</main>
<script>{SCRIPT}</script>
</body>
</html>
"""

