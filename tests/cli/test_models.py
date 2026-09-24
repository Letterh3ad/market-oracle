from functools import partial

import httpx
import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.fakes.ollama import FakeOllama


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('[models.deep]\nprovider = "claude_api"\nmodel = "claude-opus-5"\nkey = "sk-secret"\n'
                   '[models.own]\nprovider = "claude_api"\nmodel = "claude-opus-5"\n'
                   '[models.gone]\nprovider = "claude_api"\nmodel = "m"\nkey_env = "NOT_SET_ANYWHERE"\n',
                   encoding="utf-8")
    ollama = FakeOllama({"ok": True})
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=httpx.MockTransport(ollama.handle)))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    return _run


def test_list_shows_each_profile_with_its_key_as_set_or_unset_only(run):
    code, out, _ = run("models", "list")
    assert code == 0 and "sk-secret" not in out
    lines = out.splitlines()
    assert lines[0].split() == ["name", "provider", "model", "context", "key"]
    assert lines[1].split()[:7] == ["default", "ollama", "qwen3:4b-instruct-2507-q8_0", "16384", "-", "from", "[llm]"]
    assert lines[1].endswith("slots: research (unset), notes (unset), roles (unset), queries (unset), verdict (unset), "
                             "discovery (unset)")
    assert lines[2].split() == ["deep", "claude_api", "claude-opus-5", "200000", "set"]
    assert lines[3].split() == ["own", "claude_api", "claude-opus-5", "200000", "env", "/", "ant", "login"]
    assert lines[4].split() == ["gone", "claude_api", "m", "200000", "unset", "(NOT_SET_ANYWHERE)"]


def test_list_warns_when_a_slots_profile_has_its_key_unset(run, tmp_path):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + '[models.slots]\nverdict = "gone"\n', encoding="utf-8")
    code, out, _ = run("models", "list")
    lines = out.splitlines()
    assert code == 0 and lines[-2].endswith("slots: verdict")
    assert "warning: NOT_SET_ANYWHERE is not set, so gone cannot run" in lines[-1]
    assert "verdict (unset)" not in lines[1]


def test_test_reports_ok_with_the_reply_time_or_the_error(run):
    code, out, _ = run("models", "test", "default")
    assert code == 0 and out.startswith("default: ok in ") and out.rstrip().endswith("s")
    code, out, _ = run("models", "test", "gone")
    assert code == 1 and "gone: failed after" in out and "NOT_SET_ANYWHERE is not set" in out
    code, _, err = run("models", "test", "nope")
    assert code == 1 and "no model profile nope" in err
