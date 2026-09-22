"""表格 IO 辅助：粘贴解析、预览摘要、导出 xlsx、图表列角色推断。"""
from __future__ import annotations

import io

import pandas as pd

PASTE_ROW_HINT = 100  # 粘贴超过此行数 → 提示改用上传


# ── 粘贴解析（Tab / 逗号 / 多空格 嗅探）────────────────────────────────────
def parse_paste(text: str):
    text = (text or "").strip("\n")
    if not text.strip():
        return None, "粘贴内容为空"
    first = text.splitlines()[0]
    if "\t" in first:
        sep = "\t"
    elif "," in first:
        sep = ","
    else:
        sep = r"\s{2,}"
    try:
        df = pd.read_csv(io.StringIO(text), sep=sep, engine="python")
        df.columns = [str(c).strip() for c in df.columns]
        return df, None
    except Exception as e:  # noqa: BLE001
        return None, f"解析失败：{e}"


# ── JSON 安全取值 ──────────────────────────────────────────────────────────
def _safe(v):
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return str(v)
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def as_named_dict(obj, default_name: str = "结果表") -> dict[str, pd.DataFrame]:
    """df -> {default_name: df}；dict 原样（键转 str）。"""
    if isinstance(obj, dict):
        return {str(k): v for k, v in obj.items()}
    return {default_name: obj}


def summarize(named: dict[str, pd.DataFrame], n: int = 8) -> list[dict]:
    """每张表 -> {name, columns, rows, total_rows, preview(head n)}。"""
    out = []
    for name, df in named.items():
        out.append({
            "name": str(name),
            "columns": [str(c) for c in df.columns],
            "rows": int(len(df)),
            "total_rows": int(len(df)),
            "preview": [[_safe(v) for v in r] for r in df.head(n).itertuples(index=False, name=None)],
        })
    return out


def result_to_xlsx(obj) -> bytes:
    named = as_named_dict(obj, "结果")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, df in named.items():
            df.to_excel(w, sheet_name=str(name)[:31] or "Sheet1", index=False)
    return buf.getvalue()


# ── 图表列角色推断（供 /api/charts/suggest）─────────────────────────────────
_TIME_KW = ("日期", "时间", "年月", "月份", "date", "time", "周", "季度", "year", "month")


def _is_time(col, s) -> bool:
    if any(k in str(col).lower() for k in _TIME_KW):
        return True
    try:
        parsed = pd.to_datetime(s.dropna().head(20), errors="coerce")
        return bool(parsed.notna().mean() > 0.7)
    except Exception:  # noqa: BLE001
        return False


def column_roles(df: pd.DataFrame):
    time, cat, num = [], [], []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            num.append(str(c))
        elif _is_time(c, s):
            time.append(str(c))
        else:
            cat.append(str(c))
    return time, cat, num


def suggest_charts(time, cat, num) -> list[dict]:
    out = []
    if time and num:
        out.append({"type": "line", "x": time[0], "y": num[0], "label": f"折线 · {num[0]} 随 {time[0]}"})
    if cat and num:
        out.append({"type": "bar", "x": cat[0], "y": num[0], "label": f"柱状 · {cat[0]} 对比"})
        out.append({"type": "pie", "x": cat[0], "y": num[0], "label": f"饼图 · {cat[0]} 占比"})
    if len(num) >= 2:
        out.append({"type": "scatter", "x": num[0], "y": num[1], "label": f"散点 · {num[0]} vs {num[1]}"})
    if not out and num:
        out.append({"type": "hist", "x": num[0], "y": None, "label": f"直方 · {num[0]} 分布"})
    return out
