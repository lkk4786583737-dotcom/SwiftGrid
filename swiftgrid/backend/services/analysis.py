"""Lightweight table analysis and chart aggregation.

This module is intentionally provider-shaped: the builtin heuristics give the
product a useful 60% analysis layer today, and a future knowledge-pack provider
can replace or extend the suggestion methods without changing API contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .tableio import column_roles


MAX_CHART_POINTS = 80


@dataclass(frozen=True)
class ChartSpec:
    title: str
    chart_type: str
    x: str
    y: str | None
    agg: str
    reason: str
    source: str = "builtin"


def _safe(v: Any):
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "isoformat"):
        return str(v)
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _to_number_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _role_for_column(col: str, time_cols: list[str], cat_cols: list[str], num_cols: list[str]) -> str:
    if col in time_cols:
        return "time"
    if col in num_cols:
        return "numeric"
    if col in cat_cols:
        return "category"
    return "text"


def profile_table(df: pd.DataFrame) -> dict:
    time_cols, cat_cols, num_cols = column_roles(df)
    columns = []
    total = int(len(df))
    for c in df.columns:
        s = df[c]
        non_null = s.dropna()
        examples = [_safe(v) for v in non_null.head(3).tolist()]
        columns.append({
            "name": str(c),
            "role": _role_for_column(str(c), time_cols, cat_cols, num_cols),
            "null_rate": round(float(s.isna().mean()), 4) if total else 0,
            "unique_count": int(s.nunique(dropna=True)),
            "examples": examples,
        })
    return {
        "total_rows": total,
        "total_columns": int(len(df.columns)),
        "columns": columns,
        "time_columns": time_cols,
        "category_columns": cat_cols,
        "numeric_columns": num_cols,
    }


class BuiltinAnalysisProvider:
    """Default heuristic provider.

    Future integration point: add a KnowledgePackProvider with the same public
    methods and choose it from a small registry.
    """

    def suggest_charts(self, profile: dict) -> list[ChartSpec]:
        time_cols = profile["time_columns"]
        cat_cols = profile["category_columns"]
        num_cols = profile["numeric_columns"]
        charts: list[ChartSpec] = []

        if time_cols and num_cols:
            charts.append(ChartSpec(
                title=f"{num_cols[0]}趋势",
                chart_type="line",
                x=time_cols[0],
                y=num_cols[0],
                agg="sum",
                reason="检测到时间字段和数值字段，适合观察整体趋势变化。",
            ))
        if cat_cols and num_cols:
            charts.append(ChartSpec(
                title=f"按{cat_cols[0]}对比{num_cols[0]}",
                chart_type="bar",
                x=cat_cols[0],
                y=num_cols[0],
                agg="sum",
                reason="检测到分类字段和数值字段，适合比较不同类别贡献。",
            ))
            charts.append(ChartSpec(
                title=f"{num_cols[0]}占比",
                chart_type="pie",
                x=cat_cols[0],
                y=num_cols[0],
                agg="sum",
                reason="分类项数量适中时，适合查看结构占比。",
            ))
        if len(num_cols) >= 2:
            charts.append(ChartSpec(
                title=f"{num_cols[0]}与{num_cols[1]}关系",
                chart_type="scatter",
                x=num_cols[0],
                y=num_cols[1],
                agg="sum",
                reason="检测到两个数值字段，可观察二者的关系和离群点。",
            ))
        if not charts and num_cols:
            charts.append(ChartSpec(
                title=f"{num_cols[0]}分布",
                chart_type="hist",
                x=num_cols[0],
                y=None,
                agg="count",
                reason="仅检测到数值字段，适合先查看数值分布。",
            ))
        return charts

    def suggest_questions(self, profile: dict) -> list[str]:
        time_cols = profile["time_columns"]
        cat_cols = profile["category_columns"]
        num_cols = profile["numeric_columns"]
        questions: list[str] = []
        if time_cols and num_cols:
            questions.append(f"{num_cols[0]}随{time_cols[0]}是否有明显趋势或异常波动？")
        if cat_cols and num_cols:
            questions.append(f"不同{cat_cols[0]}的{num_cols[0]}差异是否明显？")
            questions.append(f"哪些{cat_cols[0]}贡献最高，哪些明显偏低？")
        if len(num_cols) >= 2:
            questions.append(f"{num_cols[0]}和{num_cols[1]}之间是否存在关联或异常点？")
        if not questions:
            questions.append("这张表的字段结构是否完整，是否需要补充数值或时间字段再分析？")
        return questions

    def insights(self, profile: dict) -> list[str]:
        total_rows = profile["total_rows"]
        total_columns = profile["total_columns"]
        time_n = len(profile["time_columns"])
        cat_n = len(profile["category_columns"])
        num_n = len(profile["numeric_columns"])
        out = [
            f"当前表包含 {total_rows} 行、{total_columns} 列，其中 {num_n} 个数值字段、{cat_n} 个分类字段、{time_n} 个时间字段。"
        ]
        if num_n and (time_n or cat_n):
            out.append("这张表具备基础聚合分析条件，可以先从趋势、分类对比和占比结构入手。")
        elif num_n:
            out.append("这张表有数值字段，但缺少明显时间或分类维度，适合先查看分布和异常值。")
        else:
            out.append("这张表暂未识别到数值字段，更适合做数据清洗、字段核对或明细查看。")
        return out


def analysis_summary(df: pd.DataFrame) -> dict:
    provider = BuiltinAnalysisProvider()
    profile = profile_table(df)
    charts = provider.suggest_charts(profile)
    return {
        "profile": profile,
        "recommended_questions": provider.suggest_questions(profile),
        "recommended_charts": [c.__dict__ for c in charts],
        "insights": provider.insights(profile),
        "provider": "builtin",
    }


def chart_points(df: pd.DataFrame, chart_type: str, x: str, y: str | None, agg: str = "sum") -> dict:
    if x not in df.columns:
        raise ValueError(f"Column not found: {x}")
    if chart_type != "hist" and (not y or y not in df.columns):
        raise ValueError(f"Numeric column not found: {y}")

    agg = (agg or "sum").lower()
    chart_type = (chart_type or "bar").lower()
    total_rows = int(len(df))
    limited = False

    if chart_type == "hist":
        values = _to_number_series(df[x]).dropna()
        if values.empty:
            points = []
        else:
            buckets = min(12, max(4, int(values.nunique() ** 0.5)))
            cats = pd.cut(values, bins=buckets, duplicates="drop")
            grouped = cats.value_counts().sort_index()
            points = [{"label": str(idx), "value": int(val)} for idx, val in grouped.items()]
    elif chart_type == "scatter":
        xv = _to_number_series(df[x])
        yv = _to_number_series(df[y])  # type: ignore[arg-type]
        valid = pd.DataFrame({"x": xv, "y": yv}).dropna()
        if len(valid) > MAX_CHART_POINTS:
            valid = valid.iloc[:MAX_CHART_POINTS]
            limited = True
        points = [
            {"label": str(i + 1), "x": float(r.x), "value": float(r.y)}
            for i, r in enumerate(valid.itertuples(index=False))
        ]
    else:
        work = df[[x, y]].copy()  # type: ignore[list-item]
        work[y] = _to_number_series(work[y])  # type: ignore[index]
        work = work.dropna(subset=[y])
        grouped = work.groupby(x, dropna=False)[y]
        if agg == "avg":
            series = grouped.mean()
        elif agg == "count":
            series = grouped.count()
        elif agg == "max":
            series = grouped.max()
        elif agg == "min":
            series = grouped.min()
        else:
            agg = "sum"
            series = grouped.sum()
        if chart_type in {"bar", "pie"}:
            series = series.sort_values(ascending=False)
        if len(series) > MAX_CHART_POINTS:
            series = series.head(MAX_CHART_POINTS)
            limited = True
        points = [{"label": str(idx), "value": float(val)} for idx, val in series.items()]

    return {
        "points": points,
        "total_rows": total_rows,
        "source_rows": total_rows,
        "aggregation": agg,
        "limited": limited,
        "limit": MAX_CHART_POINTS,
    }
