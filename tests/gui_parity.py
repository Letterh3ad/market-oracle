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


# oracle.toml has no CLI command: every key (oracle.config.setting_list, walked from Config)
# is edited through these. tests/services/test_settings_form.py pins that the form lists them all.
SETTINGS_FORM = ("GET /api/settings", "PUT /api/settings")

OUTPUT_ONLY = Exempt("formats the CLI's output; the page shows it all")

MANIFEST: dict[str, str | Exempt | Pending] = {
    "status": "GET /api/status",
    "net status": "GET /api/status",
    # Activity lists this server's jobs and, under "elsewhere", live locks other processes
    # hold. Stale locks are left out: they block nothing, the next run takes them over.
    "jobs status": "GET /api/activity",
    "universe refresh": "POST /api/jobs/universe",
    "universe status": "GET /api/universe/status",
    "universe search": "GET /api/universe/search",
    **{f"universe search --{flag}": "GET /api/universe/search" for flag in (
        "type", "country", "sector", "industry", "market-cap", "category-group", "category", "family",
        "exchange", "tag", "sort", "desc", "offset", "all-listings", "include-delisted")},
    "universe search --limit": OUTPUT_ONLY,
    "universe merges": "POST /api/jobs/merges",  # Universe tab, "Proposed merges"
    "universe facets": "GET /api/universe/facets/{field}",
    "tag add": "POST /api/companies/{symbol}/tags",
    "tag remove": "DELETE /api/companies/{symbol}/tags/{tag}",
    # Every tag: the Universe tab's "Your tags" row. One company's: its search result row.
    "tag list": "GET /api/universe/tags",
    "watch add": "POST /api/watchlists/{name}/items",
    "watch add --list": "POST /api/watchlists",  # Watchlist tab "Create"; Watch targets any list
    "watch remove": "DELETE /api/watchlists/{name}/items/{symbol}",
    "watch remove --list": "DELETE /api/watchlists/{name}/items/{symbol}",
    "watch swap": "POST /api/watchlists/swap",  # Watchlist row and Company tab, a dead listing's Swap
    "watch show": "GET /api/watchlists/{name}",
    "watch lists": "GET /api/watchlists",
    "prices update": "POST /api/jobs/prices",
    "prices show": "GET /api/companies/{symbol}/prices",
    "prices show --days": "GET /api/companies/{symbol}/prices",
    "prices show --base": OUTPUT_ONLY,
    "prices sources": "GET /api/status",  # Data sources panel, "Prices are tried in this order"
    "fx update": "POST /api/jobs/fx",
    "fx update --source": "POST /api/jobs/fx",
    "fx show": "GET /api/fx/{currency}",
    "fx show --days": "GET /api/fx/{currency}",
    "company": "GET /api/companies/{symbol}",
    "analyse": "POST /api/jobs/analyse",
    "peers": "GET /api/companies/{symbol}/peers",
    "research": "POST /api/jobs/research",
    "research --as-of": "POST /api/jobs/research",  # Company tab, "Replay as of"
    "research --history": "GET /api/companies/{symbol}/research/history",
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
