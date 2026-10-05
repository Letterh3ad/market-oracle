from functools import partial

from oracle.cli import main as cli_main
from oracle.cli.main import main
from oracle.services.context import open_context
from tests.fakes.pdf import paper
from tests.lab.test_papers_service import GKX, GKX_PDF, JT, JT_TITLE, web


def config(tmp_path, email=True):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\nresearch_db_path = "r.db"\n'
                   + ('[research]\nopen_access_email = "lab-tests@example.org"\n' if email else ""), encoding="utf-8")
    return str(cfg)


def test_fetch_keeps_an_open_copy_and_a_closed_one_goes_on_the_wanted_list(tmp_path, capsys, monkeypatch):
    fake = web({GKX_PDF: paper("Empirical Asset Pricing via Machine Learning", GKX)})
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=fake.transport))
    cfg = config(tmp_path)
    assert main(["--config", cfg, "lab", "fetch", f"doi:{GKX}"]) == 0
    out = capsys.readouterr().out
    assert "Empirical Asset Pricing via Machine Learning" in out and "2 pages" in out and "from unpaywall" in out
    assert main(["--config", cfg, "lab", "record", JT, "--title", JT_TITLE]) == 0
    assert main(["--config", cfg, "lab", "fetch", JT]) == 0
    out = capsys.readouterr().out
    assert "added to papers I'd like" in out and "Unpaywall: closed" in out
    assert main(["--config", cfg, "lab", "wanted"]) == 0
    out = capsys.readouterr().out
    assert JT_TITLE in out and "1 paper wanted" in out


def test_want_unwant_and_the_inbox(tmp_path, capsys):
    cfg = config(tmp_path, email=False)
    assert main(["--config", cfg, "lab", "record", JT, "--title", JT_TITLE]) == 0
    assert main(["--config", cfg, "lab", "want", JT, "--note", "via the library"]) == 0
    assert main(["--config", cfg, "lab", "wanted"]) == 0
    assert "note: via the library" in capsys.readouterr().out
    inbox = tmp_path / ".scratch" / "research-lab" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "jt.pdf").write_bytes(paper("Returns to Buying Winners and Selling Losers", JT))
    assert main(["--config", cfg, "lab", "inbox"]) == 0
    out = capsys.readouterr().out
    assert "jt.pdf: #" in out and JT_TITLE in out and f"by id doi:{JT}" in out
    assert main(["--config", cfg, "lab", "wanted"]) == 0
    assert "0 papers wanted" in capsys.readouterr().out
    assert main(["--config", cfg, "lab", "want", JT]) != 0
    assert "already has its full text" in capsys.readouterr().err
    assert main(["--config", cfg, "lab", "unwant", JT]) != 0
    assert "not on the list" in capsys.readouterr().err


def test_text_lists_the_full_texts_and_prints_one(tmp_path, capsys):
    cfg = config(tmp_path, email=False)
    assert main(["--config", cfg, "lab", "record", JT, "--title", JT_TITLE]) == 0
    assert main(["--config", cfg, "lab", "record", GKX, "--title", "Empirical Asset Pricing via Machine Learning"]) == 0
    inbox = tmp_path / ".scratch" / "research-lab" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "jt.pdf").write_bytes(paper(JT_TITLE, JT))
    assert main(["--config", cfg, "lab", "inbox"]) == 0
    capsys.readouterr()
    assert main(["--config", cfg, "lab", "text"]) == 0
    out = capsys.readouterr().out
    assert JT_TITLE in out and "from inbox" in out and "2 pages" in out and "1 full text" in out
    assert "Empirical Asset Pricing" not in out
    assert main(["--config", cfg, "lab", "text", JT]) == 0
    out = capsys.readouterr().out
    assert JT_TITLE in out and "from inbox" in out and "Line 59 of the body text" in out
    assert main(["--config", cfg, "lab", "lookup", JT]) == 0
    assert "full text: from inbox" in capsys.readouterr().out
    assert main(["--config", cfg, "lab", "text", GKX]) != 0
    assert "has no full text" in capsys.readouterr().err
