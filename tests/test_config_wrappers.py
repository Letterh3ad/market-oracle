import pytest

from oracle.config import ConfigError, load_config


def test_wrappers_assume_nothing_about_the_user_and_lisa_is_off(tmp_path):
    w = load_config(None, cwd=tmp_path)[0].wrappers
    assert (w.lisa, w.salary, w.employee_pct, w.employer_pct, w.born, w.needed_before) == (False, 0, 0, 0, 0, -1)
    assert (w.on_salary, w.sacrifice, w.pension_fee, w.pension_charge) == (False, False, 0.3, 1.8)


def test_wrappers_take_the_users_numbers_and_refuse_bad_ones(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text("[wrappers]\nlisa = true\nsalary = 32000\nemployee_pct = 5\nemployer_pct = 3\nborn = 1995\n"
                    "needed_before = 150\nsacrifice = true\nretire_tax = 20\n", encoding="utf-8")
    w = load_config(path, cwd=tmp_path)[0].wrappers
    assert (w.lisa, w.salary, w.employee_pct, w.employer_pct, w.born, w.needed_before, w.sacrifice) == (
        True, 32000, 5, 3, 1995, 150, True)
    for line in ('lisa = "yes"', "salary = -1", "employee_pct = 101", "born = 1800", "born = 1995.5",
                 "needed_before = -2", "retire_tax = 46", "pension_charge = true", "other = 1"):
        path.write_text(f"[wrappers]\n{line}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="wrappers"):
            load_config(path, cwd=tmp_path)
