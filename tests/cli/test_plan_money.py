from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from tests.services.test_plan_money import store


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


def test_contributions_prints_the_distributions_mix_and_cash_drag(tmp_path, capsys):
    cfg = config(tmp_path)
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        store(c, "ACWI", [100.0 * 1.01 ** i for i in range(40)])
        store(c, "ISF.L", [100.0] * 40)
    assert main(["--config", str(cfg), "plan", "contributions", "--monthly", "100", "--years", "2"]) == 0
    out = capsys.readouterr().out
    assert "ACWI total return in GBP (proxy)" in out
    assert "Lump £1,200 vs 12-mo drip" in out and "    28 " in out
    assert "5-point bands" in out and "yearly" in out
    assert "Cash drag: 5% of the pot" in out


def test_contributions_without_prices_is_an_error_not_a_crash(tmp_path, capsys):
    assert main(["--config", str(config(tmp_path)), "plan", "contributions", "--monthly", "100"]) != 0
    assert "ACWI" in capsys.readouterr().err
