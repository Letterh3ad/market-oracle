"""Stock index member lists, read from Wikipedia pages (CC BY-SA 4.0): the companies a market's
main indexes hold, as Yahoo symbols, so the universe can be checked for the home lines it lacks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

SOURCE = "wikipedia"
PAGE_URL = "https://{lang}.wikipedia.org/api/rest_v1/page/html/{title}"
# Wikimedia refuses scripted clients whose User-Agent names no contact (seen 2026-09-24).
USER_AGENT = "market-oracle/0.1 (https://github.com/Letterh3ad/market-oracle)"

SYMBOL_HEADERS = frozenset({"symbol", "ticker", "code", "stock code", "stock symbol", "ticker symbol", "股票代號"})
NAME_HEADERS = frozenset({"company", "name", "security", "company name", "constituent name", "securities name",
                          "名稱"})


@dataclass(frozen=True)
class IndexSpec:
    code: str
    name: str
    title: str
    """The Wikipedia page, as its URL spells it."""
    suffix: str
    """Yahoo's exchange suffix; empty when the page's tickers already carry theirs."""
    lang: str = "en"
    pad: int = 0
    """Numeric codes are zero-padded to this width (Hong Kong's 5 is 0005.HK)."""
    listed: bool = False
    """Members are list items ("Toyota Motor Corp. (TYO: 7203)"), not table rows."""
    venues: tuple[tuple[str, str], ...] = ()
    """(the page's exchange prefix, its suffix) where it differs from `suffix` ("SZSE: 300750")."""


@dataclass(frozen=True)
class Member:
    symbol: str
    name: str
    isin: str | None = None


# Mid caps count as much as large: S&P 400 and 600, FTSE 250 and MDAX sit beside the flagships.
# Brazil and India's midcaps have no member table on Wikipedia; TOPIX Core 30 is inside the Nikkei 225.
INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec("sp500", "S&P 500", "List_of_S%26P_500_companies", ""),
    IndexSpec("sp400", "S&P 400", "List_of_S%26P_400_companies", ""),
    IndexSpec("sp600", "S&P 600", "List_of_S%26P_600_companies", ""),
    IndexSpec("tsx60", "S&P/TSX 60", "S%26P%2FTSX_60", ".TO"),
    IndexSpec("ftse100", "FTSE 100", "FTSE_100_Index", ".L"),
    IndexSpec("ftse250", "FTSE 250", "FTSE_250_Index", ".L"),
    IndexSpec("dax", "DAX", "DAX", ""),
    IndexSpec("mdax", "MDAX", "MDAX", ".DE"),
    IndexSpec("cac40", "CAC 40", "CAC_40", ""),
    IndexSpec("aex", "AEX", "AEX_index", ""),
    IndexSpec("bel20", "BEL 20", "BEL_20", ".BR"),
    IndexSpec("smi", "SMI", "Swiss_Market_Index", ".SW"),
    IndexSpec("ibex35", "IBEX 35", "IBEX_35", ""),
    IndexSpec("ftsemib", "FTSE MIB", "FTSE_MIB", ""),
    IndexSpec("psi", "PSI", "PSI-20", ".LS"),
    IndexSpec("omxs30", "OMX Stockholm 30", "OMX_Stockholm_30", ""),
    IndexSpec("omxh25", "OMX Helsinki 25", "OMX_Helsinki_25", ""),
    IndexSpec("omxc25", "OMX Copenhagen 25", "OMX_Copenhagen_25", ".CO"),
    IndexSpec("obx", "OBX", "OBX_Index", ".OL"),
    IndexSpec("nikkei225", "Nikkei 225", "Nikkei_225", ".T", listed=True),
    IndexSpec("kospi200", "KOSPI 200", "KOSPI_200", ".KS", pad=6),
    IndexSpec("twse50", "Taiwan 50", "臺灣50指數", ".TW", lang="zh"),
    IndexSpec("hsi", "Hang Seng", "Hang_Seng_Index", ".HK", pad=4),
    IndexSpec("csi300", "CSI 300", "CSI_300_Index", ".SS", pad=6, venues=(("SZSE", ".SZ"),)),
    IndexSpec("nifty50", "Nifty 50", "NIFTY_50", ".NS"),
    IndexSpec("niftynext50", "Nifty Next 50", "NIFTY_Next_50", ".NS"),
    IndexSpec("asx200", "S&P/ASX 200", "S%26P%2FASX_200", ".AX"),
    IndexSpec("nzx50", "S&P/NZX 50", "S%26P%2FNZX_50_Index", ".NZ"),
    IndexSpec("sti", "Straits Times", "Straits_Times_Index", ".SI"),
    IndexSpec("klci", "FTSE Bursa Malaysia KLCI", "FTSE_Bursa_Malaysia_KLCI", ".KL", pad=4),
    IndexSpec("set50", "SET50", "SET50_Index", ".BK"),
    IndexSpec("lq45", "LQ45", "LQ45", ".JK"),
)
BY_CODE = {spec.code: spec for spec in INDEXES}


def page_url(spec: IndexSpec) -> str:
    return PAGE_URL.format(lang=spec.lang, title=spec.title)


class Unreadable(Exception):
    """The page no longer holds a member table this reader recognises."""


def parse_members(spec: IndexSpec, html: str) -> list[Member]:
    """The index's members, first seen first, one per Yahoo symbol."""
    reader = _Reader()
    reader.feed(html)
    found = _from_items(spec, reader.items) if spec.listed else _from_tables(spec, reader.tables)
    if not found:
        raise Unreadable(f"{spec.name}: no member {'list' if spec.listed else 'table'} on {page_url(spec)}")
    return list({m.symbol: m for m in reversed(found)}.values())[::-1]


_TYO = re.compile(r"^(?P<name>.+?)\s*\(\s*TYO\s*:\s*(?P<code>[0-9A-Z]{4})\s*\)")


def _from_items(spec: IndexSpec, items: list[str]) -> list[Member]:
    return [Member(yahoo_symbol(spec, m["code"]), m["name"].rstrip(" ,")) for m in map(_TYO.match, items) if m]


def _from_tables(spec: IndexSpec, tables: list[list[list[str]]]) -> list[Member]:
    for rows in tables:
        head = [_header(c) for c in rows[0]] if rows else []
        names = [j for j, h in enumerate(head) if h in NAME_HEADERS]
        # A page listing two members a row (Taiwan 50) pairs each symbol with the name after it.
        pairs = [(i, next((j for j in names if j > i), names[0])) for i, h in enumerate(head)
                 if h in SYMBOL_HEADERS and names]
        if not pairs:
            continue
        isin = next((i for i, h in enumerate(head) if h == "isin"), None)
        out = []
        for row in rows[1:]:
            for s, n in pairs:
                if max(s, n) < len(row) and (symbol := yahoo_symbol(spec, row[s])):
                    out.append(Member(symbol, row[n], _isin(row[isin]) if isin is not None and isin < len(row)
                                      else None))
        return out
    return []


_TICKER = re.compile(r"^[A-Z0-9&][A-Z0-9&\-]*(\.[A-Z]{1,3})?$")


def yahoo_symbol(spec: IndexSpec, cell: str) -> str | None:
    """A page's ticker as Yahoo spells it ("BRK.B" is BRK-B, "SEHK: 5" is 0005.HK), or None
    for a cell holding none."""
    *venue, code = (part.strip().upper() for part in re.split(r"[:：]", _NOTE.sub("", cell)))
    if spec.suffix:
        # A share class is dashed (BT.A is BT-A.L, "MAERSK B" MAERSK-B.CO); "BP." is BP.L.
        code = code.rstrip(".").replace(".", "-").replace(" ", "-")
        if spec.pad and code.isdigit():
            code = code.zfill(spec.pad)
        code += dict(spec.venues).get(venue[-1] if venue else "", spec.suffix)
    elif code.rpartition(".")[2] not in _YAHOO_SUFFIXES:
        # No exchange suffix: a US share class (BF.B is BF-B).
        code = code.replace(".", "-").replace(" ", "-")
    return code if _TICKER.match(code) else None


# Suffixes the pages whose tickers already carry one use.
_YAHOO_SUFFIXES = frozenset({"DE", "PA", "AS", "MC", "MI", "ST", "HE", "BR", "L", "SW", "CO", "OL", "LS", "IR", "VI"})
_NOTE = re.compile(r"\[[^\]]*\]")
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _isin(text: str) -> str | None:
    text = text.strip().upper()
    return text if _ISIN.match(text) else None


def _header(text: str) -> str:
    return " ".join(_NOTE.sub("", text).casefold().split())


class _Reader(HTMLParser):
    """Top-level `wikitable` rows as cell texts, and every list item's text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.items: list[str] = []
        self._tables: list[bool] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._item: list[list[str]] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("style", "script", "sup"):
            self._skip += 1
        elif tag == "table":
            wiki = "wikitable" in (dict(attrs).get("class") or "")
            self._tables.append(wiki and not self._tables)
            if self._tables[-1]:
                self.tables.append([])
        elif tag == "tr" and self._tables == [True]:
            self._row = []
        elif tag in ("td", "th") and self._row is not None and len(self._tables) == 1:
            self._cell = []
        elif tag == "li":
            self._item.append([])
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script", "sup"):
            self._skip = max(0, self._skip - 1)
        elif tag == "table" and self._tables:
            self._tables.pop()
        elif tag in ("td", "th") and self._cell is not None and len(self._tables) == 1:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._tables == [True]:
            self.tables[-1].append(self._row)
            self._row = None
        elif tag == "li" and self._item:
            self.items.append(" ".join("".join(self._item.pop()).split()))

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        for item in self._item:
            item.append(data)
