import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "oracle"
FRONTENDS = ("cli", "gui", "mcp")
"""The MCP server is a front end like the others: it calls services only."""
# An allowlist, so a package added later is off limits to frontends by default.
FRONTEND_MAY_IMPORT = ("oracle.services", "oracle.cli", "oracle.gui", "oracle.mcp")
SERVICES_MAY_NOT_IMPORT = ("oracle.gui", "oracle.cli", "oracle.mcp")
# Analysts are pure: data in, signals out. Loading and storing is the services' job.
ANALYSTS_MAY_IMPORT = ("oracle.analysts", "oracle.market.bars", "oracle.market.fundamentals")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _under(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def frontend_violations(path: Path) -> set[str]:
    return {
        n for n in _imports(path)
        if _under(n, ("oracle",)) and not _under(n, FRONTEND_MAY_IMPORT)
    }


def service_violations(path: Path) -> set[str]:
    return {n for n in _imports(path) if _under(n, SERVICES_MAY_NOT_IMPORT)}


def _modules(*packages: str) -> list[Path]:
    return sorted(p for pkg in packages for p in (SOURCE_ROOT / pkg).rglob("*.py"))


@pytest.mark.parametrize("path", _modules(*FRONTENDS), ids=lambda p: p.name)
def test_frontends_only_reach_services(path):
    assert frontend_violations(path) == set(), (
        "Frontends call oracle.services and nothing else, so the GUI and CLI stay "
        "thin and interchangeable."
    )


@pytest.mark.parametrize("path", _modules("services"), ids=lambda p: p.name)
def test_services_never_import_a_frontend(path):
    assert service_violations(path) == set()


def analyst_violations(path: Path) -> set[str]:
    return {n for n in _imports(path) if _under(n, ("oracle",)) and not _under(n, ANALYSTS_MAY_IMPORT)}


@pytest.mark.parametrize("path", _modules("analysts"), ids=lambda p: p.name)
def test_analysts_stay_pure(path):
    assert analyst_violations(path) == set(), "Analysts take data and return signals; services load and store."


def test_checker_catches_an_analyst_reaching_the_store(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from oracle.store.bars import BarRepo\nfrom oracle.market.bars import Bar\n")
    assert analyst_violations(bad) == {"oracle.store.bars"}


def test_checker_catches_a_frontend_reaching_the_store(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from oracle.store.db import connect\nimport oracle.services.context\n")
    assert frontend_violations(bad) == {"oracle.store.db"}


def test_checker_catches_the_mcp_server_reaching_the_store_and_a_service_importing_it(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from oracle.store.literature import LiteratureRepo\nfrom oracle.services import meaning_service\n")
    assert frontend_violations(bad) == {"oracle.store.literature"}
    bad.write_text("from oracle.mcp.server import Server\n")
    assert service_violations(bad) == {"oracle.mcp.server"}


def test_checker_catches_a_service_importing_the_gui(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import oracle.gui.app\n")
    assert service_violations(bad) == {"oracle.gui.app"}


def test_there_are_modules_to_check():
    assert _modules("cli") and _modules("mcp") and _modules("services") and _modules("analysts")


def test_checker_catches_a_frontend_reaching_config_directly(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from oracle.config import load_config\n")
    assert frontend_violations(bad) == {"oracle.config"}


# The app ships compiled, so a message a service raises or stores must name the action, not a command.
NEUTRAL_MESSAGE_PACKAGES = ("services", "broker", "research")


@pytest.mark.parametrize("package", NEUTRAL_MESSAGE_PACKAGES)
def test_no_message_below_the_frontends_names_a_cli_command(package):
    offenders = [
        f"{path.relative_to(SOURCE_ROOT)}:{n}"
        for path in sorted((SOURCE_ROOT / package).rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "`oracle " in line
    ]
    assert offenders == []


def test_service_errors_carry_one_message_for_every_frontend():
    from oracle.services.errors import ServiceError

    assert not hasattr(ServiceError, "in_gui")
