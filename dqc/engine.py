"""Config-driven data quality engine.

Each rule names a table, a rule type, a DQ dimension, a severity and a tolerance (maximum failure rate).
Every rule returns the row positions that fail, so the report can show the exact records behind each failure.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DIMENSIONS = ["Completeness", "Uniqueness", "Validity", "Consistency", "Integrity", "Timeliness", "Accuracy"]


@dataclass
class RuleResult:
    rule_id: str
    table: str
    rule_type: str
    dimension: str
    severity: str
    description: str
    tested: int
    failed: int
    tolerance: float
    failing_index: list
    detail: str = ""

    @property
    def fail_rate(self) -> float:
        return self.failed / self.tested if self.tested else 0.0

    @property
    def status(self) -> str:
        return "PASS" if self.fail_rate <= self.tolerance else "FAIL"


def load_tables(source: Path) -> dict[str, pd.DataFrame]:
    if source.suffix in (".db", ".sqlite"):
        con = sqlite3.connect(source)
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {n: pd.read_sql(f"SELECT * FROM [{n}]", con) for n in names}
    return {p.stem: pd.read_csv(p, dtype=str, keep_default_na=False, na_values=[""]) for p in sorted(source.glob("*.csv"))}


# ----------------------------------------------------------------------------- rule implementations
# Each returns (failing boolean Series aligned to the table, tested-row mask, detail text)

def r_not_null(df, rule, tables):
    cols = rule["columns"]
    bad = df[cols].isna().any(axis=1) | df[cols].astype(str).apply(lambda s: s.str.strip() == "").any(axis=1)
    return bad, None, f"columns: {', '.join(cols)}"


def r_unique(df, rule, tables):
    cols = rule["columns"]
    present = df[cols].notna().all(axis=1)
    return present & df.duplicated(cols, keep=False), present, f"key: {', '.join(cols)}"


def r_allowed_values(df, rule, tables):
    c = rule["column"]
    present = df[c].notna()
    return present & ~df[c].isin(rule["values"]), present, f"{c} in {rule['values']}"


def r_regex(df, rule, tables):
    c = rule["column"]
    present = df[c].notna()
    pat = re.compile(rule["pattern"])
    return present & ~df[c].astype(str).map(lambda v: bool(pat.fullmatch(v))), present, f"{c} matches {rule['pattern']}"


def r_range(df, rule, tables):
    c = rule["column"]
    v = pd.to_numeric(df[c], errors="coerce")
    present = df[c].notna()
    bad = present & (v.isna() | (v < rule.get("min", float("-inf"))) | (v > rule.get("max", float("inf"))))
    return bad, present, f"{rule.get('min', '-inf')} <= {c} <= {rule.get('max', 'inf')} (non-numeric fails)"


def r_date_valid(df, rule, tables):
    c = rule["column"]
    present = df[c].notna()
    parsed = pd.to_datetime(df[c], format=rule.get("format", "%Y-%m-%d"), errors="coerce")
    return present & parsed.isna(), present, f"{c} is a valid date ({rule.get('format', '%Y-%m-%d')})"


def r_compare(df, rule, tables):
    """Cross-field consistency: left <op> right, numeric or date."""
    a, b, op = rule["left"], rule["right"], rule["op"]
    conv = (lambda s: pd.to_datetime(s, errors="coerce")) if rule.get("as") == "date" else (lambda s: pd.to_numeric(s, errors="coerce"))
    x, y = conv(df[a]), conv(df[b])
    both = x.notna() & y.notna()
    ok = {"<=": x <= y, "<": x < y, ">=": x >= y, ">": x > y, "==": x == y}[op]
    return both & ~ok, both, f"{a} {op} {b}"


def r_conditional_not_null(df, rule, tables):
    """If <when_column> == <when_value> then <column> must be populated."""
    cond = df[rule["when_column"]] == rule["when_value"]
    return cond & df[rule["column"]].isna(), cond, f"{rule['column']} required when {rule['when_column']} = {rule['when_value']}"


def r_foreign_key(df, rule, tables):
    c = rule["column"]
    ref_t, ref_c = rule["references"].split(".")
    present = df[c].notna()
    return present & ~df[c].isin(set(tables[ref_t][ref_c].dropna())), present, f"{c} -> {rule['references']}"


def r_freshness(df, rule, tables):
    """Table-level: the latest date must be within max_age_days of as_of."""
    latest = pd.to_datetime(df[rule["column"]], errors="coerce").max()
    age = (pd.Timestamp(rule["as_of"]) - latest).days
    bad = pd.Series(False, index=df.index)
    if age > rule["max_age_days"]:
        bad[:] = True  # whole table is stale
    return bad, pd.Series(True, index=df.index), f"latest {rule['column']} = {latest:%Y-%m-%d}, {age} days before {rule['as_of']} (max {rule['max_age_days']})"


def r_reconcile(df, rule, tables):
    """Table-level: record count and column total must agree with the control totals supplied by the source system."""
    total = pd.to_numeric(df[rule["column"]], errors="coerce").sum()
    count_ok = len(df) == rule["expected_count"]
    sum_ok = abs(total - rule["expected_sum"]) <= rule.get("abs_tolerance", 0.01)
    bad = pd.Series(not (count_ok and sum_ok), index=df.index)
    return bad, pd.Series(True, index=df.index), (f"count {len(df):,} vs control {rule['expected_count']:,}; "
                                                  f"sum {total:,.2f} vs control {rule['expected_sum']:,.2f}")


def r_sql(df, rule, tables):
    """Custom rule: SQL that returns the _row of failing records (tables are loaded into SQLite with a _row column)."""
    con = sqlite3.connect(":memory:")
    for n, t in tables.items():
        t.assign(_row=range(len(t))).to_sql(n, con, index=False)
    rows = set(pd.read_sql(rule["sql"], con)["_row"])
    return pd.Series([i in rows for i in range(len(df))], index=df.index), None, rule["sql"].strip().replace("\n", " ")[:200]


RULES = {"not_null": r_not_null, "unique": r_unique, "allowed_values": r_allowed_values, "regex": r_regex, "range": r_range,
         "date_valid": r_date_valid, "compare": r_compare, "conditional_not_null": r_conditional_not_null,
         "foreign_key": r_foreign_key, "freshness": r_freshness, "reconcile": r_reconcile, "sql": r_sql}


TABLE_LEVEL = {"freshness", "reconcile"}


def run(tables: dict[str, pd.DataFrame], config: dict) -> list[RuleResult]:
    results = []
    for rule in config["rules"]:
        df = tables[rule["table"]].reset_index(drop=True)
        bad, tested_mask, detail = RULES[rule["type"]](df, rule, tables)
        tol = rule.get("tolerance", config.get("default_tolerance", {}).get(rule["severity"], 0.0))
        if rule["type"] in TABLE_LEVEL:  # one test on the whole table, not per record
            tested, failed, idx = 1, int(bad.any()), []
        else:
            tested = int(tested_mask.sum()) if tested_mask is not None else len(df)
            failed, idx = int(bad.sum()), list(df.index[bad])
        results.append(RuleResult(rule["id"], rule["table"], rule["type"], rule["dimension"], rule["severity"],
                                  rule.get("description", ""), tested, failed, tol, idx, detail))
    return results


def scorecards(results: list[RuleResult], tables: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rules = pd.DataFrame([{"rule_id": r.rule_id, "table": r.table, "dimension": r.dimension, "severity": r.severity,
                           "rule_type": r.rule_type, "description": r.description, "check": r.detail, "records_tested": r.tested,
                           "records_failed": r.failed, "fail_rate": round(r.fail_rate, 5), "tolerance": r.tolerance, "status": r.status}
                          for r in results])
    rules["pass_rate"] = 1 - rules.fail_rate
    dim = rules.groupby(["table", "dimension"]).agg(rules=("rule_id", "count"), failed_rules=("status", lambda s: (s == "FAIL").sum()),
                                                    score=("pass_rate", "mean")).reset_index()
    dim["score"] = dim.score.round(4)
    # a record is "clean" if no row-level rule flags it
    rows = []
    for t, df in tables.items():
        flagged = set()
        for r in results:
            if r.table == t and r.rule_type not in ("freshness", "reconcile"):
                flagged.update(r.failing_index)
        tr = rules[rules.table == t]
        rows.append({"table": t, "records": len(df), "records_with_any_issue": len(flagged),
                     "clean_record_rate": round(1 - len(flagged) / len(df), 4) if len(df) else 1.0,
                     "rules": len(tr), "rules_failed": int((tr.status == "FAIL").sum()),
                     "critical_failures": int(((tr.status == "FAIL") & (tr.severity == "Critical")).sum()),
                     "dq_score": round(float(tr.pass_rate.mean()), 4) if len(tr) else None})
    return rules, dim, pd.DataFrame(rows)


def failing_records(results: list[RuleResult], tables: dict[str, pd.DataFrame], limit: int = 200) -> pd.DataFrame:
    out = []
    for r in results:
        if r.status == "PASS" or r.rule_type in ("freshness", "reconcile"):
            continue
        df = tables[r.table].reset_index(drop=True)
        for i in r.failing_index[:limit]:
            rec = df.iloc[i]
            out.append({"rule_id": r.rule_id, "table": r.table, "severity": r.severity, "row_number": i + 2,  # CSV line incl. header
                        "record_key": str(rec.iloc[0]), "record": "; ".join(f"{k}={v}" for k, v in rec.items())[:300]})
    return pd.DataFrame(out, columns=["rule_id", "table", "severity", "row_number", "record_key", "record"])
