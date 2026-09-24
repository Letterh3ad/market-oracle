"""Discovery chat: a question about which companies fit ("which European companies gain from
grid batteries?") answered with a ranked shortlist. The model plans the searches and names the
companies the news shows, citing the items it read; which listing a name means, and whether it
may be suggested at all, is decided in code as for discovery runs. Questions and every answer
are saved. A follow-up is a question of its own that refines another; a rerun answers a question
again and says what changed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from oracle.net.gate import SourceGate
from oracle.research.llm import ChatModel, ModelError
from oracle.research.mentions import QUESTION_SCHEMA, Mention, parse_mentions, question_prompt
from oracle.research.queries import EDITION, MAX_QUERY
from oracle.research.tools import ToolError
from oracle.research.trace import Evidence
from oracle.research.web import DdgsSearcher, WebSearcher
from oracle.services.candidate_screen import REMOVED, Screen
from oracle.services.context import AppContext
from oracle.services.discovery_service import SOURCE_QUESTION, Added, add_by_hand
from oracle.services.errors import InvalidQuery, ModelUnavailable, UnknownQuestion
from oracle.services.http import open_client
from oracle.services.jobs_service import job_statuses, single_flight
from oracle.services.research_service import configured_model, context_tokens
from oracle.services.research_tools import GdeltSearch, LocalNewsSearch, NewsSearch, Reader, Shelf, WebSearch
from oracle.services.research_tools.reading import TRY_ANOTHER
from oracle.store.db import to_utc_iso
from oracle.store.discoveries import DiscoveryEvidence
from oracle.store.job_history import INTERRUPTED
from oracle.store.questions import DONE, FAILED, RUNNING, Answer, Question, QuestionRepo, ShortlistItem
from oracle.store.research import evidence_of, note_model
from oracle.store.universe import ListingProfile
from oracle.universe.listings import MARKET_CAP_BANDS

__all__ = [
    "AnswerView", "Line", "Question", "QuestionLine", "QuestionView", "add_to_discovered", "answer", "answer_progress",
    "ask", "new_question", "question", "questions", "saved_question", "sweep_interrupted_answers",
]

JOB = "discovery_chat"
LEASE_S = 1800.0
MAX_QUESTION = 300
# Bounds on one answer: planned news queries (each searched on Google News and GDELT), local
# editions, web searches, items the naming call reads, articles read in full, price checks.
MAX_QUERIES = 4
MAX_LOCAL = 2
MAX_WEB = 2
MAX_ITEMS = 24
READ = 4
MAX_PRICE_CHECKS = 8
MAX_EXCLUDE = 5
# Notes taken for a question are kept apart from the same model's notes for research, where
# "not about the company" means one company.
NOTE_KEY = "#question"

PLAN = """You plan news searches to answer an investor's question about which listed companies fit it.
Reply with JSON:
"queries": up to {queries} short English news search queries (under 8 words each) likely to find headlines naming such companies. No dates, no words like "latest" or "news".
"local": when the question names a country or region with its own language, up to {local} searches written in that language, each with its "country" from the list; else empty.
"market_caps": the company sizes the question asks for, from the list; empty for any size.
"exclude": sectors or industries the question rules out, a word or two each; empty for none."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {"type": "array", "maxItems": MAX_QUERIES, "items": {"type": "string", "maxLength": MAX_QUERY}},
        "local": {"type": "array", "maxItems": MAX_LOCAL, "items": {"type": "object", "properties": {
            "country": {"type": "string", "enum": sorted(EDITION)},
            "query": {"type": "string", "maxLength": MAX_QUERY}}, "required": ["country", "query"]}},
        "market_caps": {"type": "array", "items": {"type": "string", "enum": list(MARKET_CAP_BANDS)}},
        "exclude": {"type": "array", "maxItems": MAX_EXCLUDE, "items": {"type": "string", "maxLength": 40}},
    },
    "required": ["queries", "local", "market_caps", "exclude"],
}


@dataclass(frozen=True)
class Search:
    tool: str
    """news, gdelt, local or web."""
    query: str
    country: str | None = None

    @property
    def label(self) -> str:
        where = {"news": "Google News", "gdelt": "GDELT", "web": "the web"}.get(self.tool) or f"Google News {self.country}"
        return f"{where}: {self.query}"


@dataclass(frozen=True)
class Plan:
    searches: tuple[Search, ...]
    market_caps: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Line:
    """A shortlist entry as the page shows it."""

    item: ShortlistItem
    evidence: tuple[DiscoveryEvidence, ...]
    new: bool
    """Not on the answer it is compared with."""
    watched: bool
    """The company is on one of the user's own lists now."""
    discovered: bool
    """The company is on the discovered list now."""


@dataclass(frozen=True)
class AnswerView:
    answer: Answer
    shortlist: tuple[Line, ...]
    skipped: tuple[ShortlistItem, ...]
    gone: tuple[ShortlistItem, ...]
    """On the answer compared with, not on this one."""
    compared_to: int | None
    """The answer "new" and "gone" are against: the question's previous one, or for a
    follow-up's first, its question's newest."""


@dataclass(frozen=True)
class QuestionView:
    question: Question
    asked: str
    """The whole question, follow-ups included, as the model is given it."""
    chain: tuple[Question, ...]
    """What it refines, first asked first, then itself."""
    follow_ups: tuple[Question, ...]
    answers: tuple[AnswerView, ...]
    """Newest first."""


@dataclass(frozen=True)
class QuestionLine:
    question: Question
    answers: int
    last: Answer | None = field(default=None)


def new_question(ctx: AppContext, text: str, refines: int | None = None) -> Question:
    """Save a question, or a follow-up refining `refines`, ready to answer."""
    text = " ".join(text.split())
    if not text:
        raise InvalidQuery("ask a question, e.g. which European companies gain from grid batteries?")
    if len(text) > MAX_QUESTION:
        raise InvalidQuery(f"keep the question under {MAX_QUESTION} characters")
    repo = QuestionRepo(ctx.conn)
    if refines is not None and repo.get(refines) is None:
        raise UnknownQuestion(f"no question {refines} to follow up")
    return repo.add(text, refines, _now(ctx))


async def ask(ctx: AppContext, text: str, refines: int | None = None, model: ChatModel | None = None,
              searcher: WebSearcher | None = None) -> AnswerView:
    return await answer(ctx, new_question(ctx, text, refines).id, model, searcher)


async def answer(ctx: AppContext, question_id: int, model: ChatModel | None = None,
                 searcher: WebSearcher | None = None) -> AnswerView:
    """Answer a saved question (again): plan, search, read, name, screen, save. The model
    failing to answer fails the answer, which stays saved with why."""
    repo = QuestionRepo(ctx.conn)
    q = _question(repo, question_id)
    asked = _asked(repo, q)
    model = model or configured_model(ctx)
    with single_flight(ctx, JOB, LEASE_S):
        answer_id = repo.start(q.id, _model_label(model), _now(ctx))
        notes: list[str] = []
        try:
            items = await _answer(ctx, repo, answer_id, asked, model, searcher or DdgsSearcher(), notes)
        except BaseException as exc:
            error = "cancelled" if not isinstance(exc, Exception) else str(exc) or type(exc).__name__
            repo.finish(answer_id, FAILED, error, notes, (), _now(ctx))
            if isinstance(exc, ModelError):
                raise ModelUnavailable(str(exc)) from exc
            raise
        repo.finish(answer_id, DONE, None, notes, items, _now(ctx))
    return _view(ctx, repo, q, repo.answer(answer_id))


def saved_question(ctx: AppContext, question_id: int) -> Question:
    return _question(QuestionRepo(ctx.conn), question_id)


def question(ctx: AppContext, question_id: int) -> QuestionView:
    repo = QuestionRepo(ctx.conn)
    q = _question(repo, question_id)
    chain = _chain(repo, q)
    return QuestionView(q, _asked(repo, q), tuple(chain), tuple(repo.follow_ups(q.id)),
                        tuple(_view(ctx, repo, q, a) for a in repo.answers(q.id)))


def questions(ctx: AppContext) -> list[QuestionLine]:
    """Every saved question, newest first, with its newest answer."""
    repo = QuestionRepo(ctx.conn)
    out = []
    for q in repo.all():
        answers = repo.answers(q.id)
        out.append(QuestionLine(q, len(answers), answers[0] if answers else None))
    return out


def answer_progress(ctx: AppContext, question_id: int) -> str | None:
    """Where the question's running answer is, for a progress line."""
    answers = QuestionRepo(ctx.conn).answers(question_id)
    return answers[0].stage if answers and answers[0].status == RUNNING else None


def add_to_discovered(ctx: AppContext, answer_id: int, n: int) -> Added:
    """A shortlisted company onto the discovered list, with the question as why."""
    repo = QuestionRepo(ctx.conn)
    item = repo.item(answer_id, n)
    if item is None:
        raise UnknownQuestion(f"no company {n} on answer {answer_id}")
    if item.skipped or item.symbol is None:
        raise InvalidQuery(f"{item.name} was left off the shortlist: {item.skipped}")
    q = repo.get(repo.answer(answer_id).question_id)
    return add_by_hand(ctx, item.symbol, SOURCE_QUESTION, q.text, "answer", item.evidence)


def sweep_interrupted_answers(ctx: AppContext) -> None:
    """Mark answers a stopped app left running as failed, unless another process is answering."""
    if any(s.job == JOB and s.stale_reason is None for s in job_statuses(ctx)):
        return
    QuestionRepo(ctx.conn).fail_running(INTERRUPTED, _now(ctx))


async def _answer(ctx: AppContext, repo: QuestionRepo, answer_id: int, asked: str, model: ChatModel,
                  searcher: WebSearcher, notes: list[str]) -> list[ShortlistItem]:
    def stage(text: str) -> None:
        repo.set_stage(answer_id, text)

    stage("planning searches")
    plan = await _plan(model, asked)
    repo.set_searches(answer_id, [s.label for s in plan.searches])
    notes.extend(_filter_notes(plan))
    note_key = note_model(model.name, model.model) + NOTE_KEY
    found: list[list[int]] = []
    shelf = Shelf(ctx.conn, ctx.clock, lambda ids: found[-1].extend(ids))
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    async with open_client(ctx) as client:
        for n, s in enumerate(plan.searches, 1):
            stage(f"searching {n} of {len(plan.searches)}")
            found.append([])
            tool = {"news": lambda: NewsSearch(client, shelf), "gdelt": lambda: GdeltSearch(client, shelf),
                    "local": lambda: LocalNewsSearch(client, shelf, s.country),
                    "web": lambda: WebSearch(searcher, gate, shelf)}[s.tool]()
            try:
                await tool.run(s.query)
            except ToolError as exc:
                notes.append(f"could not search {s.label} ({str(exc).removesuffix(TRY_ANOTHER)})")
        ids = _interleave(found)[:MAX_ITEMS]
        if not ids:
            notes.append("the searches found nothing to read")
            return []
        reader = Reader(ctx.conn, ctx.clock, client, searcher, gate, ctx.config.research.contact, model,
                        f'companies that answer "{asked}"', context_tokens(ctx), note_key)
        newest_first = sorted(evidence_of(ctx.conn, ids, note_key),
                              key=lambda e: (e.published is not None, e.published), reverse=True)
        await reader.read(newest_first, READ, None, lambda n, total: stage(f"reading {n} of {total}"))
    stage("naming companies")
    evidence = evidence_of(ctx.conn, ids, note_key)
    texts = [_item_text(e) for e in evidence]
    mentions = parse_mentions(await model.reply(question_prompt(asked, texts), QUESTION_SCHEMA), texts)
    if not mentions:
        notes.append("the model named no company the news shows")
    # More items behind a name first; the model's own order among equals.
    ranked = sorted(mentions, key=lambda m: -len(m.headlines))
    stage("checking listings")
    return await _Shortlist(ctx, plan).screen(ranked, [e.id for e in evidence])


async def _plan(model: ChatModel, asked: str) -> Plan:
    """The model's searches and filters; the question itself searched when it plans none."""
    reply = await model.reply([{"role": "system", "content": PLAN.format(queries=MAX_QUERIES, local=MAX_LOCAL)},
                               {"role": "user", "content": asked}], PLAN_SCHEMA)
    reply = reply if isinstance(reply, dict) else {}
    queries = _unique(q for q in _strings(reply.get("queries")) if len(q) <= MAX_QUERY)[:MAX_QUERIES]
    if not queries:
        queries = [asked.rstrip("?")[:MAX_QUERY]]
    local = []
    for entry in reply.get("local") if isinstance(reply.get("local"), list) else []:
        if isinstance(entry, dict) and entry.get("country") in EDITION and isinstance(entry.get("query"), str):
            query = " ".join(entry["query"].split())
            if query and len(query) <= MAX_QUERY:
                local.append(Search("local", query, entry["country"]))
    searches = ([Search(tool, q) for q in queries for tool in ("news", "gdelt")] + local[:MAX_LOCAL]
                + [Search("web", q) for q in queries[:MAX_WEB]])
    caps = tuple(c for c in MARKET_CAP_BANDS if c in _strings(reply.get("market_caps")))
    exclude = tuple(_unique(_strings(reply.get("exclude"))))[:MAX_EXCLUDE]
    return Plan(tuple(searches), caps, exclude)


def _strings(value: object) -> list[str]:
    return [" ".join(v.split()) for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def _unique(items) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
    return out


def _filter_notes(plan: Plan) -> list[str]:
    notes = []
    if plan.market_caps:
        notes.append(f"only {', '.join(c.lower() + 's' for c in plan.market_caps)}")
    if plan.exclude:
        notes.append(f"leaving out {', '.join(plan.exclude)}")
    return notes


def _interleave(found: list[list[int]]) -> list[int]:
    """Each search's items in turn, so every search reaches the naming call."""
    out: dict[int, None] = {}
    for rank in range(max((len(f) for f in found), default=0)):
        for ids in found:
            if rank < len(ids):
                out.setdefault(ids[rank])
    return list(out)


def _item_text(e: Evidence) -> str:
    day = e.published.date().isoformat() if e.published else "undated"
    return f"{day} {e.outlet or 'unknown outlet'}: {e.title}" + "".join(f"\n  - {f}" for f in e.facts)


class _Shortlist(Screen):
    """Screens named companies in rank order. Never-listed and removed companies are left off;
    watched and discovered ones stay on, marked, with no price check."""

    def __init__(self, ctx: AppContext, plan: Plan) -> None:
        super().__init__(ctx, MAX_PRICE_CHECKS)
        self._plan = plan

    async def screen(self, mentions: list[Mention], item_ids: list[int]) -> list[ShortlistItem]:
        out: list[ShortlistItem] = []
        for m in mentions:
            evidence = tuple(item_ids[i] for i in m.headlines)
            why = m.why or "named in the news found for the question"
            profile, why_not = self.identify(m.name)
            if profile is None:
                if why_not:
                    out.append(ShortlistItem(len(out) + 1, m.name, None, None, None, why, evidence, why_not))
                continue
            listing, why_not = await self._check(profile)
            out.append(ShortlistItem(len(out) + 1, m.name, listing, profile.company_key, profile.name, why, evidence,
                                     why_not))
        return out

    async def _check(self, profile: ListingProfile) -> tuple[str | None, str | None]:
        if self.removed(profile):
            return None, REMOVED
        if why_not := self._filtered(profile):
            return None, why_not
        if self.watched(profile) or self.listed(profile):
            return profile.primary_symbol, None
        if not self.checks_left:
            return None, "not checked: too many companies to price-check in one answer"
        return await self.priced_line(profile)

    def _filtered(self, p: ListingProfile) -> str | None:
        caps = self._plan.market_caps
        if caps and p.market_cap not in caps:
            return f"not the size asked for ({p.market_cap or 'size unknown'})"
        for word in self._plan.exclude:
            if (field := _matching(word, (p.sector, p.industry_group, p.industry))) is not None:
                return f"in {field}, which the question leaves out"
        return None


def _matching(word: str, fields) -> str | None:
    """The first field naming `word`, singular or plural ("utility" finds "Utilities")."""
    stem = re.sub(r"(ies|y|s)$", "", word.casefold().strip())
    if len(stem) < 3:
        return None
    return next((f for f in fields if f and stem in f.casefold()), None)


def _view(ctx: AppContext, repo: QuestionRepo, q: Question, a: Answer) -> AnswerView:
    before = _compared(repo, q, a)
    known = {i.company_key for i in before.shortlist} if before else set()
    shortlist = a.shortlist
    articles = repo.evidence(sorted({e for i in shortlist for e in i.evidence})) if shortlist else {}
    screen = Screen(ctx, 0)
    lines = []
    for i in shortlist:
        profile = screen.profile(i.symbol) if i.symbol else None
        lines.append(Line(i, tuple(articles[e] for e in i.evidence if e in articles),
                          before is not None and i.company_key not in known,
                          bool(profile and screen.watched(profile)), bool(profile and screen.listed(profile))))
    now = {i.company_key for i in shortlist}
    gone = tuple(i for i in before.shortlist if i.company_key not in now) if before else ()
    return AnswerView(a, tuple(lines), tuple(i for i in a.items if i.skipped), gone, before and before.id)


def _compared(repo: QuestionRepo, q: Question, a: Answer) -> Answer | None:
    """The question's newest answer done before this one, else for a follow-up its question's."""
    earlier = [x for x in repo.answers(q.id) if x.id < a.id and x.status == DONE]
    if earlier:
        return earlier[0]
    if q.refines is not None:
        return next((x for x in repo.answers(q.refines) if x.status == DONE and x.id < a.id), None)
    return None


def _question(repo: QuestionRepo, question_id: int) -> Question:
    q = repo.get(question_id)
    if q is None:
        raise UnknownQuestion(f"no question {question_id}")
    return q


def _chain(repo: QuestionRepo, q: Question) -> list[Question]:
    chain = [q]
    while chain[0].refines is not None and (parent := repo.get(chain[0].refines)) is not None:
        chain.insert(0, parent)
    return chain


def _asked(repo: QuestionRepo, q: Question) -> str:
    first, *follow_ups = _chain(repo, q)
    return first.text + "".join(f" Follow-up: {f.text}" for f in follow_ups)


def _model_label(model: ChatModel) -> str:
    return f"{model.name} {model.model}"


def _now(ctx: AppContext) -> str:
    return to_utc_iso(ctx.clock.utcnow())

