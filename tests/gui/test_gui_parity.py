import argparse
import re
from pathlib import Path

from oracle.cli.main import build_parser
from oracle.gui.app import STATIC_DIR, build_app
from tests.gui_parity import MANIFEST, SETTINGS_FORM, Exempt, Pending


def cli_actions(parser: argparse.ArgumentParser, prefix: str = "") -> set[str]:
    """Runnable commands and their optional flags; positionals and --help need no entry."""
    found: set[str] = set()
    runnable = True
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            runnable = not action.required
            for name, sub in action.choices.items():
                found |= cli_actions(sub, f"{prefix}{name} ")
        elif action.option_strings and not isinstance(action, argparse._HelpAction) and prefix:
            found.add(f"{prefix}{_long(action.option_strings)}")
    if runnable and prefix:
        found.add(prefix.strip())
    return found


def _long(options: list[str]) -> str:
    return next((o for o in options if o.startswith("--")), options[0])


def unmapped(actions: set[str], manifest: dict) -> set[str]:
    return actions - manifest.keys()


def stale(actions: set[str], manifest: dict) -> set[str]:
    return manifest.keys() - actions


def routes(manifest: dict) -> set[str]:
    return {v for v in manifest.values() if isinstance(v, str)}


def missing_routes(manifest: dict, app_routes: set[str]) -> set[str]:
    return routes(manifest) - app_routes


def app_routes(app) -> set[str]:
    # The schema, since FastAPI builds included routers lazily and app.routes hides them.
    return {f"{m.upper()} {path}" for path, ops in app.openapi()["paths"].items() for m in ops}


def uncalled(route: str, page_source: str) -> bool:
    """True when no page script builds the route's path (parameters may be any expression)."""
    path = route.split(" ", 1)[1]
    pattern = "".join(".+?" if part.startswith("{") else re.escape(part)
                      for part in re.split(r"(\{[^}]+\})", path))
    return re.search(pattern + r"(?![\w/-])", page_source) is None


def _page_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in Path(STATIC_DIR).rglob("*.js")
                     if "vendor" not in p.parts)


def _toy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status")
    box = sub.add_parser("box").add_subparsers(dest="box_command", required=True)
    box.add_parser("open").add_argument("--wide", action="store_true")
    return parser


def test_the_walk_finds_runnable_commands_and_their_flags_only():
    assert cli_actions(_toy_parser()) == {"status", "box open", "box open --wide"}


def test_an_unmapped_command_or_flag_is_caught():
    manifest = {"status": "GET /api/status"}
    assert unmapped(cli_actions(_toy_parser()), manifest) == {"box open", "box open --wide"}


def test_a_route_the_app_lacks_is_caught():
    manifest = {"status": "GET /api/status", "box open": "POST /api/box", "box open --wide": Exempt("x")}
    assert missing_routes(manifest, {"GET /api/status"}) == {"POST /api/box"}


def test_a_route_the_page_never_builds_is_caught():
    page = 'api(`/api/companies/${encodeURIComponent(s)}/prices?days=1`); api("/api/ledgers/fill")'
    assert not uncalled("GET /api/companies/{symbol}/prices", page)
    assert uncalled("GET /api/ledgers", page)


def test_every_cli_action_has_a_gui_control_or_a_stated_exemption():
    assert unmapped(cli_actions(build_parser()), MANIFEST) == set(), (
        "Map each new CLI command or flag to its GUI route in tests/gui_parity.py, "
        "or exempt it with a reason."
    )


def test_the_manifest_names_only_cli_actions_that_exist():
    assert stale(cli_actions(build_parser()), MANIFEST) == set()


def test_every_mapped_route_exists_in_the_app(ctx):
    assert missing_routes(MANIFEST, app_routes(build_app(ctx))) == set()


def test_every_mapped_route_is_called_from_the_page():
    source = _page_source()
    assert {r for r in routes(MANIFEST) if uncalled(r, source)} == set()


def test_every_exemption_and_pending_entry_says_why():
    assert all(v.reason if isinstance(v, Exempt) else v.ticket
               for v in MANIFEST.values() if isinstance(v, (Exempt, Pending)))


def test_the_settings_form_over_every_oracle_toml_key_is_served_and_called(ctx):
    assert set(SETTINGS_FORM) <= app_routes(build_app(ctx))
    assert [r for r in SETTINGS_FORM if uncalled(r, _page_source())] == []
