"""Analysis and full-data chart APIs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services.analysis import analysis_summary, chart_points
from ..services.session import get_data
from ..services.tableio import as_named_dict

router = APIRouter()


def _get_sheet(result_handle: str, sheet: str | None = None):
    obj = get_data(result_handle or "")
    if obj is None:
        raise HTTPException(status_code=404, detail="结果不存在或已过期")
    named = as_named_dict(obj, "结果表")
    if sheet and sheet in named:
        return named[sheet]
    return next(iter(named.values()))


@router.post("/api/charts/data")
async def charts_data(body: dict):
    df = _get_sheet(body.get("result_handle", ""), body.get("sheet"))
    try:
        return chart_points(
            df,
            chart_type=body.get("chart_type", "bar"),
            x=body.get("x", ""),
            y=body.get("y"),
            agg=body.get("agg", "sum"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/analysis/summary")
async def summary(body: dict):
    df = _get_sheet(body.get("result_handle", ""), body.get("sheet"))
    return analysis_summary(df)
