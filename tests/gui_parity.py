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
    # Universe tab, "Backfill from indexes"; a refresh queues one after it too.
    "universe backfill": "POST /api/jobs/backfill",
    # Universe tab, Index backfill, "Find investment trusts" then "Remove".
    "universe funds": "GET /api/universe/funds",
    "universe funds --remove": "POST /api/universe/funds/remove",
    # Universe tab, Index backfill, "Show corrections" and each row's Undo.
    "universe corrections list": "GET /api/universe/corrections",
    "universe corrections undo": "DELETE /api/universe/corrections/{symbol}",
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
    "events": "GET /api/companies/{symbol}/events",  # Company tab, "After earnings"
    # Watchlist tab: the momentum and calm columns (sortable) and the "Factors as of" day.
    # The Company tab reads one listing's from GET /api/factors/{symbol}.
    "factors": "GET /api/factors",
    "factors --list": "GET /api/factors",
    "factors --as-of": "GET /api/factors",
    "research": "POST /api/jobs/research",
    "research --as-of": "POST /api/jobs/research",  # Company tab, "Replay as of"
    "research --history": "GET /api/companies/{symbol}/research/history",
    "research --model": "POST /api/jobs/research",  # Company tab, the model picker beside Research
    # Settings > Models; the section also adds, edits and deletes profiles.
    "models list": "GET /api/models",
    "models test": "POST /api/models/{name}/test",
    "discover": "POST /api/jobs/discover",  # Watchlist tab, Discovery section "Discover now"
    # Discover tab: Ask, a follow-up's Refine, Rerun, the saved questions, one question's answers,
    # and a shortlist row's Add to discovered (its Watch and Research reuse those routes).
    "discover ask": "POST /api/jobs/ask",
    "discover ask --refines": "POST /api/jobs/ask",
    "discover ask --model": "POST /api/jobs/ask",  # Discover tab, the model picker beside Ask
    "discover rerun --model": "POST /api/jobs/ask/{question_id}/rerun",
    "discover rerun": "POST /api/jobs/ask/{question_id}/rerun",
    "discover questions": "GET /api/questions",
    "discover show": "GET /api/questions/{question_id}",
    "discover add": "POST /api/questions/answers/{answer_id}/items/{n}/discovered",
    "discover auto": "PUT /api/questions/{question_id}/auto",  # Discover tab, a question's Auto switch
    # Company tab, "Industry": every link with its headlines, each row's Delete and the Add link form.
    "links show": "GET /api/companies/{symbol}/industry",
    "links add": "POST /api/companies/{symbol}/links",
    "links delete": "DELETE /api/companies/{symbol}/links/{link_id}",
    "industry": "GET /api/companies/{symbol}/industry",
    # Company tab, Laya section: "Score with Laya" and the scored headlines. On/off and device are
    # [laya] in Settings.
    "laya score": "POST /api/jobs/laya",
    "laya show": "GET /api/companies/{symbol}/laya",
    "paper buy": "POST /api/ledgers/manual/buy",
    "paper sell": "POST /api/ledgers/manual/sell",
    "paper sell --shares": "POST /api/ledgers/manual/sell",
    "paper sell --all": "POST /api/ledgers/manual/sell",
    "paper fill": "POST /api/ledgers/fill",
    "paper show": "GET /api/ledgers",
    "paper show --account": OUTPUT_ONLY,
    "paper step": "POST /api/ledgers/step",
    # --account regime is the Ledgers tab's "Regime step" (POST /api/ledgers/regime/step, a job on the tick
    # queue); its log and on/off switch are the Regime reading card (GET and PUT /api/ledgers/regime), the
    # switch also in Settings. --account regime-ai is "Regime-AI step" (POST /api/ledgers/regime-ai/step, a
    # job too); its log, tilt, switch and tilt model are the Regime-AI reading card (GET and PUT
    # /api/ledgers/regime-ai), switch and model also in Settings ([regime_ai_account]). --account regime-v2 is
    # "Regime-v2 step" (POST /api/ledgers/regime-v2/step, a job); its log, rotation rule and switch are the
    # Regime-v2 reading card (GET and PUT /api/ledgers/regime-v2), switch and rotation also in Settings.
    "paper step --account": "POST /api/ledgers/ai/step",
    "paper log": "GET /api/ledgers/decisions",
    "paper log --account": "GET /api/ledgers/decisions",
    "paper history": "GET /api/ledgers",
    "paper history --account": OUTPUT_ONLY,
    # Ledgers tab, Backtest section; its kept results come from GET /api/backtests.
    **{key: "POST /api/jobs/backtest" for key in (
        "backtest", "backtest --list", "backtest --from", "backtest --to", "backtest --slippage-bps",
        "backtest --commission", "backtest --regime-ai", "backtest --walk-forward")},
    # Ledgers tab, Replay study card.
    "replay create": "POST /api/replay",
    "replay create --from": "POST /api/replay",
    "replay create --symbols": "POST /api/replay",
    "replay create --bands": "POST /api/replay",  # the card's size mix picker
    "replay replace": "POST /api/replay/replace",  # each listing's Replace, before the first replay
    "replay start": "POST /api/replay/start",
    "replay pause": "POST /api/replay/pause",
    "replay status": "GET /api/replay",
    "replay status --json": "GET /api/replay",
    "replay retry": "POST /api/replay/retry",  # the card's "Retry failed" and each failed cell's Retry
    # The card's Score, read from stored bars; its "Update prices" (POST /api/replay/prices) is
    # the fetch the command does first.
    "replay report": "GET /api/replay/report",
    "replay report --json": "GET /api/replay/report",
    # Ledgers tab, Replay study card: the model picker and "Probe cutoff", and the readout under them.
    "replay probe": "POST /api/jobs/cutoff-probe",
    "replay probe --model": "POST /api/jobs/cutoff-probe",
    "replay cutoff": "GET /api/replay/cutoff",
    "replay cutoff --model": "GET /api/replay/cutoff",
    "tick": "POST /api/jobs/tick",
    "tick --last": "GET /api/status",
    "tick --fill": "POST /api/jobs/fill",  # Ticks panel, "Run fill pass now"
    "schedule": "GET /api/status",
    "schedule --on": "PUT /api/settings/schedule",
    "schedule --off": "PUT /api/settings/schedule",
    "schedule --time": "PUT /api/settings/schedule",
    "automation": "PUT /api/app/automation",
    "gui": Exempt("starts the GUI itself"),
    "gui --restart": "POST /api/app/restart",
    "gui --autostart": "PUT /api/app/autostart",
    "gui --install-shortcuts": "POST /api/app/shortcuts",
    # App section, Holiday watch card.
    "holiday arm": "POST /api/holiday/arm",
    "holiday disarm": "POST /api/holiday/disarm",
    "holiday status": "GET /api/holiday",
}
