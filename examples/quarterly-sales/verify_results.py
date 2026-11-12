"""Check recorded exports against raw-source calculations; no model or product imports."""
import argparse
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from reference import load_sources, transform

ROOT = Path(__file__).resolve().parent
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
CASES = [
    ("learn", "target.xlsx", "result-learn.xlsx", 18, 66441),
    ("period-b", "expected.xlsx", "result-period_b.xlsx", 35, 131936),
    ("period-c", "expected.xlsx", "result-period_c.xlsx", 30, 61511),
]


def compare(actual, expected):
    assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True),
                       check_dtype=False, check_exact=False, atol=1e-6, rtol=0,
                       check_names=False)


def single_sheet(path):
    book = pd.read_excel(path, sheet_name=None)
    assert len(book) == 1, f"Expected one sheet: {path.name}"
    return next(iter(book.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict-sheet-names", action="store_true")
    args = parser.parse_args()
    name_mismatches = []
    for period, expected_name, recorded_name, rows, total in CASES:
        sheets = load_sources([ROOT / period / "source.xlsx"])
        computed = transform(sheets)["Sheet1"]
        reference_sheet, expected = single_sheet(ROOT / period / expected_name)
        actual_sheet, actual = single_sheet(ROOT / "recorded" / recorded_name)
        compare(expected, computed)
        compare(actual, computed)
        assert len(actual) == rows, f"{period}: output row count"
        assert actual["全年合计"].sum() == total == sheets["OrderDetail"]["OrderQty"].sum()
        assert (actual[QUARTERS].sum(axis=1) == actual["全年合计"]).all()
        assert (actual["销量排名"] == actual["全年合计"].rank(method="min", ascending=False)).all()
        assert ((actual["销量占比"] - (actual["全年合计"] / total).round(4)).abs() <= 1e-6).all()
        if period == "period-c":
            assert (actual[["Q3", "Q4"]] == 0).all().all()
        if actual_sheet != reference_sheet:
            name_mismatches.append(period)
        print(f"{period}: DATA PASS; rows={rows}; total={total}; "
              f"sheet_name_match={actual_sheet == reference_sheet}")
    print("Values, column order, row order and case invariants passed for 3 periods.")
    print("Known sheet-name differences: " + ", ".join(name_mismatches))
    print("This checks supplied artifacts, not fresh LLM generation or workbook formatting.")
    return 1 if args.strict_sheet_names and name_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
