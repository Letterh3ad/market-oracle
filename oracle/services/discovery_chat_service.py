"""Discovery chat: a question about which companies fit ("which European companies gain from
grid batteries?") answered with a ranked shortlist. The model plans the searches and names the
companies the news shows, citing the items it read; which listing a name means, and whether it
may be suggested at all, is decided in code as for discovery runs. Questions and every answer
are saved. A follow-up is a question of its own that refines another; a rerun answers a question
again and says what changed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from oracle.net.gate import SourceGate
from oracle.research.llm import ChatModel, ModelError
from oracle.research.mentions import (
    QUESTION_SCHEMA, Mention, item_text, item_words, parse_mentions, question_prompt,
)
from oracle.research.queries import EDITION, MAX_QUERY
from oracle.research.tools import ToolError
from oracle.research.web import DdgsSearcher, WebSearcher
from oracle.services.candidate_screen import REMOVED, Screen
from oracle.services.context import AppContext
from oracle.services.auto_validation import auto_add
from oracle.services.discovery_service import DISCOVERED_LIST, SOURCE_QUESTION, Added, add_by_hand, list_full
from oracle.services.errors import (
    InvalidQuery, JobAlreadyRunning, ModelUnavailable, SearchFailed, ServiceError, UnknownQuestion,
)
from oracle.services.http import open_client
from oracle.services.jobs_service import job_statuses, single_flight
from oracle.services.links_service import SOURCE_CHAT, LinkFound, record_links
from oracle.services.models_service import RunModel, resolve_stages
from oracle.services.research_tools import GdeltSearch, LocalNewsSearch, NewsSearch, Reader, Shelf, WebSearch
from oracle.services.research_tools.reading import TRY_ANOTHER
from oracle.store.db import to_utc_iso
from oracle.store.discoveries import DiscoveryEvidence
from oracle.store.job_history import INTERRUPTED
from oracle.store.questions import DONE, FAILED, RUNNING, Answer, Question, QuestionRepo, ShortlistItem
from oracle.store.research import evidence_of, note_model
from oracle.store.universe import CLASSIFICATION_LEVELS, ListingProfile, UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.universe.listings import MARKET_CAP_BANDS

__all__ = [
    "AnswerView", "AutoRun", "Line", "Question", "QuestionLine", "QuestionView", "add_to_discovered", "answer",
    "answer_auto_questions", "answer_progress", "ask", "new_question", "question", "questions", "saved_question",
    "TYPICAL_ANSWER_MIN", "set_auto", "sweep_interrupted_answers",
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
# About how long one answer takes with a local model; a question is not started with less left.
TYPICAL_ANSWER_MIN = 5
# Notes taken for a question are kept apart from the same model's notes for research, and from
# other questions': "not about" means not about what that question asks. A follow-up reads
# for its root question, so the two share notes.
NOTE_KEY = "#q{root}"
# Words that name no sector or industry in an exclude ("oil and gas sector").
_NOT_A_SECTOR = frozenset({"and", "or", "the", "of", "a", "an", "sector", "sectors", "industry", "industries",
                           "companies", "company", "stocks", "shares", "firms", "no", "not", "any"})
_WORDS = re.compile(r"[a-z0-9]+")
# Everyday words for what the universe names otherwise, after plurals are folded.
_ALIASES = {"tech": "technology", "pharma": "pharmaceutical", "biotech": "biotechnology", "insurer": "insurance",
            "chip": "semiconductor", "chipmaker": "semiconductor", "healthcare": "health care",
            "automaker": "automobile", "car": "automobile", "cars": "automobile", "auto": "automobile",
            "autos": "automobile", "telecom": "telecommunication", "telecoms": "telecommunication"}
# "Tech" alone means the sector, not Health Care Technology or Technology Hardware.
_WHOLE = {frozenset({"technology"}): frozenset({"information", "technology"})}

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
    discovered_as: str | None
    """The discovered list's line of the company now, or None when it is not on it."""


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
              searcher: WebSearcher | None = None, profile: str | None = None) -> AnswerView:
    return await answer(ctx, new_question(ctx, text, refines).id, model, searcher, profile=profile)


async def answer(ctx: AppContext, question_id: int, model: ChatModel | None = None,
                 searcher: WebSearcher | None = None, deadline: datetime | None = None,
                 profile: str | None = None) -> AnswerView:
    """Answer a saved question (again): plan, search, read, name, screen, save, and for an auto
    question validate and add, no company validated after `deadline`. The model failing to answer
    fails the answer, which stays saved with why and whatever shortlist it had. Every step runs on
    the discovery slot's profile, or on `profile` (a run's picked model)."""
    repo = QuestionRepo(ctx.conn)
    q = _question(repo, question_id)
    asked = _asked(repo, q)
    resolved = resolve_stages(ctx, ("discovery",), profile, model)["discovery"]
    model = resolved.model
    with single_flight(ctx, JOB, LEASE_S):
        answer_id = repo.start(q.id, _model_label(model), _now(ctx))
        notes: list[str] = []
        items: list[ShortlistItem] = []
        note_key = note_model(model.name, model.model) + NOTE_KEY.format(root=_chain(repo, q)[0].id)
        try:
            items = await _answer(ctx, repo, answer_id, asked, resolved, searcher or DdgsSearcher(), notes,
                                  note_key)
            if q.auto:
                await auto_add(ctx, q.id, asked, model, note_key, items, lambda text: repo.set_stage(answer_id, text),
                               notes, deadline)
        except BaseException as exc:
            error = "cancelled" if not isinstance(exc, Exception) else str(exc) or type(exc).__name__
            # Companies already added stay added, so the answer keeps what validation made of them.
            repo.finish(answer_id, FAILED, error, notes, items, _now(ctx))
            if isinstance(exc, ModelError):
                raise ModelUnavailable(str(exc)) from exc
            raise
        repo.finish(answer_id, DONE, None, notes, items, _now(ctx))
    return _view(ctx, repo, q, repo.answer(answer_id))


def set_auto(ctx: AppContext, question_id: int, on: bool) -> Question:
    """Switch a question's auto mode: each answer from then on adds the shortlisted companies
    that pass validation to the discovered list."""
    repo = QuestionRepo(ctx.conn)
    _question(repo, question_id)
    repo.set_auto(question_id, on)
    return repo.get(question_id)


@dataclass(frozen=True)
class AutoRun:
    answered: tuple[AnswerView, ...] = ()
    failed: tuple[str, ...] = ()
    """Questions whose answer failed, with why."""
    note: str | None = None
    """Why none was answered."""


async def answer_auto_questions(ctx: AppContext, limit: int, model: ChatModel | None = None,
                                searcher: WebSearcher | None = None, deadline: datetime | None = None) -> AutoRun:
    """Answer up to `limit` auto questions again, the one answered longest ago first; a failed
    answer is noted and the next one goes on. Nothing is asked while the discovered list is full,
    and no question is started unless a typical answer fits before `deadline`."""
    auto = QuestionRepo(ctx.conn).auto()
    if not auto:
        return AutoRun(note="no question is on auto")
    on_list, cap = len(WatchlistRepo(ctx.conn).items(DISCOVERED_LIST)), ctx.config.discovery.max_total
    if on_list >= cap:
        return AutoRun(note=list_full(on_list, cap))
    answered, failed = [], []
    for q in auto[:limit]:
        if deadline and deadline - ctx.clock.utcnow() < timedelta(minutes=TYPICAL_ANSWER_MIN):
            return AutoRun(tuple(answered), tuple(failed), "no time left for another question")
        try:
            answered.append(await answer(ctx, q.id, model, searcher, deadline))
        except JobAlreadyRunning:
            return AutoRun(tuple(answered), tuple(failed), "another question is being answered: the rest wait")
        except ServiceError as exc:
            failed.append(f"question {q.id}: {exc}")
    return AutoRun(tuple(answered), tuple(failed))


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


async def add_to_discovered(ctx: AppContext, answer_id: int, n: int) -> Added:
    """A shortlisted company onto the discovered list, with the whole question (follow-ups
    included) as why."""
    repo = QuestionRepo(ctx.conn)
    item = repo.item(answer_id, n)
    if item is None:
        raise UnknownQuestion(f"no company {n} on answer {answer_id}")
    if item.skipped or item.symbol is None:
        raise InvalidQuery(f"{item.name} was left off the shortlist: {item.skipped}")
    asked = _asked(repo, repo.get(repo.answer(answer_id).question_id))
    return await add_by_hand(ctx, item.symbol, SOURCE_QUESTION, asked, "answer", item.evidence)


def sweep_interrupted_answers(ctx: AppContext) -> None:
    """Mark answers a stopped app left running as failed, unless another process is answering."""
    if any(s.job == JOB and s.stale_reason is None for s in job_statuses(ctx)):
        return
    QuestionRepo(ctx.conn).fail_running(INTERRUPTED, _now(ctx))


async def _answer(ctx: AppContext, repo: QuestionRepo, answer_id: int, asked: str, resolved: RunModel,
                  searcher: WebSearcher, notes: list[str], note_key: str) -> list[ShortlistItem]:
    model = resolved.model

    def stage(text: str) -> None:
        repo.set_stage(answer_id, text)

    stage("planning searches")
    plan = await _plan(model, asked)
    repo.set_searches(answer_id, [s.label for s in plan.searches])
    classifications = UniverseRepo(ctx.conn).classifications()
    exclusions = [e for x in plan.exclude if (e := exclusion(x, classifications))]
    notes.extend(_filter_notes(plan, exclusions))
    found: list[list[int]] = []
    failed: list[str] = []
    shelf = Shelf(ctx.conn, ctx.clock, lambda ids, _query: found[-1].extend(ids))
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
                failed.append(f"{s.label} ({str(exc).removesuffix(TRY_ANOTHER)})")
        # An outage is not an answer: saved as done it would mark everything "gone" and, once
        # the news is back, everything "new".
        if len(failed) == len(plan.searches):
            raise SearchFailed(f"no search could run: {'; '.join(failed)}")
        notes.extend(f"could not search {f}" for f in failed)
        ids = _interleave(found)[:MAX_ITEMS]
        if not ids:
            notes.append("the searches found nothing to read")
            return []
        reader = Reader(ctx.conn, ctx.clock, client, searcher, gate, ctx.config.research.contact, model,
                        f'companies that answer "{asked}"', resolved.context, note_key)
        newest_first = sorted(evidence_of(ctx.conn, ids, note_key),
                              key=lambda e: (e.published is not None, e.published), reverse=True)
        await reader.read(newest_first, READ, None, lambda n, total: stage(f"reading {n} of {total}"))
    stage("naming companies")
    evidence = evidence_of(ctx.conn, ids, note_key)
    reply = await model.reply(question_prompt(asked, [item_text(e) for e in evidence]), QUESTION_SCHEMA)
    # A name must be in the item's own words; an outlet ("Siemens Stiftung News") names no answer.
    mentions = parse_mentions(reply, [item_words(e) for e in evidence])
    if not mentions:
        notes.append("the model named no company the news shows")
    # More items behind a name first; the model's own order among equals.
    ranked = sorted(mentions, key=lambda m: -len(m.headlines))
    stage("checking listings")
    shortlist = _Shortlist(ctx, plan, exclusions)
    items = await shortlist.screen(ranked, [e.id for e in evidence])
    if kept := record_links(ctx, _links(shortlist, ranked, [e.id for e in evidence]), SOURCE_CHAT):
        notes.append(f"kept {kept} link{'' if kept == 1 else 's'} between companies the news states "
                     "(shown on their Company pages)")
    return items


def _links(screen: Screen, mentions: list[Mention], item_ids: list[int]) -> list[LinkFound]:
    """The links the model says an item states, both companies resolved, with the items naming
    both in one line as evidence."""
    return [LinkFound(other.primary_symbol, named.primary_symbol, m.relation, tuple(item_ids[i] for i in m.link_items))
            for m in mentions if m.related_to
            and (other := screen.resolve(m.related_to)) and (named := screen.resolve(m.name))]


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


def _filter_notes(plan: Plan, exclusions: list[Exclusion]) -> list[str]:
    """What the filters do, and which excludes name nothing the universe holds (they leave nothing out)."""
    notes = []
    if plan.market_caps:
        notes.append(f"only {', '.join(c.lower() + 's' for c in plan.market_caps)}")
    known = [e.text for e in exclusions]
    if known:
        notes.append(f"leaving out {', '.join(known)}")
    notes.extend(f'"{x}" was not recognised as a sector or industry, so nothing was left out for it'
                 for x in plan.exclude if x not in known)
    return notes


def _interleave(found: list[list[int]]) -> list[int]:
    """Each search's items in turn, so every search reaches the naming call."""
    out: dict[int, None] = {}
    for rank in range(max((len(f) for f in found), default=0)):
        for ids in found:
            if rank < len(ids):
                out.setdefault(ids[rank])
    return list(out)


class _Shortlist(Screen):
    """Screens named companies in rank order. Never-listed and removed companies are left off;
    watched and discovered ones stay on, marked, with no price check."""

    def __init__(self, ctx: AppContext, plan: Plan, exclusions: list[Exclusion]) -> None:
        super().__init__(ctx, MAX_PRICE_CHECKS)
        self._plan = plan
        self._exclusions = exclusions

    async def screen(self, mentions: list[Mention], item_ids: list[int]) -> list[ShortlistItem]:
        """Shortlisted first, numbered from 1 in rank order, then those left off."""
        out: list[ShortlistItem] = []
        for m in mentions:
            evidence = tuple(item_ids[i] for i in m.headlines)
            why = m.why or "named in the news found for the question"
            profile, why_not = self.identify(m.name)
            if profile is None:
                if why_not:
                    out.append(ShortlistItem(0, m.name, None, None, None, why, evidence, why_not))
                continue
            listing, why_not = await self._check(profile)
            out.append(ShortlistItem(0, m.name, listing, profile.company_key, profile.name, why, evidence, why_not))
        ranked = [i for i in out if i.skipped is None] + [i for i in out if i.skipped]
        return [replace(i, n=n) for n, i in enumerate(ranked, 1)]

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
        for rule in self._exclusions:
            if (value := rule.leaves_out(p)) is not None:
                return f"in {value}, which the question leaves out"
        return None


@dataclass(frozen=True)
class Exclusion:
    """An exclude as the universe's classifications at one level."""

    text: str
    level: str
    """A ListingProfile field: industry, industry_group or sector."""
    values: frozenset[str]

    def leaves_out(self, p: ListingProfile) -> str | None:
        value = getattr(p, self.level)
        return value if value in self.values else None


def exclusion(text: str, classifications: dict[str, frozenset[str]]) -> Exclusion | None:
    """What an exclude leaves out: the classifications holding every word of it, singular or
    plural and in everyday words ("utility" finds "Utilities", "chipmakers" finds "Semiconductors");
    one shared word is not enough ("consumer staples" is not "Consumer Discretionary"). Only the
    most specific level is used, an exact name before one merely holding the words, so "biotech"
    leaves out Biotechnology, not the Pharmaceuticals beside it in their combined group, and
    "media" leaves out Media, not Interactive Media & Services. Short of an exact name, a sector
    or group whose name starts with the words ("consumer" is both Consumer sectors, "telecom"
    Telecommunication Services) comes before any name holding them somewhere. None when
    nothing matches."""
    words = frozenset(w for word in _words(text) for w in _ALIASES.get(word, word).split()) - _NOT_A_SECTOR
    wanted = _WHOLE.get(words, words)
    if not wanted:
        return None
    tries = ([(level, lambda v: _words(v) == wanted) for level in CLASSIFICATION_LEVELS]
             + [(level, lambda v: set(_word_list(v)[:len(wanted)]) == wanted)
                for level in reversed(CLASSIFICATION_LEVELS[1:])]
             + [(level, lambda v: wanted <= _words(v)) for level in CLASSIFICATION_LEVELS])
    for level, matches in tries:
        if found := frozenset(v for v in classifications.get(level, ()) if matches(v)):
            return Exclusion(text, level, found)
    return None


def _words(text: str) -> set[str]:
    return set(_word_list(text))


def _word_list(text: str) -> list[str]:
    """Words in order, singular, without the ones that name no sector."""
    return [s for w in _WORDS.findall(text.casefold()) if (s := _singular(w)) not in _NOT_A_SECTOR]


def _singular(word: str) -> str:
    # Short words are left alone: "gas" is no plural.
    if len(word) <= 4:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


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
                          bool(profile and screen.watched(profile)), profile and screen.listed_as(profile)))
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

