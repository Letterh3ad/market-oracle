"""Full texts for the literature index (research-lab 07): legal open-access copies named by Unpaywall and
Semantic Scholar, PDF text, and the ids and title lines that match a PDF the user drops in the inbox to its
source. Shadow libraries are refused wherever a link comes from."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from pathlib import PurePath
from urllib.parse import urlsplit

from oracle.lab.matching import source_key

SHADOW = re.compile(r"(^|\.)(sci-?hub|libgen|gen\.lib\.rus\.ec|library\.lol|annas-archive|z-?lib(rary)?|1lib|b-ok|"
                    r"booksc|singlelogin|libstc)(\.|$)", re.I)
"""Hosts of shadow libraries: never fetched (spec decision 4)."""
_IDS = re.compile(r"10\.\d{4,9}/[^\s\"<>]+|arxiv\.org/(?:abs|pdf)/\d{4}\.\d{4,5}|arxiv:?\s*\d{4}\.\d{4,5}|"
                  r"ssrn\.com/\S*?abstract(?:_id)?=\d{4,}", re.I)
TITLE_LINES = 8
"""Lines from the top of page 1 a title may sit in (after a journal header or a running head)."""


class NotAPdf(ValueError):
    pass


@dataclass(frozen=True)
class Copy:
    url: str
    via: str
    """unpaywall or semantic-scholar."""
    licence: str
    version: str


@dataclass(frozen=True)
class PdfText:
    pages: tuple[str, ...]
    title: str | None
    """The document's own title field, when it has a real one."""

    @property
    def text(self) -> str:
        return "\n\n".join(self.pages)


def is_shadow(url: str) -> bool:
    return bool(SHADOW.search(urlsplit(url).hostname or ""))


def api_ids(key: str) -> tuple[str | None, str | None]:
    """The DOI Unpaywall takes and the id Semantic Scholar takes for a source key; None when it has none."""
    kind, _, rest = key.partition(":")
    if kind == "arxiv":
        return f"10.48550/arxiv.{rest}", f"ARXIV:{rest}"
    doi = {"doi": rest, "ssrn": f"10.2139/ssrn.{rest}", "nber": f"10.3386/{rest}"}.get(kind)
    return (doi, f"DOI:{doi}") if doi else (None, None)


def unpaywall_copies(body: dict) -> list[Copy]:
    """Every location with a PDF link, the best first; a landing page alone is no full text."""
    locations = body.get("oa_locations") or []
    best = body.get("best_oa_location")
    if best:
        locations = [best, *(x for x in locations if x != best)]
    return _distinct(Copy(x["url_for_pdf"], "unpaywall", x.get("license") or "", x.get("version") or "")
                     for x in locations if x.get("url_for_pdf"))


S2_CLOSED = "CLOSED"
"""Semantic Scholar's status for a copy it found but whose licence it cannot vouch for (a course page's upload)."""


def s2_copies(body: dict) -> list[Copy]:
    pdf = body.get("openAccessPdf") or {}
    if not pdf.get("url") or (pdf.get("status") or "").upper() == S2_CLOSED:
        return []
    return _distinct([Copy(pdf["url"], "semantic-scholar", pdf.get("license") or "", pdf.get("status") or "")])


def _distinct(copies) -> list[Copy]:
    out: list[Copy] = []
    for c in copies:
        if not is_shadow(c.url) and all(c.url != o.url for o in out):
            out.append(c)
    return out


def pdf_text(data: bytes) -> PdfText:
    if data.lstrip()[:5] != b"%PDF-":
        raise NotAPdf("not a PDF (a sign-in page or a landing page?)")
    import pypdf
    from pypdf.errors import DependencyError, FileNotDecryptedError, PyPdfError

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages = tuple(_page(p) for p in reader.pages)
        title = (reader.metadata.title if reader.metadata else None) or None
    except DependencyError as exc:
        raise NotAPdf(f"it is encrypted (AES), which needs the optional cryptography package ({exc}): save an "
                      "unencrypted copy (print to PDF) and drop that instead") from exc
    except FileNotDecryptedError as exc:
        raise NotAPdf("it is password-protected: save an unprotected copy and drop that instead") from exc
    except (PyPdfError, ValueError, KeyError, TypeError) as exc:
        raise NotAPdf(f"the PDF cannot be read: {exc}") from exc
    return PdfText(pages, title.strip() if isinstance(title, str) and title.strip() else None)


def _page(page) -> str:
    from pypdf.errors import DependencyError

    try:
        return page.extract_text() or ""
    except DependencyError:
        raise
    except Exception:  # noqa: BLE001 - pypdf raises many kinds on one odd page; the rest still count
        return ""


def ids_in(text: str) -> list[str]:
    """Source keys named in `text`, in order, once each."""
    out: list[str] = []
    for m in _IDS.finditer(text):
        key = source_key(_balanced(m[0]))
        if key and key not in out:
            out.append(key)
    return out


def _balanced(found: str) -> str:
    """An id in brackets without the closing bracket; a DOI's own brackets, as in S0304-405X(97)00004-X, stay."""
    while True:
        found = found.rstrip(".,;:")
        if found.endswith(")") and found.count(")") > found.count("("):
            found = found[:-1]
        elif found.endswith("]") and found.count("]") > found.count("["):
            found = found[:-1]
        else:
            return found


def title_candidates(pdf: PdfText, filename: str) -> list[str]:
    """What a title may be: the document's title field, the file's name, and runs of 1 to 3 of page 1's
    top lines (a long title wraps)."""
    lines = [ln.strip() for ln in (pdf.pages[0] if pdf.pages else "").splitlines() if ln.strip()][:TITLE_LINES]
    runs = [" ".join(lines[i:i + n]) for n in (1, 2, 3) for i in range(len(lines) - n + 1)]
    stem = re.sub(r"[_\-]+", " ", PurePath(filename).stem).strip()
    return list(dict.fromkeys(x for x in (pdf.title, stem, *runs) if x))
