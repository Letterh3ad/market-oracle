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
    assert lines[1].split() == ["default", "ollama", "qwen3:4b-instruct-2507-q8_0", "16384", "-", "from", "[llm]"]
    assert lines[2].split() == ["deep", "claude_api", "claude-opus-5", "200000", "set"]
    assert lines[3].split() == ["own", "claude_api", "claude-opus-5", "200000", "env", "/", "ant", "login"]
    assert lines[4].split() == ["gone", "claude_api", "m", "200000", "unset", "(NOT_SET_ANYWHERE)"]


def test_list_marks_a_claude_code_profile_off_while_the_flag_is_unset(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('[models.sub]\nprovider = "claude_code"\nmodel = "sonnet"\n', encoding="utf-8")
    assert cli_main.main(["--config", str(cfg), "models", "list"]) == 0
    assert capsys.readouterr().out.splitlines()[2].split() == [
        "sub", "claude_code", "sonnet", "200000", "-", "off", "(allow_claude_code", "is", "not", "set)"]


def test_test_reports_ok_with_the_reply_time_or_the_error(run):
    code, out, _ = run("models", "test", "default")
    assert code == 0 and out.startswith("default: ok in ") and out.rstrip().endswith("s")
    code, out, _ = run("models", "test", "gone")
    assert code == 1 and "gone: failed after" in out and "NOT_SET_ANYWHERE is not set" in out
    code, _, err = run("models", "test", "nope")
    assert code == 1 and "no model profile nope" in err
