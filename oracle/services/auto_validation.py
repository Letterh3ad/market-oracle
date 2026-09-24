"""The checks a shortlisted company passes before an auto question adds it to the discovered
list unattended. Everything but relevance is decided in code; relevance is a second, separate
model call that reads only the question, the company and the evidence the answer cited, and
answers yes or no. Checks run cheapest first and stop at the first that fails."""

from __future__ import annotations

from datetime import timedelta

from oracle.market.bars import is_stale
from oracle.research.llm import ChatModel, ModelError
from oracle.research.mentions import item_text, item_words
from oracle.research.trace import Evidence
from oracle.research.web import domain
from oracle.services.candidate_screen import REMOVED, Screen
from oracle.services.context import AppContext
from oracle.services.never_list import is_never, never_company, never_names
from oracle.store.articles import ArticleRepo
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.discoveries import DISCOVERED_LIST, Check, Validation
from oracle.store.questions import ShortlistItem
from oracle.store.research import evidence_of
from oracle.store.universe import ListingProfile, UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.universe.listings import blind_name, is_receipt, normalise_name

__all__ = ["CHECKS", "EVIDENCE_DAYS", "MIN_OUTLETS", "RELEVANCE_SCHEMA", "Validator"]

RESOLVED, LINE, PRICES, NEVER, REMOVED_CHECK, RECEIPT, EVIDENCE, RELEVANCE = (
    "resolved", "line", "prices", "never list", "removed", "receipt", "evidence", "relevance")
CHECKS = (RESOLVED, LINE, PRICES, NEVER, REMOVED_CHECK, RECEIPT, EVIDENCE, RELEVANCE)
"""In the order they run."""
EVIDENCE_DAYS = 90
MIN_OUTLETS = 2
MAX_REASON = 200
# A filing dated a day ahead is a time-zone edge, not the future.
_AHEAD = timedelta(days=1)

RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["yes", "no"]},
                   "reason": {"type": "string", "maxLength": MAX_REASON}},
    "required": ["answer", "reason"],
}

_RELEVANCE = ("Role: relevance_check. You check one company against an investor's question, using only the "
              "numbered news items given. Answer \"yes\" only if the items themselves show that this company fits "
              "the question; answer \"no\" if they do not, if they are about another company, or if you are unsure. "
              "Give a one-line reason.")


class Validator:
    """Validates an answer's shortlisted companies for one auto question."""

    def __init__(self, ctx: AppContext, model: ChatModel, question: str, note_key: str) -> None:
        self._ctx = ctx
        self._model = model
        self._question = question
        self._note_key = note_key
        self._universe = UniverseRepo(ctx.conn)
        self._screen = Screen(ctx, 0)
        self._never = never_names(ctx, user=True)

    async def validate(self, item: ShortlistItem) -> tuple[Validation, tuple[int, ...]]:
        """The checks run on the company, and the evidence the evidence check counted."""
        checks: list[Check] = []
        counted: tuple[int, ...] = ()
        profile = self._universe.profile(item.symbol) if item.symbol else None
        steps = (lambda: self._resolved(item, profile), lambda: self._line(item, profile),
                 lambda: self._prices(item), lambda: self._never_list(item, profile),
                 lambda: self._removed(profile), lambda: self._receipt(item, profile))
        for step in steps:
            checks.append(step())
            if not checks[-1].passed:
                return Validation(tuple(checks)), counted
        evidence, counted = self._evidence(item, profile)
        checks.append(evidence)
        if evidence.passed:
            checks.append(await self._relevance(item, profile))
        return Validation(tuple(checks)), counted

    def _resolved(self, item: ShortlistItem, p: ListingProfile | None) -> Check:
        if p is None or p.company_key != item.company_key:
            return Check(RESOLVED, False, f"{item.symbol} no longer resolves to {item.company or item.name} "
                                          "in the universe")
        return Check(RESOLVED, True, f"{item.name} is {p.name} ({item.symbol}), matched in code")

    def _line(self, item: ShortlistItem, p: ListingProfile) -> Check:
        if item.symbol.upper() in {p.primary_symbol.upper(), (self._screen.live_line(p) or "").upper()}:
            return Check(LINE, True, f"{item.symbol} is its primary or most liquid live line")
        return Check(LINE, False, f"{item.symbol} is not its primary or most liquid live line")

    def _prices(self, item: ShortlistItem) -> Check:
        last = BarRepo(self._ctx.conn).last_date(item.symbol)
        if last is None or is_stale(last, self._ctx.clock.utcnow().date()):
            return Check(PRICES, False, f"no recent prices (last {last or 'never'})")
        return Check(PRICES, True, f"prices up to {last}")

    def _never_list(self, item: ShortlistItem, p: ListingProfile) -> Check:
        if is_never(item.name, self._never) or never_company(self._ctx, p.symbol, user=True):
            return Check(NEVER, False, "on the never list")
        return Check(NEVER, True, "not on the never list")

    def _removed(self, p: ListingProfile) -> Check:
        if self._screen.removed(p):
            return Check(REMOVED_CHECK, False, REMOVED)
        return Check(REMOVED_CHECK, True, "never removed by you")

    def _receipt(self, item: ShortlistItem, p: ListingProfile) -> Check:
        line = next((x.name for x in self._universe.company_listings(p.company_key)
                     if x.symbol.upper() == item.symbol.upper()), None) or p.name
        if is_receipt(line) or is_receipt(p.name):
            return Check(RECEIPT, False, f"{line} is a depositary receipt of a company listed elsewhere")
        if twin := self._watched_twin(p):
            return Check(RECEIPT, False, f"the same company as {twin}, which you watch, under another listing")
        return Check(RECEIPT, True, "its own shares, not another line of a watched company")

    def _watched_twin(self, p: ListingProfile) -> str | None:
        """A watched company of another key with the same name: a receipt line the universe
        keeps as a company of its own."""
        name = blind_name(p.name)
        lists = WatchlistRepo(self._ctx.conn)
        for w in lists.summaries():
            if w.name.casefold() == DISCOVERED_LIST:
                continue
            for i in lists.items(w.name):
                other = self._universe.profile(i.symbol)
                if other and other.company_key != p.company_key and blind_name(other.name) == name:
                    return f"{other.name} ({i.symbol})"
        return None

    def _evidence(self, item: ShortlistItem, p: ListingProfile) -> tuple[Check, tuple[int, ...]]:
        """Items naming the company from `MIN_OUTLETS` outlets within `EVIDENCE_DAYS`: the ones
        the answer cited, and the company's own SEC filings already stored."""
        now = self._ctx.clock.utcnow()
        since = now - timedelta(days=EVIDENCE_DAYS)
        symbols = [x.symbol for x in self._universe.company_listings(p.company_key)]
        filings = ArticleRepo(self._ctx.conn).filings(symbols, to_utc_iso(since))
        names = {n for n in (blind_name(item.name), blind_name(p.name)) if n}
        counted: dict[int, str] = {}
        for e in evidence_of(self._ctx.conn, list(dict.fromkeys((*item.evidence, *filings))), self._note_key):
            if e.published is None or not since <= e.published <= now + _AHEAD:
                continue
            if e.id in filings or _names(e, names):
                counted[e.id] = (e.outlet or domain(e.url) or "unknown").casefold()
        outlets = sorted(set(counted.values()))
        if len(outlets) < MIN_OUTLETS:
            found = f"only {len(outlets)} outlet{'' if len(outlets) == 1 else 's'}" + (
                f" ({', '.join(outlets)})" if outlets else "")
            return Check(EVIDENCE, False, f"{found} named it in the last {EVIDENCE_DAYS} days; "
                                          f"{MIN_OUTLETS} are needed"), ()
        return Check(EVIDENCE, True, f"{len(counted)} items from {len(outlets)} outlets ({', '.join(outlets)}) "
                                     f"in the last {EVIDENCE_DAYS} days"), tuple(counted)

    async def _relevance(self, item: ShortlistItem, p: ListingProfile) -> Check:
        cited = evidence_of(self._ctx.conn, list(item.evidence), self._note_key)
        messages = relevance_prompt(self._question, f"{p.name} ({item.symbol})", [item_text(e) for e in cited])
        try:
            reply = await self._model.reply(messages, RELEVANCE_SCHEMA)
        except ModelError as exc:
            return Check(RELEVANCE, False, f"the check could not run ({exc})")
        answer = reply.get("answer") if isinstance(reply, dict) else None
        reason = reply.get("reason") if isinstance(reply, dict) else None
        reason = " ".join(reason.split())[:MAX_REASON] if isinstance(reason, str) else ""
        if answer not in ("yes", "no") or not reason:
            return Check(RELEVANCE, False, "the model's answer could not be read")
        return Check(RELEVANCE, answer == "yes", f"{answer}: {reason}")


def relevance_prompt(question: str, company: str, items: list[str]) -> list[dict]:
    numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(items, start=1))
    return [{"role": "system", "content": _RELEVANCE},
            {"role": "user", "content": f"Question: {question}\nCompany: {company}\nNews items:\n{numbered}"}]


def _names(e: Evidence, names: set[str]) -> bool:
    text = f" {normalise_name(item_words(e))} "
    return any(f" {n} " in text for n in names)
