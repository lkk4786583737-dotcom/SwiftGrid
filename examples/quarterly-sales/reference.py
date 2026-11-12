"""Independent reference calculation for this public demo; never a model input."""
import argparse
from pathlib import Path

import pandas as pd

QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
UNCATEGORIZED = "未分类"


def transform(sheets):
    header = sheets["OrderHeader"].copy()
    detail = sheets["OrderDetail"].copy()
    product = sheets["Product"].copy()
    subcategory = sheets["Subcategory"].copy()

    header["OrderDate"] = pd.to_datetime(header["OrderDate"])
    product["ProductSubcategoryID"] = pd.to_numeric(product["ProductSubcategoryID"], errors="coerce")
    subcategory["ProductSubcategoryID"] = pd.to_numeric(
        subcategory["ProductSubcategoryID"], errors="coerce")

    merged = detail.merge(header[["SalesOrderID", "OrderDate"]], on="SalesOrderID", how="inner")
    merged = merged.merge(product[["ProductID", "ProductSubcategoryID"]],
                          on="ProductID", how="inner")
    merged = merged.merge(
        subcategory[["ProductSubcategoryID", "Name"]].rename(columns={"Name": "subcategory_name"}),
        on="ProductSubcategoryID", how="left",
    )
    merged["子类别"] = merged["subcategory_name"].fillna(UNCATEGORIZED)
    merged["quarter"] = "Q" + merged["OrderDate"].dt.quarter.astype(str)

    pivot = merged.pivot_table(index="子类别", columns="quarter", values="OrderQty",
                               aggfunc="sum", fill_value=0)
    for quarter in QUARTERS:
        if quarter not in pivot.columns:
            pivot[quarter] = 0
    out = pivot[QUARTERS].reset_index()
    for quarter in QUARTERS:
        out[quarter] = out[quarter].astype("int64")

    out["全年合计"] = out[QUARTERS].sum(axis=1).astype("int64")
    out["销量排名"] = out["全年合计"].rank(method="min", ascending=False).astype("int64")
    out["销量占比"] = (out["全年合计"] / out["全年合计"].sum()).round(4)
    out = out.sort_values(["销量排名", "子类别"], kind="mergesort").reset_index(drop=True)
    columns = ["子类别"] + QUARTERS + ["全年合计", "销量排名", "销量占比"]
    return {"Sheet1": out[columns]}


def render(out_path, frames):
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for name, frame in frames.items():
            frame.to_excel(writer, sheet_name=name, index=False)


def load_sources(paths):
    """Read every sheet of every source workbook, keyed by sheet name."""
    sheets = {}
    for path in paths:
        book = pd.read_excel(path, sheet_name=None)
        for name, frame in book.items():
            key = name if name not in sheets else f"{Path(path).stem}__{name}"
            sheets[key] = frame
    return sheets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="output workbook path")
    parser.add_argument("sources", nargs="+", help="one or more source workbooks")
    args = parser.parse_args()
    frames = transform(load_sources(args.sources))
    render(args.out, frames)
    for name, frame in frames.items():
        print(f"{name}: {len(frame)} rows")


if __name__ == "__main__":
    main()
