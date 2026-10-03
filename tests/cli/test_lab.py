from oracle.cli.main import main


def config(tmp_path):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\nresearch_db_path = "r.db"\n', encoding="utf-8")
    return str(cfg)


def test_lookup_prints_the_nearest_methods_with_score_and_status(tmp_path, capsys):
    assert main(["--config", config(tmp_path), "lab", "lookup", "does academic reserch destory stock return predictibility"]) == 0
    out = capsys.readouterr().out
    assert "Does Academic Research Destroy Stock Return Predictability?" in out
    assert "McLean, Pontiff 2016" in out and "replicated" in out
    lines = [ln for ln in out.splitlines() if ln.startswith("  T-") or ln.startswith("  A-") or ln.startswith("  X-")]
    assert len(lines) == 5 and all(" new " in ln or " fail " in ln or " built " in ln for ln in lines)


def test_lookup_by_id_and_by_ref(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", cfg, "lab", "lookup", "http://arxiv.org/pdf/2502.21206v2"]) == 0
    out = capsys.readouterr().out
    assert "arxiv:2502.21206" in out and "exact id match" in out and "A-24" in out
    assert main(["--config", cfg, "lab", "lookup", "t-15"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[lines.index("Nearest methods:") + 1].split()[:3] == ["T-15", "new", "1.00"]


def test_record_mark_and_show(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", cfg, "lab", "record", "https://doi.org/10.1234/x", "--title", "Yearly highs again",
                 "--authors", "Doe", "--year", "2024", "--quality", "peer-reviewed", "--read", "2026-10-02",
                 "--method", "T-15", "--new-method", "Yearly-high breakout", "--definition", "Buy within 2% of the high.",
                 "--alias", "52 week breakout", "--family", "momentum", "--by", "autopilot"]) == 0
    out = capsys.readouterr().out
    assert "New source" in out and "doi:10.1234/x" in out and "New method M-001" in out and "T-15" in out
    assert main(["--config", cfg, "lab", "mark", "M-001", "dig", "--note", "check OSAP", "--by", "user"]) == 0
    assert "M-001 triage" in capsys.readouterr().out
    assert main(["--config", cfg, "lab", "show", "m-001"]) == 0
    out = capsys.readouterr().out
    assert "Buy within 2% of the high." in out and "dig: new -> triage by user" in out and "check OSAP" in out
    assert "Yearly highs again" in out and "aliases: 52 week breakout" in out


def test_a_refused_mark_is_an_error(tmp_path, capsys):
    assert main(["--config", config(tmp_path), "lab", "mark", "X-01", "go"]) == 1
    assert "never re-tested" in capsys.readouterr().err


def test_search_log_records_and_lists(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", cfg, "lab", "search-log", "post-publication decay", "--where", "SSRN",
                 "--found", "https://ivey.uwo.ca/media/3775549/pontiff.pdf", "--note", "one hit"]) == 0
    assert "Logged" in capsys.readouterr().out
    assert main(["--config", cfg, "lab", "search-log"]) == 0
    out = capsys.readouterr().out
    assert "post-publication decay" in out and "SSRN" in out and "Does Academic Research" in out and "one hit" in out
    assert main(["--config", cfg, "lab", "search-log", "x"]) == 1
    assert "where" in capsys.readouterr().err


def test_methods_lists_by_status(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", cfg, "lab", "methods", "--status", "built"]) == 0
    out = capsys.readouterr().out
    assert "X-13" in out and "T-15" not in out and "1 method" in out
    assert main(["--config", cfg, "lab", "methods"]) == 0
    assert "205 methods" in capsys.readouterr().out


def test_a_blank_title_is_no_title(tmp_path, capsys):
    cfg = config(tmp_path)
    assert main(["--config", cfg, "lab", "record", "--title", "  "]) == 1
    assert "nothing to record" in capsys.readouterr().err
    assert main(["--config", cfg, "lab", "record", "https://ivey.uwo.ca/media/3775549/pontiff.pdf", "--title", " ",
                 "--note", "again"]) == 0
    assert "Does Academic Research Destroy Stock Return Predictability?" in capsys.readouterr().out


def test_show_prints_the_archive_note(tmp_path, capsys):
    assert main(["--config", config(tmp_path), "lab", "show", "T-10"]) == 0
    assert "archive: Tried-other (low-vol scorer inside the 51-name regime blend" in capsys.readouterr().out
