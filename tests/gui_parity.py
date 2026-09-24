"""Every CLI action and the GUI route that does the same, so nothing needs the command line
(the app will be compiled). Keys: "command", "command sub" or "command sub --flag"; a
command that needs a subcommand is not an action itself. `tests/gui/test_gui_parity.py`
fails on a CLI action missing here, a stale key, or a route the app lacks or the page never
calls."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exempt:
    reason: str


@dataclass(frozen=True)
class Pending:
    ticket: str


OUTPUT_ONLY = Exempt("formats the CLI's output; the page shows it all")

MANIFEST: dict[str, str | Exempt | Pending] = {
    "status": "GET /api/status",
    "net status": "GET /api/status",
    "jobs status": Pending("untracked: job lock holders are not shown"),
    "universe refresh": Pending("gui-parity 03"),
    "universe status": Pending("gui-parity 03"),
    "universe search": "GET /api/universe/search",
    **{f"universe search --{flag}": "GET /api/universe/search" for flag in (
        "type", "country", "sector", "industry", "market-cap", "category-group", "category", "family",
        "exchange", "tag", "sort", "desc", "offset", "all-listings", "include-delisted")},
    "universe search --limit": OUTPUT_ONLY,
    "universe facets": "GET /api/universe/facets/{field}",
    "tag add": "POST /api/companies/{symbol}/tags",
    "tag remove": "DELETE /api/companies/{symbol}/tags/{tag}",
    "tag list": Pending("untracked: no list of every tag"),
    "watch add": "POST /api/watchlists/{name}/items",
    "watch add --list": Pending("gui-parity 06"),
    "watch remove": "DELETE /api/watchlists/{name}/items/{symbol}",
    "watch remove --list": "DELETE /api/watchlists/{name}/items/{symbol}",
    "watch show": "GET /api/watchlists/{name}",
    "watch lists": "GET /api/watchlists",
    "prices update": "POST /api/jobs/prices",
    "prices show": "GET /api/companies/{symbol}/prices",
    "prices show --days": "GET /api/companies/{symbol}/prices",
    "prices show --base": OUTPUT_ONLY,
    "prices sources": Pending("untracked: price source order is not shown"),
    "fx update": Pending("gui-parity 04"),
    "fx update --source": Pending("gui-parity 04"),
    "fx show": Pending("gui-parity 04"),
    "fx show --days": Pending("gui-parity 04"),
    "company": "GET /api/companies/{symbol}",
    "analyse": "POST /api/jobs/analyse",
    "peers": "GET /api/companies/{symbol}/peers",
    "research": "POST /api/jobs/research",
    "research --as-of": Pending("gui-parity 05"),
    "research --history": Pending("gui-parity 05"),
    "paper buy": "POST /api/ledgers/manual/buy",
    "paper sell": "POST /api/ledgers/manual/sell",
    "paper sell --shares": "POST /api/ledgers/manual/sell",
    "paper sell --all": "POST /api/ledgers/manual/sell",
    "paper fill": "POST /api/ledgers/fill",
    "paper show": "GET /api/ledgers",
    "paper show --account": OUTPUT_ONLY,
    "paper step": "POST /api/ledgers/step",
    "paper step --account": "POST /api/ledgers/ai/step",
    "paper log": "GET /api/ledgers/decisions",
    "paper log --account": "GET /api/ledgers/decisions",
    "paper history": "GET /api/ledgers",
    "paper history --account": OUTPUT_ONLY,
    "tick": "POST /api/jobs/tick",
    "tick --last": "GET /api/status",
    "schedule": "GET /api/status",
    "schedule --on": "PUT /api/settings/schedule",
    "schedule --off": "PUT /api/settings/schedule",
    "schedule --time": "PUT /api/settings/schedule",
    "automation": "PUT /api/app/automation",
    "gui": Exempt("starts the GUI itself"),
    "gui --restart": "POST /api/app/restart",
    "gui --autostart": "PUT /api/app/autostart",
    "gui --install-shortcuts": "POST /api/app/shortcuts",
}
