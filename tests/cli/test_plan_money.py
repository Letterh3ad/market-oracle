import asyncio

from oracle.cli.main import main
from tests.services.test_plan_money import seed


def config(tmp_path, plan: str = 'percent_fee = 0.25\nflat_fee = 4.99\n'):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "o.db"\nresearch_db_path = "r.db"\n[plan]\n{plan}', encoding="utf-8")
    return cfg


def test_fees_prints_every_platform_and_the_crossover(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "plan", "fees", "--monthly", "500", "--years", "20"]) == 0
    out = capsys.readouterr().out
    assert "No fees" in out and "Percentage 0.25% a year" in out and "Flat £4.99 a month" in out
    assert "Flat beats percentage once the pot is above £23,952" in out
    assert "5.0% a year (assumed)" in out


def test_fees_says_when_flat_never_wins(tmp_path, capsys):
    cfg = config(tmp_path, "percent_fee = 0.25\npercent_cap = 45\nflat_fee = 4.99\n")
    assert main(["--config", str(cfg), "plan", "fees", "--monthly", "100"]) == 0
    assert "Flat never beats percentage" in capsys.readouterr().out


def test_contributions_print_the_span_distributions_mix_and_cash_drag(tmp_path, capsys):
    asyncio.run(seed(tmp_path))
    assert main(["--config", str(config(tmp_path)), "plan", "contributions", "--monthly", "100", "--years", "2"]) == 0
    out = capsys.readouterr().out
    assert "All-world (proxy: ACWI" in out and "0.19% TER" in out
    assert "Whole months 2018-01-31 to 2020-12-31 (35 months" in out
    assert "Lump vs drip: 24 start months, 2018-01-31 to 2019-12-31." in out
    assert "Monthly: 12 windows of 2 years, starting 2018-01-31 to 2018-12-31." in out
    assert "Lump £1,200 vs 12-mo drip" in out
    assert "5-point bands" in out and "UK shares" in out
    assert "Cash drag: 5% of the pot" in out


def test_contributions_without_history_say_which_job_to_run(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "plan", "contributions", "--monthly", "100"]) != 0
    assert "run the fund comparison first" in capsys.readouterr().err


def test_a_zero_lump_is_refused_cleanly(tmp_path, capsys):
    asyncio.run(seed(tmp_path))
    assert main(["--config", str(config(tmp_path)), "plan", "contributions", "--monthly", "100", "--lump", "0"]) != 0
    assert "lump must be above 0" in capsys.readouterr().err
