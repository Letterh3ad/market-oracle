"""Discovery chat: a question about which companies fit ("which European companies gain from
grid batteries?") answered with a ranked shortlist. The model plans the searches and names the
companies the news shows, citing the items it read; which listing a name means, and whether it
may be suggested at all, is decided in code as for discovery runs. Questions and every answer
are saved. A follow-up is a question of its own that refines another; a rerun answers a question
again and says what changed."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from oracle.net.gate import SourceGate
from oracle.research.llm import ChatModel, ModelError
from oracle.research.mentions import (
    MAX_ANSWERS, QUESTION_SCHEMA, Mention, item_text, item_words, parse_mentions, question_prompt,
)
from oracle.research.notes import listed
from oracle.research.queries import EDITION, MAX_QUERY
from oracle.research.tools import ToolError
from oracle.research.trace import Evidence
from oracle.research.web import DdgsSearcher, WebSearcher
from oracle.services.business_fit import NamedIn
from oracle.services.candidate_screen import REMOVED, Screen
from oracle.services.context import AppContext
from oracle.services.auto_validation import auto_add
from oracle.services.discovery_service import DISCOVERED_LIST, SOURCE_QUESTION, Added, add_by_hand, list_full
from oracle.services.errors import (
    InvalidQuery, JobAlreadyRunning, ModelUnavailable, SearchFailed, ServiceError, UnknownQuestion,
)
from oracle.services.http import open_client
from oracle.services.industry_words import Classes, classes
from oracle.services.jobs_service import job_statuses, single_flight
from oracle.services.links_service import SOURCE_CHAT, LinkFound, record_links
from oracle.services.models_service import RunModel, check_model_profile, resolve_stages
from oracle.services.research_tools import GdeltSearch, LocalNewsSearch, NewsSearch, Reader, Shelf, WebSearch
from oracle.services.research_tools.reading import TRY_ANOTHER
from oracle.services.universe_screen import Filters, Screened, Screening, plan_filters, screen_universe
from oracle.store.db import to_utc_iso
from oracle.store.discoveries import DiscoveryEvidence
from oracle.store.job_history import INTERRUPTED
from oracle.store.questions import DONE, FAILED, RUNNING, Answer, Question, QuestionRepo, ShortlistItem
from oracle.store.research import evidence_of, note_model
from oracle.store.universe import ListingProfile, UniverseRepo
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
# Bounds on one answer: planned news queries (each searched on Google News and the web, the
# first on GDELT too), local editions, items the naming call reads, articles read in full,
# price checks.
MAX_QUERIES = 4
MAX_LOCAL = 2
# GDELT locks out quick repeats (429, then its circuit opens): a second search in one answer failed.
MAX_GDELT = 1
MAX_ITEMS = 24
READ = 4
# Every company the naming call may name (a list page's can fill it) gets its check.
MAX_PRICE_CHECKS = MAX_ANSWERS
MAX_EXCLUDE = 5
MAX_INDUSTRIES = 3
MAX_REGIONS = 5
# Companies the universe screen adds to one answer, and the price checks it adds to the answer's
# (named companies are checked first; a screened one without recent prices is passed over for the next).
MAX_SCREENED = 15
MAX_SCREEN_CHECKS = 20
# About how long one answer takes with a local model; a question is not started with less left.
TYPICAL_ANSWER_MIN = 5
# Notes taken for a question are kept apart from the same model's notes for research, and from
# other questions': "not about" means not about what that question asks. A follow-up reads
# for its root question, so the two share notes.
NOTE_KEY = "#q{root}"

PLAN = """You plan news searches to answer an investor's question about which listed companies fit it.
Reply with JSON:
"queries": up to {queries} short English news search queries (under 8 words each) likely to find headlines naming such companies. No dates, no words like "latest" or "news". When the question asks which companies fit, make one a search for a page listing them ("list of European chipmakers", "top battery makers").
"local": when the question names a country or region with its own language, up to {local} searches written in that language, each with its "country" from the list; else empty.
"market_caps": the company sizes the question asks for, from the list; empty for any size.
"exclude": sectors or industries the question rules out, a word or two each; empty for none.
"industries": up to {industries} industries the companies asked about are in, each one of these names where one fits, else a word or two: {names}. Empty when the question names no industry (e.g. "who supplies Nvidia").
"regions": the countries or regions the companies must be based in ("Europe", "Germany", "the Nordics"); empty for anywhere."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {"type": "array", "maxItems": MAX_QUERIES, "items": {"type": "string", "maxLength": MAX_QUERY}},
        "local": {"type": "array", "maxItems": MAX_LOCAL, "items": {"type": "object", "properties": {
            "country": {"type": "string", "enum": sorted(EDITION)},
            "query": {"type": "string", "maxLength": MAX_QUERY}}, "required": ["country", "query"]}},
        "market_caps": {"type": "array", "items": {"type": "string", "enum": list(MARKET_CAP_BANDS)}},
        "exclude": {"type": "array", "maxItems": MAX_EXCLUDE, "items": {"type": "string", "maxLength": 40}},
        "industries": {"type": "array", "maxItems": MAX_INDUSTRIES, "items": {"type": "string", "maxLength": 40}},
        "regions": {"type": "array", "maxItems": MAX_REGIONS, "items": {"type": "string", "maxLength": 40}},
    },
    "required": ["queries", "local", "market_caps", "exclude", "industries", "regions"],
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
    industries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()


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
    check_model_profile(ctx, profile)
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
                                  note_key, deadline)
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
                  searcher: WebSearcher, notes: list[str], note_key: str,
                  deadline: datetime | None = None) -> list[ShortlistItem]:
    model = resolved.model

    def stage(text: str) -> None:
        repo.set_stage(answer_id, text)

    stage("planning searches")
    classifications = UniverseRepo(ctx.conn).classifications()
    plan = await _plan(model, asked, classifications["industry"])
    repo.set_searches(answer_id, [s.label for s in plan.searches])
    exclusions = [e for x in plan.exclude if (e := classes(x, classifications))]
    notes.extend(_filter_notes(plan, exclusions))
    filters, not_screened = plan_filters(ctx, list(plan.industries), list(plan.regions), plan.market_caps)
    notes.extend(not_screened)
    # The plan's industry words are the question's own: a name matched by its start is held to them.
    asked_for_fit = " ".join((asked, *(c.text for c in filters.industries)))
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
        else:
            reader = Reader(ctx.conn, ctx.clock, client, searcher, gate, ctx.config.research.contact, model,
                            f'companies that answer "{asked}"', resolved.context, note_key, lists=True)
            newest_first = sorted(evidence_of(ctx.conn, ids, note_key),
                                  key=lambda e: (e.published is not None, e.published), reverse=True)
            await reader.read(newest_first, READ, None, lambda n, total: stage(f"reading {n} of {total}"))
    ranked: list[Mention] = []
    evidence: list[Evidence] = []
    if ids:
        stage("naming companies")
        evidence = evidence_of(ctx.conn, ids, note_key)
        notes.extend(f'{len(names)} compan{"y" if len(names) == 1 else "ies"} from the list page "{e.title}"'
                     for e in evidence if (names := listed(e.facts)))
        reply = await model.reply(question_prompt(asked, [item_text(e) for e in evidence]), QUESTION_SCHEMA)
        # A name must be in the item's own words; an outlet ("Siemens Stiftung News") names no answer.
        mentions = parse_mentions(reply, [item_words(e) for e in evidence])
        if not mentions:
            notes.append("the model named no company the news shows")
        # More items behind a name first; the model's own order among equals.
        ranked = sorted(mentions, key=lambda m: -len(m.headlines))
    stage("checking listings")
    shortlist = _Shortlist(ctx, plan, exclusions, filters, model, deadline)
    items = await shortlist.screen(ranked, evidence, asked_for_fit, screen_universe(ctx, filters))
    notes.extend(shortlist.screen_notes)
    if kept := record_links(ctx, await _links(shortlist, ranked, evidence, asked_for_fit), SOURCE_CHAT):
        notes.append(f"kept {kept} link{'' if kept == 1 else 's'} between companies the news states "
                     "(shown on their Company pages)")
    return items


async def _links(screen: Screen, mentions: list[Mention], evidence: list[Evidence], asked: str) -> list[LinkFound]:
    """The links the model says an item states, both companies resolved, with the items naming
    both in one line as evidence."""
    links = []
    for m in mentions:
        named_in = _named_in(m, evidence, asked)
        if m.related_to and (other := await screen.resolve(m.related_to, named_in)) and (
                named := await screen.resolve(m.name, named_in)):
            links.append(LinkFound(other.primary_symbol, named.primary_symbol, m.relation,
                                   tuple(evidence[i].id for i in m.link_items)))
    return links


def _named_in(m: Mention, evidence: list[Evidence], asked: str) -> NamedIn:
    return NamedIn(asked, tuple(item_words(evidence[i]) for i in dict.fromkeys((*m.headlines, *m.link_items))))


async def _plan(model: ChatModel, asked: str, industries: Iterable[str]) -> Plan:
    """The model's searches and filters; the question itself searched when it plans none."""
    system = PLAN.format(queries=MAX_QUERIES, local=MAX_LOCAL, industries=MAX_INDUSTRIES,
                         names="; ".join(sorted(industries)))
    reply = await model.reply([{"role": "system", "content": system},
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
    searches = ([Search(tool, q) for n, q in enumerate(queries) for tool in ("news", "gdelt")
                 if tool == "news" or n < MAX_GDELT]
                + local[:MAX_LOCAL] + [Search("web", q) for q in queries])
    caps = tuple(c for c in MARKET_CAP_BANDS if c in _strings(reply.get("market_caps")))
    exclude = tuple(_unique(_strings(reply.get("exclude"))))[:MAX_EXCLUDE]
    industries = tuple(_unique(_strings(reply.get("industries"))))[:MAX_INDUSTRIES]
    regions = tuple(_unique(_strings(reply.get("regions"))))[:MAX_REGIONS]
    return Plan(tuple(searches), caps, exclude, industries, regions)


def _strings(value: object) -> list[str]:
    return [" ".join(v.split()) for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def _unique(items) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
    return out


def _filter_notes(plan: Plan, exclusions: list[Classes]) -> list[str]:
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
    """Screens named companies in rank order, then the universe screen's. Never-listed and
    removed companies are left off; watched and discovered ones stay on, marked, with no price
    check."""

    def __init__(self, ctx: AppContext, plan: Plan, exclusions: list[Classes], filters: Filters, model: ChatModel,
                 deadline: datetime | None = None) -> None:
        super().__init__(ctx, MAX_PRICE_CHECKS + (MAX_SCREEN_CHECKS if filters.usable else 0), model)
        self._deadline = deadline
        self._out_of_time = False
        self._plan = plan
        self._exclusions = exclusions
        self._filters = filters
        self.screen_notes: list[str] = []

    async def screen(self, mentions: list[Mention], evidence: list[Evidence], asked: str,
                     screening: Screening) -> list[ShortlistItem]:
        """Shortlisted first, numbered from 1: companies both named and screened, then those only
        named, in rank order, then those only screened, largest first; then those left off."""
        screened = {s.profile.company_key: s for s in screening.companies}
        out: list[ShortlistItem] = []
        for m in mentions:
            if self._deadline and self._ctx.clock.utcnow() >= self._deadline:
                self.hurry()
            cited = tuple(evidence[i].id for i in m.headlines)
            why = m.why or "named in the news found for the question"
            profile, why_not = await self.identify(m.name, named_in=_named_in(m, evidence, asked))
            if note := self.notes.get(m.name):
                why = f"{note}; {why}"
            if profile is None:
                if why_not:
                    out.append(ShortlistItem(0, m.name, None, None, None, why, cited, why_not))
                continue
            also = screened.pop(profile.company_key, None)
            listing, why_not = await self._check(profile, also and also.line)
            out.append(ShortlistItem(0, m.name, listing, profile.company_key, profile.name, why, cited, why_not,
                                     universe=also and also.evidence))
        only, passed_over = await self._screened_only(list(screened.values()))
        on = [i for i in out if i.skipped is None]
        ranked = ([i for i in on if i.universe] + [i for i in on if not i.universe] + only
                  + [i for i in out if i.skipped])
        if screening.matched:
            shown = sum(1 for i in ranked if i.universe and i.skipped is None)
            passed = f", {passed_over} passed over without recent prices" if passed_over else ""
            passed += "; the rest not checked: the time ran out" if self._out_of_time else ""
            self.screen_notes.append(f"universe screen: {screening.matched} compan"
                                     f"{'y' if screening.matched == 1 else 'ies'} matched "
                                     f"({self._filters.describe()}), {shown} shown{passed}")
        return [replace(i, n=n) for n, i in enumerate(ranked, 1)]

    async def _screened_only(self, companies: list[Screened]) -> tuple[list[ShortlistItem], int]:
        """Up to `MAX_SCREENED` of the screen's companies no headline named, and how many were
        passed over for having no recent prices (they are not listed as left off: no one named
        them)."""
        where = f" in {', '.join(self._filters.places)}" if self._filters.places else ""
        why = f"in the universe's {', '.join(c.text for c in self._filters.industries)}{where}; no news found names it"
        out: list[ShortlistItem] = []
        passed_over = 0
        for s in companies:
            if len(out) == MAX_SCREENED or not self.checks_left:
                break
            if self._deadline and self._ctx.clock.utcnow() >= self._deadline:
                self._out_of_time = True
                break
            profile, _ = await self.identify(s.profile.name, symbol=s.line)
            if profile is None or self.removed(profile) or self._filtered(profile):
                continue
            listing, _ = await self._check(profile, s.line)
            if listing is None:
                passed_over += 1
                continue
            out.append(ShortlistItem(0, profile.name, listing, profile.company_key, profile.name, why, (),
                                     universe=s.evidence))
        return out, passed_over

    async def _check(self, profile: ListingProfile, line: str | None = None) -> tuple[str | None, str | None]:
        """The line to list the company on (`line` when given, else its live one), else why not."""
        if self.removed(profile):
            return None, REMOVED
        if why_not := self._filtered(profile):
            return None, why_not
        if self.watched(profile) or self.listed(profile):
            return line or profile.primary_symbol, None
        if not self.checks_left:
            return None, "not checked: too many companies to price-check in one answer"
        return await self.priced_line(profile, line)

    def _filtered(self, p: ListingProfile) -> str | None:
        caps = self._plan.market_caps
        if caps and p.market_cap not in caps:
            return f"not the size asked for ({p.market_cap or 'size unknown'})"
        for rule in self._exclusions:
            if (value := rule.holds(p)) is not None:
                return f"in {value}, which the question leaves out"
        return None


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

