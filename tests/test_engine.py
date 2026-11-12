import pandas as pd
import pytest

from dqc import engine as E
from dqc.main import main


def run_one(df, rule, extra=None):
    tables = {"t": df, **(extra or {})}
    rule = {"id": "r", "table": "t", "dimension": "Validity", "severity": "Critical", **rule}
    return E.run(tables, {"rules": [rule]})[0]


def test_not_null_counts_blank_strings():
    r = run_one(pd.DataFrame({"a": ["x", None, " ", "y"]}), {"type": "not_null", "columns": ["a"]})
    assert r.failed == 2 and r.status == "FAIL"


def test_unique_flags_all_duplicates():
    r = run_one(pd.DataFrame({"k": ["1", "2", "2", "3", "3", "3"]}), {"type": "unique", "columns": ["k"]})
    assert r.failed == 5


def test_regex_and_allowed_values_skip_nulls():
    df = pd.DataFrame({"e": ["a@b.com", "bad", None], "s": ["A", "Z", None]})
    assert run_one(df, {"type": "regex", "column": "e", "pattern": r"[^@]+@[^@]+\.\w+"}).failed == 1
    r = run_one(df, {"type": "allowed_values", "column": "s", "values": ["A", "B"]})
    assert r.failed == 1 and r.tested == 2


def test_range_rejects_non_numeric():
    r = run_one(pd.DataFrame({"x": ["5", "-1", "abc", "100"]}), {"type": "range", "column": "x", "min": 0, "max": 50})
    assert r.failed == 3


def test_compare_dates_and_conditional():
    df = pd.DataFrame({"o": ["2026-01-10", "2026-01-10", "2026-01-10"], "c": ["2026-01-09", "2026-02-01", None],
                       "st": ["CLOSED", "CLOSED", "CLOSED"]})
    assert run_one(df, {"type": "compare", "left": "o", "op": "<=", "right": "c", "as": "date"}).failed == 1
    assert run_one(df, {"type": "conditional_not_null", "column": "c", "when_column": "st", "when_value": "CLOSED"}).failed == 1


def test_foreign_key_and_sql():
    parent = pd.DataFrame({"id": ["P1", "P2"]})
    child = pd.DataFrame({"pid": ["P1", "P9", None], "v": ["1", "20", "3"]})
    assert run_one(child, {"type": "foreign_key", "column": "pid", "references": "p.id"}, {"p": parent}).failed == 1
    assert run_one(child, {"type": "sql", "sql": "SELECT _row FROM t WHERE CAST(v AS INT) > 10"}).failed == 1


def test_table_level_rules_count_once():
    df = pd.DataFrame({"d": ["2026-10-01", "2026-10-20"], "amt": ["10", "20"]})
    f = run_one(df, {"type": "freshness", "column": "d", "as_of": "2026-10-31", "max_age_days": 2})
    assert (f.tested, f.failed) == (1, 1)
    ok = run_one(df, {"type": "reconcile", "column": "amt", "expected_count": 2, "expected_sum": 30.0})
    assert ok.status == "PASS"


def test_tolerance_allows_small_failure_rates():
    df = pd.DataFrame({"a": ["x"] * 99 + [None]})
    assert run_one(df, {"type": "not_null", "columns": ["a"], "tolerance": 0.02}).status == "PASS"


def test_demo_end_to_end_matches_planted(tmp_path):
    code = main(["--out", str(tmp_path), "--fail-on", "Critical"])
    assert code == 1  # demo extract has critical failures
    chk = pd.read_csv(tmp_path / "planted_vs_flagged.csv")
    assert chk["match"].all()
