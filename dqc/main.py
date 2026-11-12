"""Run a data-quality rule set against a folder of CSVs (or a SQLite file) and write the DQ report.

    python -m dqc.main                                              # demo extract with planted issues
    python -m dqc.main --data path/to/csvs --rules my_rules.json     # your own data
    python -m dqc.main --fail-on Critical                           # exit code 1 if any Critical rule fails (for pipelines)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import engine as E
from .demo import build as build_demo

ROOT = Path(__file__).resolve().parent.parent


def attach_control_totals(cfg: dict, data: Path) -> dict:
    ct_file = data / cfg.get("control_totals_file", "control_totals.json")
    if not ct_file.exists():
        cfg["rules"] = [r for r in cfg["rules"] if r["type"] != "reconcile"]
        return cfg
    ct = json.loads(ct_file.read_text())
    for r in cfg["rules"]:
        if r["type"] == "reconcile":
            r["expected_count"] = ct[r["control_key"]]["record_count"]
            r["expected_sum"] = ct[r["control_key"]]["amount_total"]
    return cfg


def write_excel(path: Path, rules: pd.DataFrame, dims: pd.DataFrame, tables: pd.DataFrame, fails: pd.DataFrame, meta: dict) -> None:
    from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    heat = dims.pivot(index="table", columns="dimension", values="score").reindex(columns=[d for d in E.DIMENSIONS if d in set(dims.dimension)])
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        summary = pd.DataFrame([("Dataset", meta["dataset"]), ("Tables", len(tables)), ("Records", int(tables.records.sum())),
                                ("Rules run", len(rules)), ("Rules failed", int((rules.status == "FAIL").sum())),
                                ("Critical rules failed", int(((rules.status == "FAIL") & (rules.severity == "Critical")).sum())),
                                ("Overall DQ score (mean rule pass rate)", round(float(rules.pass_rate.mean()), 4)),
                                ("Verdict", meta["verdict"])], columns=["Measure", "Value"])
        sheets = [("Summary", summary, False), ("Table Scorecard", tables, False), ("Dimension Heatmap", heat, True),
                  ("Rule Results", rules.drop(columns="pass_rate"), False), ("Failing Records", fails, False)]
        for name, df, idx in sheets:
            df.to_excel(xw, sheet_name=name, index=idx)
            ws = xw.sheets[name]
            for c in ws[1]:
                c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3864")
            for j in range(1, ws.max_column + 1):
                ws.column_dimensions[get_column_letter(j)].width = 16 if name != "Failing Records" or j < 6 else 110
            ws.freeze_panes = "B2" if idx else "A2"
        rr = xw.sheets["Rule Results"]
        scol = get_column_letter(list(rules.drop(columns="pass_rate").columns).index("status") + 1)
        rr.conditional_formatting.add(f"{scol}2:{scol}{len(rules) + 1}", CellIsRule(operator="equal", formula=['"FAIL"'], fill=PatternFill("solid", fgColor="FFC7CE")))
        rr.conditional_formatting.add(f"{scol}2:{scol}{len(rules) + 1}", CellIsRule(operator="equal", formula=['"PASS"'], fill=PatternFill("solid", fgColor="C6EFCE")))
        rr.column_dimensions["F"].width, rr.column_dimensions["G"].width = 45, 60
        hm = xw.sheets["Dimension Heatmap"]
        rng = f"B2:{get_column_letter(heat.shape[1] + 1)}{heat.shape[0] + 1}"
        hm.conditional_formatting.add(rng, ColorScaleRule(start_type="num", start_value=0.95, start_color="FFC7CE",
                                                          mid_type="num", mid_value=0.99, mid_color="FFEB9C",
                                                          end_type="num", end_value=1, end_color="C6EFCE"))
        for row in hm.iter_rows(min_row=2, min_col=2):
            for c in row:
                c.number_format = "0.00%"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, help="folder of CSVs or a .db file; omit to build the demo extract")
    ap.add_argument("--rules", type=Path, default=ROOT / "config" / "card_servicing_rules.json")
    ap.add_argument("--out", type=Path, default=ROOT / "output")
    ap.add_argument("--fail-on", choices=["Critical", "Major", "Minor", "never"], default="never")
    a = ap.parse_args(argv)

    if a.data is None:
        a.data = ROOT / "demo_data"
        build_demo(a.data)
    cfg = attach_control_totals(json.loads(a.rules.read_text()), a.data)
    tables = E.load_tables(a.data)
    results = E.run(tables, cfg)
    rules, dims, tscore = E.scorecards(results, tables)
    fails = E.failing_records(results, tables)

    order = ["Critical", "Major", "Minor"]
    failed = rules[rules.status == "FAIL"]
    worst = next((s for s in order if s in set(failed.severity)), None)
    verdict = "FIT FOR USE" if worst is None else ("NOT FIT FOR USE - critical failures" if worst == "Critical" else f"USE WITH CAUTION - {worst} failures")

    a.out.mkdir(parents=True, exist_ok=True)
    rules.to_csv(a.out / "rule_results.csv", index=False)
    fails.to_csv(a.out / "failing_records.csv", index=False)
    tscore.to_csv(a.out / "table_scorecard.csv", index=False)
    write_excel(a.out / "dq_report.xlsx", rules, dims, tscore, fails, {"dataset": cfg.get("dataset", ""), "verdict": verdict})

    planted_file = a.data / "planted_issues.json"
    if planted_file.exists():
        planted = json.loads(planted_file.read_text())
        check = pd.DataFrame([{"rule_id": k, "planted": v, "flagged": int(rules.set_index("rule_id").at[k, "records_failed"])} for k, v in planted.items()])
        check["match"] = check.planted == check.flagged
        check.to_csv(a.out / "planted_vs_flagged.csv", index=False)
        print(check.to_string(index=False))
        unplanted = rules[(rules.status == "FAIL") & ~rules.rule_id.isin(planted)]
        print(f"Planted issues matched exactly: {int(check.match.sum())}/{len(check)} | unplanted failures: {len(unplanted)}")

    print(tscore.to_string(index=False))
    print(rules[["rule_id", "severity", "records_tested", "records_failed", "fail_rate", "status"]].to_string(index=False))
    print(f"\nVERDICT: {verdict}")
    if a.fail_on != "never" and worst is not None and order.index(worst) <= order.index(a.fail_on):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
