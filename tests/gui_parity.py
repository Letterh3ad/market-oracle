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
    # Ledgers tab, Stronger reader card: "Read history with the stronger reader" and "Count headlines and estimate".
    "reader run": "POST /api/jobs/reader",
    "reader run --list": "POST /api/jobs/reader",
    "reader plan": "GET /api/reader/plan",
    "reader plan --list": "GET /api/reader/plan",
    "reader run --all": "POST /api/jobs/reader",  # the card's "Own news only" box unticked
    "reader plan --all": "GET /api/reader/plan",
    "laya score": "POST /api/jobs/laya",
    "laya show": "GET /api/companies/{symbol}/laya",
    # Ledgers tab, Backtest, "Laya predictive test" card: its buttons and table.
    "laya history": "POST /api/jobs/laya-history",
    "laya history --list": "POST /api/jobs/laya-history",
    "laya history --allow-cpu": "POST /api/jobs/laya-history",
    "laya history --plan": "GET /api/laya/plan",
    "laya history --own-only": Exempt("a measuring run on a DB copy; the card scores the whole history"),
    "laya history --only": Exempt("a measuring run on a DB copy; the card scores the whole history"),
    "laya test": "POST /api/jobs/laya-test",
    "laya test --list": "POST /api/jobs/laya-test",
    "laya test --sensitivity": "POST /api/jobs/laya-test",
    # The test card's "Scores of" model choice and "Own news only" box.
    "laya test --model": "POST /api/jobs/laya-test",
    "laya test --own-only": "POST /api/jobs/laya-test",
    "laya test --show": "GET /api/laya/tests",
    # Company tab, Industry panel: "Find suppliers and customers" (this company or the main list),
    # the ties list and each unconfirmed tie's Confirm and Delete.
    "edges find": "POST /api/jobs/edges",
    "edges find --list": "POST /api/jobs/edges",
    "edges show": "GET /api/companies/{symbol}/edges",
    "edges confirm": "POST /api/companies/{symbol}/edges/{edge_id}/confirm",
    "edges delete": "DELETE /api/companies/{symbol}/edges/{edge_id}",
    # Company tab, Industry panel: "Research industry" (a symbol) and "Research the daily set" (none).
    "industry-news run": "POST /api/jobs/industry",
    "industry-news show": "GET /api/companies/{symbol}/industry-news",
    "industry-news show --days": "GET /api/companies/{symbol}/industry-news",
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
    # --account core-sat is "Core-sat step" (POST /api/ledgers/core-sat/step, a job); its log, sleeves and switch
    # are the Core-sat reading card (GET and PUT /api/ledgers/core-sat), switch also in Settings. --account laya
    # is "Laya step" (POST /api/ledgers/laya/step, a job); its log, verdict and switch are the Laya reading card
    # (GET and PUT /api/ledgers/laya), switch also in Settings. --account index is "Index step" (POST
    # /api/ledgers/index/step); its figures are the Index column. regime-ai, regime, regime-v2 and strategy are
    # frozen: their steps decide nothing (Strategy step says so), their cards have no switch, and Settings
    # marks their switches ignored.
    "paper step --account": "POST /api/ledgers/ai/step",
    "paper log": "GET /api/ledgers/decisions",
    "paper log --account": "GET /api/ledgers/decisions",
    "paper history": "GET /api/ledgers",
    "paper history --account": OUTPUT_ONLY,
    # Ledgers tab, Backtest section; its kept results come from GET /api/backtests.
    # Ledgers tab, History coverage card: "Backfill history" and its table.
    "history backfill": "POST /api/jobs/history",
    "history backfill --list": "POST /api/jobs/history",
    "history coverage": "GET /api/history/coverage",
    "history coverage --list": "GET /api/history/coverage",
    # The card's "Searched as" column shows each name; clicking a company fills the Search name form.
    "history name": "GET /api/history/coverage",
    **{f"history name --{flag}": "PUT /api/history/names/{symbol}" for flag in ("query", "alias", "skip")},
    "history name --clear": "DELETE /api/history/names/{symbol}",
    # Ledgers tab, Signal tests card: "Build testbed", "Refresh testbed" and its coverage table.
    "testbed build": "POST /api/jobs/testbed",
    "testbed build --refresh": "POST /api/jobs/testbed",
    **{f"testbed build --{flag}": Exempt("a smoke run on a scratch research DB; the card builds the whole testbed")
       for flag in ("only", "limit", "from", "to")},
    "testbed status": "GET /api/testbed/coverage",
    # The card's Renames list and its "Check renames" button.
    "testbed aliases": "GET /api/testbed/coverage",
    "testbed aliases --verify": "POST /api/jobs/testbed",
    # Same card: "Fetch SEC fundamentals", "Refetch" and the fundamentals coverage table.
    "testbed fundamentals": "POST /api/jobs/xbrl",
    "testbed fundamentals --refresh": "POST /api/jobs/xbrl",
    **{f"testbed fundamentals --{flag}": Exempt("a smoke run on a scratch research DB; the card fetches every member")
       for flag in ("only", "limit")},
    "testbed fundamentals-status": "GET /api/xbrl/coverage",
    # Same card: "Fetch Form 4s and 8-Ks", "Refetch" and their coverage table.
    "testbed insiders": "POST /api/jobs/insiders",
    "testbed insiders --refresh": "POST /api/jobs/insiders",
    **{f"testbed insiders --{flag}": Exempt("a smoke run on a scratch research DB; the card fetches every member")
       for flag in ("only", "limit")},
    "testbed insiders-status": "GET /api/insiders/coverage",
    # Same card: the typed-event study's "Run event study", "Train only" and its kept tables.
    "signals events": "POST /api/jobs/event-study",
    "signals events --train-only": "POST /api/jobs/event-study",
    "signals events --show": "GET /api/events",
    # Same card: the signals table, each row's "Run test" and its kept result.
    "signals list": "GET /api/signals",
    "signals test": "POST /api/jobs/signal-test",
    "signals test --train-only": "POST /api/jobs/signal-test",
    # Research tab, Scoreboard: the table, a row's Registration (Record commit, Drop), Register a test.
    "lab scoreboard": "GET /api/lab/scoreboard",
    "lab show": "GET /api/lab/tests/{key}",
    "lab register": "POST /api/lab/tests",
    "lab commit": "PUT /api/lab/tests/{key}/commit",
    "lab drop": "DELETE /api/lab/tests/{key}",
    # Plan tab. Every Plan action has a CLI twin too (test_every_plan_action_has_a_cli_twin).
    # Your plan card: the summary, and its fund picker and monthly amount.
    "plan show": "GET /api/plan/summary",
    **{key: "PUT /api/plan/choice" for key in ("plan set", "plan set --fund", "plan set --monthly")},
    # Fees and contributions card: the Platform form saves the platform keys together.
    **{f"plan set --{flag}": "PUT /api/plan/platform" for flag in (
        "platform", "percent-fee", "percent-cap", "flat-fee", "dealing-fee", "fx-fee")},
    **{key: "GET /api/plan/fees" for key in (
        "plan fees", *(f"plan fees --{flag}" for flag in ("monthly", "years", "growth", "start")))},
    **{key: "GET /api/plan/contributions" for key in (
        "plan contributions", *(f"plan contributions --{flag}" for flag in (
            "monthly", "lump", "years", "fund", "second", "weight", "band", "cash", "cash-rate")))},
    # Fund comparison card: "Compare funds" and its kept result.
    "plan funds": "POST /api/jobs/plan-funds",
    "plan funds --show": "GET /api/plan/funds",
    # Trend overlay test card: "Run trend test" and its kept result.
    "plan trend": "POST /api/jobs/plan-trend",
    "plan trend --show": "GET /api/plan/trend",
    # Guardrails card: the drawdown preview (pot input, never stored), the no-sell rule, the pending change.
    **{key: "GET /api/plan/drawdowns" for key in ("plan drawdowns", "plan drawdowns --pot")},
    **{key: "GET /api/plan/guardrails" for key in ("plan rule", "plan pending")},
    **{key: "PUT /api/plan/rule" for key in ("plan rule --set", "plan rule --clear")},
    "plan pending --cancel": "DELETE /api/plan/pending",
    # Ledgers tab, Data health card: "Run data checks", its counts and each check's flagged rows.
    "data check": "POST /api/jobs/data-check",
    "data check --check": OUTPUT_ONLY,
    "data check --limit": OUTPUT_ONLY,
    "data show": "GET /api/data/checks",
    "data show --check": OUTPUT_ONLY,
    "data show --limit": OUTPUT_ONLY,
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
