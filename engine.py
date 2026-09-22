"""
表速通 · 引擎层 (engine.py)

职责：纯逻辑，不做任何输入输出（无 print / input / 文件路径硬编码）。
核心能力：
  - 读取源表 / 目标表（从内存中的 DataFrame，而非本地目录）
  - 调用 DeepSeek 反推 process_data 逻辑，并用完整目标表本地严格校验
  - 模型自修正循环 + 差异摘要
  - 锁定逻辑后，对新源表执行，产出结果 DataFrame

界面层（app.py / CLI）只负责拿数据进来、把结果展示出去。
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import re
from typing import Any

import numpy as np
import openpyxl
import pandas as pd
import pandas.testing as pdt
from openai import OpenAI

# -----------------------------------------------------------------------------
# 常量
# -----------------------------------------------------------------------------
SAMPLE_ROWS = 10
MAX_LLM_SELF_RETRIES = 3
MAX_CLARIFICATION_ROUNDS = 3

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"
REQUEST_TIMEOUT = 90.0   # 单次 LLM 调用超时（秒），防止无限挂起

# Generated code is intentionally limited to deterministic, in-memory table
# transforms.  This is a guardrail for the local demo, not a replacement for a
# container/VM sandbox in an untrusted multi-user deployment.
_ALLOWED_IMPORT_ROOTS = {
    "_strptime",
    "collections",
    "datetime",
    "decimal",
    "functools",
    "itertools",
    "math",
    "numpy",
    "operator",
    "pandas",
    "re",
    "statistics",
    "string",
    "time",
}
_FORBIDDEN_NAMES = {
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "exit",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "print",
    "quit",
    "setattr",
    "vars",
}
_FORBIDDEN_ATTRIBUTE_PARTS = {
    "__class__",
    "__dict__",
    "__globals__",
    "__subclasses__",
    "chmod",
    "connect",
    "dump",
    "dumps",
    "load",
    "loads",
    "mkdir",
    "open",
    "os",
    "pathlib",
    "popen",
    "read_clipboard",
    "read_csv",
    "read_excel",
    "read_feather",
    "read_fwf",
    "read_gbq",
    "read_hdf",
    "read_html",
    "read_json",
    "read_orc",
    "read_parquet",
    "read_pickle",
    "read_sas",
    "read_spss",
    "read_sql",
    "read_stata",
    "read_table",
    "read_xml",
    "remove",
    "rename",
    "rmdir",
    "socket",
    "spawn",
    "subprocess",
    "system",
    "tempfile",
    "to_clipboard",
    "to_csv",
    "to_excel",
    "to_feather",
    "to_gbq",
    "to_hdf",
    "to_json",
    "to_orc",
    "to_parquet",
    "to_pickle",
    "to_sql",
    "unlink",
    "urllib",
}

DEFAULT_FORMULA_CLUES = (
    "请根据源表与目标表的列名及样本行，推断将源数据转换为目标表的 Pandas 处理逻辑。"
    " source_dfs 的键为「文件名_工作表名」（不含扩展名的文件名 + 下划线 + 工作表名）。"
)


def _normalize_for_compare(df: pd.DataFrame) -> pd.DataFrame:
    """比对前归一化：字符串类型统一、float 精度统一、NA 表示统一，不改变值语义。"""
    df = df.copy()
    for col in df.columns:
        s = df[col]
        # float 列四舍五入到 4 位，消除 57.15048 vs 57.15 类差异（保留到分的精度）
        if pd.api.types.is_float_dtype(s):
            df[col] = s.round(4)
            continue
        # 纯整数列保持原样
        if pd.api.types.is_integer_dtype(s) and not pd.api.types.is_extension_array_dtype(s):
            continue
        # object / string / categorical / 可空整数：先尝试数值化
        # —— 解决「合计行含 NA 使整数列退化成 object」与 float 列比不相等的问题
        coerced = pd.to_numeric(s, errors="coerce")
        non_na = s.notna().sum()
        if non_na > 0 and coerced.notna().sum() == non_na:
            df[col] = coerced.round(4)          # 实为数值列（含被 NA 污染成 object 的整数列）
        else:
            df[col] = s.astype(object)          # 真正的字符串列 → 统一 object，消除容器差异
    return df

SYSTEM_PROMPT = """你是一个只输出可执行 Python 代码的代码生成器。

硬性规则：
1. 只输出纯净的 Python 代码，禁止使用 Markdown 代码块标记，禁止任何解释性文字。
2. 必须定义且仅依赖一个入口函数：def process_data(source_dfs):
3. source_dfs 的键为「文件名_工作表名」格式字符串，值为 pandas.DataFrame。
4. 函数返回值：
   - 若目标只有一张结果表，返回单个 pandas.DataFrame。
   - 若目标包含多个工作表（下方目标数据会以「工作表「名称」」逐个列出），
     必须返回一个 dict，键为目标工作表名（字符串，与列出的名称完全一致），
     值为该工作表对应的 pandas.DataFrame。
5. 代码需具备防御性：显式处理缺失值、在聚合前尽量将数值列转为数值类型（如 pd.to_numeric(errors="coerce")）。
6. 仅可使用标准库与 pandas（已预装），不要假设存在其他第三方包。
7. 不要读写文件，不要打印调试信息。"""

USER_PROMPT_TEMPLATE = """请根据以下信息，编写满足 SYSTEM 要求的 process_data 函数。

【源数据样本】（每张表仅前若干行；键为「文件名_工作表名」）
{source_data}

【目标数据样本】（仅前若干行；完整目标用于本地校验）
{target_data}

【目标表公式线索】
{formula_hints}

【公式/业务线索】
{formula_clues}

【需求澄清 / 用户补充说明】
{clarification_context}

【上一轮与目标表的偏差摘要】
{mismatch_context}

【上一轮执行或校验错误反馈（模型自修正）】
{error_feedback}

请直接输出完整 Python 代码。"""


# -----------------------------------------------------------------------------
# DataFrame 读取（从上传的文件对象，而非本地目录）
# -----------------------------------------------------------------------------
def load_dfs_from_uploaded(files: list[tuple[str, Any]]) -> dict[str, pd.DataFrame]:
    """
    files: [(filename, file_like), ...]
    遍历每个 Excel 的所有 sheet，键名为「文件名(stem)_工作表名」，重名自动加后缀。
    """
    out: dict[str, pd.DataFrame] = {}
    for filename, file_like in files:
        stem = re.sub(r"\.xlsx$|\.xls$", "", filename, flags=re.IGNORECASE)
        xl = pd.ExcelFile(file_like, engine="openpyxl")
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            key = f"{stem}_{sheet}"
            base_key, n = key, 2
            while key in out:
                key = f"{base_key}_{n}"
                n += 1
            out[key] = df
    return out


def load_single_df(filename: str, file_like: Any) -> pd.DataFrame:
    """读取目标表（取第一个 sheet）。保留向后兼容。"""
    return pd.read_excel(file_like, sheet_name=0, engine="openpyxl")


def load_target(filename: str, file_like: Any) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """
    读取目标表样例：
      - 单工作表 → 返回 DataFrame（与旧逻辑一致，向后兼容）
      - 多工作表 → 返回 {工作表名: DataFrame}，表示「结果应产出多张表」
    """
    xl = pd.ExcelFile(file_like, engine="openpyxl")
    names = list(xl.sheet_names)
    if len(names) <= 1:
        return xl.parse(names[0])
    return {s: xl.parse(s) for s in names}


# -----------------------------------------------------------------------------
# 多工作表结果/目标的统一处理
# -----------------------------------------------------------------------------
_SINGLE = "__single__"   # 单表内部占位 sheet 名


def as_sheet_dict(obj: Any) -> dict[str, pd.DataFrame]:
    """把结果/目标统一成 {工作表名: DataFrame}。单张表用占位键 __single__。"""
    if isinstance(obj, pd.DataFrame):
        return {_SINGLE: obj}
    if isinstance(obj, dict) and obj and all(isinstance(v, pd.DataFrame) for v in obj.values()):
        return {str(k): v for k, v in obj.items()}
    raise TypeError("结果必须是 DataFrame 或 {工作表名: DataFrame}。")


def is_multi_sheet(obj: Any) -> bool:
    """目标/结果是否为多工作表形态。"""
    return isinstance(obj, dict) and len([v for v in obj.values() if isinstance(v, pd.DataFrame)]) >= 1 \
        and not isinstance(obj, pd.DataFrame)


def _colset_sim(a: Any, b: Any) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _name_sim(x: str, y: str) -> float:
    import difflib as _dl
    return _dl.SequenceMatcher(None, str(x).lower(), str(y).lower()).ratio()


def _align_result_to_target(
    result_sheets: dict[str, pd.DataFrame],
    target_sheets: dict[str, pd.DataFrame],
) -> list[tuple[str, pd.DataFrame | None, pd.DataFrame]]:
    """把结果各 sheet 按「名称 + 列集合」贪心对齐到目标 sheet，返回 [(目标sheet名, 结果df或None, 目标df)]。"""
    used: set[str] = set()
    pairs: list[tuple[str, pd.DataFrame | None, pd.DataFrame]] = []
    rkeys = list(result_sheets.keys())
    for tname, tdf in target_sheets.items():
        best, bk = -1.0, None
        for rk in rkeys:
            if rk in used:
                continue
            score = 0.4 * _name_sim(rk, tname) + 0.6 * _colset_sim(result_sheets[rk].columns, tdf.columns)
            if score > best:
                best, bk = score, rk
        if bk is not None:
            used.add(bk)
            pairs.append((tname, result_sheets[bk], tdf))
        else:
            pairs.append((tname, None, tdf))
    return pairs


def _result_target_pairs(result: Any, target: Any) -> list[tuple[str | None, pd.DataFrame | None, pd.DataFrame]]:
    """统一产出 (sheet名, 结果df, 目标df) 配对；单表时 sheet名为 None。"""
    t = as_sheet_dict(target)
    r = as_sheet_dict(result)
    if _SINGLE in t:
        rdf = list(r.values())[0] if r else None
        return [(None, rdf, t[_SINGLE])]
    return _align_result_to_target(r, t)


def assert_results_match(result: Any, target: Any) -> None:
    """多工作表感知的严格比对，不一致抛 AssertionError。"""
    t = as_sheet_dict(target)
    r = as_sheet_dict(result)
    if len(r) != len(t):
        raise AssertionError(f"结果产出了 {len(r)} 张表，但目标需要 {len(t)} 张。")
    for tname, rdf, tdf in _result_target_pairs(result, target):
        if rdf is None:
            raise AssertionError(f"缺少与目标工作表「{tname}」对应的结果表。")
        rdf2 = rdf
        if set(rdf.columns) == set(tdf.columns):
            rdf2 = rdf[list(tdf.columns)]
        pdt.assert_frame_equal(
            _normalize_for_compare(rdf2.reset_index(drop=True)),
            _normalize_for_compare(tdf.reset_index(drop=True)),
            check_dtype=False,
            check_names=False,
            atol=0.005,
        )


# -----------------------------------------------------------------------------
# 公式提取（三层策略：读公式 → 检测错乱 → 翻译/喂LLM）
# -----------------------------------------------------------------------------
def extract_target_formulas(file_like: Any) -> dict:
    """
    从目标 Excel 文件中提取公式信息。

    返回:
      {
        "total_cells": int,
        "formula_cells": int,
        "trusted_formulas": int,
        "broken_formulas": int,
        "formulas": [
          {"cell": "D2", "formula": "=B2*C2", "result": 100, "displayed": 100, "trusted": true},
          ...
        ],
        "hint_text": "人话摘要（喂给 LLM）"
      }
    """
    raw = file_like.read() if hasattr(file_like, "read") else file_like
    if isinstance(raw, bytes):
        buf_f = io.BytesIO(raw)
        buf_v = io.BytesIO(raw)
    else:
        buf_f = io.BytesIO(raw.encode() if isinstance(raw, str) else raw)
        buf_v = io.BytesIO(raw.encode() if isinstance(raw, str) else raw)

    wb_f = openpyxl.load_workbook(buf_f, data_only=False)  # 读公式文本
    wb_v = openpyxl.load_workbook(buf_v, data_only=True)   # 读计算结果

    formulas: list[dict] = []
    trusted = 0
    broken = 0
    total = 0
    multi = len(wb_f.sheetnames) > 1

    for ws_name in wb_f.sheetnames:
        ws_f = wb_f[ws_name]
        ws_v = wb_v[ws_name]
        max_rows = min(ws_f.max_row, 200)

        for row in ws_f.iter_rows(min_row=1, max_row=max_rows, max_col=min(ws_f.max_column, 30)):
            for cell in row:
                total += 1
                val_f = cell.value
                if val_f is None:
                    continue
                if not isinstance(val_f, str) or not str(val_f).startswith("="):
                    continue
                # 有公式
                formula_str = str(val_f)
                # 多工作表时在坐标前加「工作表名!」前缀，便于 LLM 区分
                cell_coord = f"{ws_name}!{cell.coordinate}" if multi else cell.coordinate
                # 读取计算结果
                try:
                    computed = ws_v[cell.coordinate].value
                except Exception:
                    computed = None

                cmp_ok = True
                # 用 pandas 读同单元格的显示值对比
                displayed = _read_display_value(raw, cell.coordinate, ws_name)

                if computed is None:
                    # 无法获取公式计算结果（文件未被 Excel 打开计算过）
                    cmp_ok = None  # 无法验证 → 标记为未知
                elif displayed is not None and computed is not None:
                    try:
                        if isinstance(computed, (int, float)) and isinstance(displayed, (int, float)):
                            diff = abs(float(computed) - float(displayed))
                            cmp_ok = diff < 0.001
                        elif str(computed) != str(displayed):
                            cmp_ok = False
                    except Exception:
                        cmp_ok = True  # 无法比较则信任

                if cmp_ok is True:
                    trusted += 1
                elif cmp_ok is False:
                    broken += 1
                # cmp_ok is None → 不计数（无法验证）

                formulas.append({
                    "cell": cell_coord,
                    "formula": formula_str,
                    "result": computed,
                    "displayed": displayed,
                    "trusted": cmp_ok,
                })

    wb_f.close()
    wb_v.close()

    # 生成人类可读摘要
    hint_text = _build_formula_hint(formulas, trusted, broken)

    return {
        "total_cells": total,
        "formula_cells": len(formulas),
        "trusted_formulas": trusted,
        "broken_formulas": broken,
        "formulas": formulas,
        "hint_text": hint_text,
    }


def _read_display_value(raw: bytes, coord: str, sheet: Any = 0) -> Any:
    """用 pandas 读取目标表特定单元格的显示值。sheet 可为名称或序号。"""
    try:
        df = pd.read_excel(io.BytesIO(raw), sheet_name=sheet, engine="openpyxl", header=None)
        col_letter = "".join(c for c in coord if c.isalpha())
        row_num = int("".join(c for c in coord if c.isdigit()))
        col_idx = openpyxl.utils.column_index_from_string(col_letter) - 1
        row_idx = row_num - 1
        if row_idx < len(df) and col_idx < len(df.columns):
            return df.iloc[row_idx, col_idx]
    except Exception:
        pass
    return None


def _build_formula_hint(formulas: list[dict], trusted: int, broken: int) -> str:
    """构建喂给 LLM 的公式线索文本。"""
    if not formulas:
        return "（目标表不包含公式，请根据数据样本推断处理逻辑。）"

    verified = trusted + broken
    unverified = len(formulas) - verified
    lines = [f"目标表的 {len(formulas)} 个单元格包含公式。"]

    if verified == 0:
        lines.append(f"[!] 无法验证公式计算结果（文件未被 Excel 计算过），列出公式仅供参考意图。")
    elif broken == 0:
        lines.append(f"[OK] 已验证 {trusted} 个公式，计算结果与表内显示值一致，公式可信。")
    elif trusted > 0 and broken > 0:
        lines.append(f"[!] {trusted} 个公式可信，但 {broken} 个公式的计算结果与表内显示值不一致（可能是用户手动改过或公式有误）。")
        lines.append("   可信的公式可以直接翻译成 pandas 操作；不可信的公式仅供参考意图。")
    elif broken > 0 and trusted == 0:
        lines.append(f"[!] 所有 {broken} 个公式的计算结果与表内显示值都不一致（公式可能已错乱），仅供参考。")

    if unverified > 0:
        lines.append(f"（另有 {unverified} 个公式无法验证，已一并列出。）")

    # 列出所有公式（区分可信/不可信/未知）
    lines.append("\n公式列表：")
    for f in formulas[:25]:
        if f["trusted"] is True:
            tag = "[可信]"
        elif f["trusted"] is False:
            tag = "[不可信]"
        else:
            tag = "[未知]"
        lines.append(f"  {tag} {f['cell']}: {f['formula']}")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# 文本/差异工具（与原版一致，纯函数）
# -----------------------------------------------------------------------------
def _strip_model_thinking(raw: str) -> str:
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


def _extract_python_code(text: str) -> str:
    t = _strip_model_thinking(text)
    m = re.match(r"^\s*```(?:python)?\s*\n(.*)\n```\s*$", t, flags=re.DOTALL | re.IGNORECASE)
    if m:
        t = m.group(1).strip()
    return t


def _format_df_for_prompt(df: pd.DataFrame, max_rows: int = SAMPLE_ROWS) -> str:
    return df.head(max_rows).to_string(index=False)


def _format_source_dfs_for_prompt(source_dfs: dict[str, pd.DataFrame], max_rows: int = SAMPLE_ROWS) -> str:
    parts = [f"--- {name} ---\n{_format_df_for_prompt(df, max_rows)}" for name, df in source_dfs.items()]
    return "\n\n".join(parts)


def _format_target_for_prompt(target: Any, max_rows: int = SAMPLE_ROWS) -> str:
    """目标样本格式化：单表照旧；多表逐 sheet 列出并提示需返回 dict。"""
    sheets = as_sheet_dict(target)
    if _SINGLE in sheets:
        return _format_df_for_prompt(sheets[_SINGLE], max_rows)
    parts = [
        f"⚠ 目标包含 {len(sheets)} 个工作表，process_data 必须返回 dict，"
        f"键为以下工作表名：{list(sheets.keys())}"
    ]
    for name, df in sheets.items():
        parts.append(
            f"--- 工作表「{name}」（共 {df.shape[0]} 行 × {df.shape[1]} 列）---\n"
            f"{_format_df_for_prompt(df, max_rows)}"
        )
    return "\n\n".join(parts)


def describe_results_mismatch(result: Any, target: Any) -> str:
    """多工作表感知的人话差异诊断：单表照旧；多表逐 sheet 汇总。"""
    t = as_sheet_dict(target)
    r = as_sheet_dict(result)
    if _SINGLE in t:
        rdf = list(r.values())[0] if r else result
        return describe_frame_mismatch(rdf, t[_SINGLE])

    lines: list[str] = []
    if len(r) != len(t):
        lines.append(
            f"⚠ 工作表数量不一致：结果产出 {len(r)} 张表，目标需要 {len(t)} 张"
            f"（{', '.join(t.keys())}）。"
        )
    for tname, rdf, tdf in _align_result_to_target(r, t):
        lines.append(f"\n=== 工作表「{tname}」 ===")
        if rdf is None:
            lines.append(f"结果里没有对应「{tname}」的表。")
            continue
        lines.append(describe_frame_mismatch(rdf, tdf))
    return "\n".join(lines)


def classify_results_mismatch(result: Any, target: Any) -> dict:
    """多工作表感知的结构化分类诊断，供 UI 渲染引导问题使用。"""
    t = as_sheet_dict(target)
    r = as_sheet_dict(result) if isinstance(result, (pd.DataFrame, dict)) else {}
    if _SINGLE in t:
        rdf = list(r.values())[0] if r else result
        return classify_frame_mismatch(rdf, t[_SINGLE])

    merged = {"category": "value_error", "missing_cols": [], "extra_cols": [],
              "row_count_diff": 0, "row_direction": None, "bad_value_cols": [],
              "precision_cols": [], "value_examples": [], "row_examples": []}
    cats: list[str] = []
    for tname, rdf, tdf in _align_result_to_target(r, t):
        if rdf is None:
            merged["missing_cols"] += [f"{tname}·{c}" for c in tdf.columns]
            cats.append("missing_cols")
            merged["row_examples"].append(f"[{tname}] 结果里完全缺少这张表")
            continue
        info = classify_frame_mismatch(rdf, tdf)
        has_issue = (info["missing_cols"] or info["extra_cols"] or info["row_count_diff"]
                     or info["bad_value_cols"] or info["precision_cols"])
        if not has_issue:
            continue
        cats.append(info["category"])
        merged["missing_cols"]  += [f"{tname}·{c}" for c in info["missing_cols"]]
        merged["extra_cols"]    += [f"{tname}·{c}" for c in info["extra_cols"]]
        merged["bad_value_cols"] += [f"{tname}·{c}" for c in info["bad_value_cols"]]
        merged["precision_cols"] += [f"{tname}·{c}" for c in info["precision_cols"]]
        merged["value_examples"] += [f"[{tname}] {e}" for e in info.get("value_examples", [])]
        merged["row_examples"]   += [f"[{tname}] {e}" for e in info.get("row_examples", [])]
        if info["row_count_diff"]:
            merged["row_count_diff"] += info["row_count_diff"]
            merged["row_direction"] = info["row_direction"]

    distinct = set(cats)
    if not cats:
        merged["category"] = "value_error"
    elif len(distinct) == 1:
        merged["category"] = cats[0]
    else:
        merged["category"] = "mixed"
    return merged


def describe_frame_mismatch(result_df: pd.DataFrame, target_df: pd.DataFrame) -> str:
    """生成人类可读的差异诊断报告。分类输出 + 建议澄清方向。"""
    if not isinstance(result_df, pd.DataFrame):
        return f"返回的不是表格，无法对比。"

    lines: list[str] = []
    precision_issues = 0
    value_issues = 0
    col_issues = 0
    row_count_diff = abs(len(result_df) - len(target_df))
    col_count_diff = abs(len(result_df.columns) - len(target_df.columns))

    # ── 1. 总体概况 ──
    lines.append(f"生成结果有 {result_df.shape[0]} 行 {result_df.shape[1]} 列，目标表有 "
                 f"{target_df.shape[0]} 行 {target_df.shape[1]} 列。")

    # ── 2. 行数差异 ──
    if row_count_diff > 0:
        direction = "多了" if len(result_df) > len(target_df) else "少了"
        lines.append(f"⚠ 行数不一致：比目标表{direction} {row_count_diff} 行。")

    # ── 3. 列名差异 ──
    rs, ts = set(result_df.columns), set(target_df.columns)
    extra = sorted(rs - ts)
    missing = sorted(ts - rs)
    if extra:
        col_issues += len(extra)
        lines.append(f"⚠ 多出了列：{', '.join(extra)}（目标表不需要这些列）")
    if missing:
        col_issues += len(missing)
        lines.append(f"⚠ 缺少了列：{', '.join(missing)}（目标表需要但没有生成）")

    # ── 4. 顺序检查 ──
    if rs == ts and list(result_df.columns) != list(target_df.columns):
        lines.append(f"⚠ 列的顺序不一致（内容对但排列顺序不同）。")

    # ── 5. 逐值对比（分类诊断） ──
    common = [c for c in target_df.columns if c in rs]
    if common:
        try:
            r = result_df[common].reset_index(drop=True)
            t = target_df[common].reset_index(drop=True)
            precision_examples: list[str] = []
            value_examples: list[str] = []

            for i in _rows_to_scan(min(len(r), len(t))):
                for c in common:
                    rv, tv = r.iloc[i][c], t.iloc[i][c]
                    if _cells_equal(rv, tv):
                        continue

                    # 判断是否为纯精度问题
                    is_precision = False
                    try:
                        frv, ftv = float(rv), float(tv)
                        diff_pct = abs(frv - ftv) / max(abs(ftv), 0.01)
                        is_precision = diff_pct < 0.01  # 差异 <1% 视为精度问题
                    except (TypeError, ValueError):
                        pass

                    if is_precision and len(precision_examples) < 3:
                        precision_issues += 1
                        precision_examples.append(f"「{c}」计算结果是 {rv}，目标表是 {tv}")
                    elif not is_precision and len(value_examples) < 5:
                        value_issues += 1
                        value_examples.append(f"「{c}」行{i+1} 得到 {rv}，但应该是 {tv}")

            if precision_issues > 0:
                pct = round(precision_issues / max(len(r) * len(common), 1) * 100)
                lines.append(f"⚠ 有 {precision_issues} 处数值精度不一致（约占总数据的 {pct}%），"
                             f"计算结果在小数位上略有偏差。")
                for ex in precision_examples:
                    lines.append(f"   例如：{ex}")

            if value_issues > 0:
                lines.append(f"⚠ 有 {value_issues} 处数值明显不对：")
                for ex in value_examples:
                    lines.append(f"   {ex}")

        except Exception as e:
            lines.append(f"对比过程中出错：{e}")

    # ── 6. 建议澄清方向 ──
    suggestions: list[str] = []
    if precision_issues > 0 and value_issues == 0 and row_count_diff == 0 and col_issues == 0:
        suggestions.append("✅ 结构完全正确！只是小数位数不一致。你可以说："
                           "「所有计算结果保留两位小数」或「百分比保留一位小数」")
    elif precision_issues > 0:
        suggestions.append("💡 有数值精度问题，可以告诉系统「所有金额保留两位小数」")
    if col_issues > 0:
        suggestions.append("💡 列名有差异，可以告诉系统「我需要的是XX列，不需要YY列」")
    if row_count_diff > 0:
        suggestions.append("💡 行数不一致，可能是过滤条件不对。例如「只要在职员工」或「只统计有数据的」")
    if value_issues > 0:
        suggestions.append("💡 有数值明显不对，可能是计算公式或分组方式有误。"
                           "可以用大白话描述计算规则，例如「实发工资 = 基本工资 + 奖金 - 扣款」")
    if not suggestions:
        suggestions.append("💡 你可以用大白话补充：这些表格是怎么处理的？"
                           "例如「按省份汇总销售额」或「关联两张表的SKU取类目」")

    lines.append("")
    lines.append("📋 你可以这样澄清：")
    lines.extend(suggestions)

    return "\n".join(lines)


def _rows_to_scan(n: int, head: int = 15, tail: int = 6) -> list[int]:
    """诊断时要检查的行索引：头部 + 尾部都覆盖，确保底部的合计/汇总行不被漏掉。"""
    if n <= head + tail:
        return list(range(n))
    return list(range(head)) + list(range(n - tail, n))


def _cells_equal(rv: Any, tv: Any) -> bool:
    """NA 安全的单元格相等判断：都空=相等；一空一非空=不等；数值按近似相等。"""
    rv_na, tv_na = pd.isna(rv), pd.isna(tv)
    if rv_na or tv_na:
        return bool(rv_na and tv_na)
    try:
        if bool(rv == tv):
            return True
    except Exception:
        pass
    try:
        return float(rv) == float(tv)
    except (TypeError, ValueError):
        return False


def _clean_val(v: Any) -> str:
    """格式化单元格值用于展示：去掉 numpy 包装（np.float64(...)），字符串保留引号。"""
    try:
        if isinstance(v, np.generic):
            v = v.item()
    except Exception:
        pass
    if isinstance(v, float):
        v = round(v, 4)
    return repr(v)


def classify_frame_mismatch(result_df: pd.DataFrame, target_df: pd.DataFrame) -> dict:
    """结构化分类诊断，供 UI 渲染引导问题使用。不调 LLM，纯数据比对。"""
    empty = {"category": "crash", "missing_cols": [], "extra_cols": [],
             "row_count_diff": 0, "row_direction": None,
             "bad_value_cols": [], "precision_cols": [], "value_examples": []}
    if not isinstance(result_df, pd.DataFrame):
        return empty

    rs = set(result_df.columns)
    ts = set(target_df.columns)
    missing_cols = sorted(ts - rs)
    extra_cols   = sorted(rs - ts)
    row_diff     = len(result_df) - len(target_df)
    row_direction = "more" if row_diff > 0 else ("fewer" if row_diff < 0 else None)

    bad_value_cols: list[str] = []
    precision_cols: list[str] = []
    value_examples: list[str] = []
    common = [c for c in target_df.columns if c in rs]
    if common:
        try:
            r = result_df[common].reset_index(drop=True)
            t = target_df[common].reset_index(drop=True)
            scan_idx = _rows_to_scan(min(len(r), len(t)))
            for c in common:
                has_val_err = has_prec = False
                for i in scan_idx:
                    rv, tv = r.iloc[i][c], t.iloc[i][c]
                    if _cells_equal(rv, tv):
                        continue
                    is_prec = False
                    try:
                        frv, ftv = float(rv), float(tv)
                        is_prec = abs(frv - ftv) / max(abs(ftv), 0.01) < 0.01
                    except (TypeError, ValueError):
                        pass
                    if is_prec:
                        has_prec = True
                    else:
                        has_val_err = True
                        # 仅在行列对齐时采集样例，否则按位置比对会产生误导
                        aligned_shape = (row_diff == 0 and not missing_cols and not extra_cols)
                        if aligned_shape and len(value_examples) < 4:
                            value_examples.append(
                                f"第 {i + 1} 行「{c}」：AI 得到 {_clean_val(rv)}，目标表是 {_clean_val(tv)}"
                            )
                if has_val_err:
                    bad_value_cols.append(c)
                elif has_prec:
                    precision_cols.append(c)
        except Exception:
            pass

    # ── 行级定位：列对齐但行数不同时，指出 AI 多留/漏掉的具体行 ──
    # 解决「数据量大、用户看不出哪一行是脏数据」的痛点
    row_examples: list[str] = []
    if row_diff != 0 and not missing_cols and not extra_cols and common:
        try:
            rr = result_df[common].copy()
            tt = target_df[common].copy()
            for c in common:
                if pd.api.types.is_float_dtype(rr[c]):
                    rr[c] = rr[c].round(2)
                if pd.api.types.is_float_dtype(tt[c]):
                    tt[c] = tt[c].round(2)
            rr_s, tt_s = rr.astype(str), tt.astype(str)
            cols = list(common)
            show = cols[:4]

            extra_rows = rr[rr_s.merge(tt_s, how="left", indicator=True, on=cols)
                            ["_merge"].values == "left_only"]
            miss_rows = tt[tt_s.merge(rr_s, how="left", indicator=True, on=cols)
                           ["_merge"].values == "left_only"]

            # 守卫：只有当「多/漏的行数」与实际行数差额吻合时才认为是干净的行级差异，
            # 否则多半是数值噪声导致的整体错位，列样例反而误导用户。
            n_extra_exp = max(0, row_diff)
            n_miss_exp  = max(0, -row_diff)
            if n_extra_exp and len(extra_rows) <= n_extra_exp + 1:
                for _, row in extra_rows.head(3).iterrows():
                    desc = "，".join(f"{c}={_clean_val(row[c])}" for c in show)
                    row_examples.append(f"AI 多保留了这行（目标表里没有）：{desc}")
            if n_miss_exp and len(miss_rows) <= n_miss_exp + 1:
                for _, row in miss_rows.head(3).iterrows():
                    desc = "，".join(f"{c}={_clean_val(row[c])}" for c in show)
                    row_examples.append(f"目标表有但 AI 漏掉的行：{desc}")
        except Exception:
            pass

    issues = (
        (1 if (missing_cols or extra_cols) else 0)
        + (1 if row_diff != 0 else 0)
        + (1 if bad_value_cols else 0)
    )
    if issues == 0 and precision_cols:
        category = "precision_only"
    elif issues > 1:
        category = "mixed"
    elif missing_cols or extra_cols:
        category = "missing_cols"
    elif row_diff != 0:
        category = "row_count"
    else:
        category = "value_error"

    return {
        "category": category,
        "missing_cols": missing_cols,
        "extra_cols": extra_cols,
        "row_count_diff": abs(row_diff),
        "row_direction": row_direction,
        "bad_value_cols": bad_value_cols,
        "precision_cols": precision_cols,
        "value_examples": value_examples,
        "row_examples": row_examples,
    }


# few-shot：教 LLM 找「人不容易察觉」的问题，并给可照抄的澄清话术
_DIAGNOSE_FEWSHOT = """示例1（异常数据未过滤）：
比对发现：AI 多保留了一行「渠道=腾讯广告，转化量=66063，点击量=65945」。
诊断：这一行的转化量比点击量还大，现实中不可能（转化只会来自点击），属于脏数据，目标表把它过滤掉了，但 AI 没识别出来。
建议澄清：请过滤掉「转化量大于点击量」的异常行，这类数据不计入结果。

示例2（分类边界口径不同）：
比对发现：第 5 行「会员等级」AI 得到「白银」，目标表是「黄金」，该行累计消费=20000。
诊断：消费正好等于 20000 时，AI 归到了「白银」，但目标表把它算作「黄金」，说明分级是「大于等于」边界而非「大于」。
建议澄清：会员等级按「大于等于」划分：消费≥20000 即为黄金，≥50000 为钻石。

示例3（精度口径）：
比对发现：多处「占比(%)」AI 与目标表在小数位上有微小差异。
诊断：AI 没有对百分比做四舍五入，目标表统一保留了两位小数。
建议澄清：所有百分比和金额都保留两位小数。"""


def _lookup_extra_rows_in_source(
    result_df: pd.DataFrame,
    target_df: pd.DataFrame,
    source_dfs: dict[str, pd.DataFrame],
    max_rows: int = 5,
) -> str:
    """找出 AI 多保留的行（结果有、目标无），回溯它们在源表中的完整原始数据。"""
    try:
        common = [c for c in target_df.columns if c in result_df.columns]
        if not common:
            return ""
        rr, tt = result_df[common].copy(), target_df[common].copy()
        for c in common:
            if pd.api.types.is_float_dtype(rr[c]):
                rr[c] = rr[c].round(2)
            if pd.api.types.is_float_dtype(tt[c]):
                tt[c] = tt[c].round(2)
        merged = rr.astype(str).merge(tt.astype(str), how="left", indicator=True, on=common)
        extras = result_df[merged["_merge"].values == "left_only"].head(max_rows)
        if extras.empty:
            return ""

        lines: list[str] = []
        for _, erow in extras.iterrows():
            for sk, sdf in source_dfs.items():
                shared = [c for c in sdf.columns if c in result_df.columns]
                if not shared:
                    continue
                mask = pd.Series(True, index=sdf.index)
                for c in shared:
                    mask &= (sdf[c].astype(str) == str(erow[c]))
                hit = sdf[mask]
                if not hit.empty:
                    srow = hit.iloc[0]
                    desc = "，".join(f"{c}={_clean_val(srow[c])}" for c in sdf.columns)
                    lines.append(f"  来自源表[{sk}]：{desc}")
        return "\n".join(lines)
    except Exception:
        return ""


def diagnose_mismatch(
    source_dfs: dict[str, pd.DataFrame],
    target_df: pd.DataFrame,
    result_df: pd.DataFrame | None,
    diff_summary: str,
    clarification_history: str,
    api_key: str,
    base_url: str = DEEPSEEK_BASE_URL,
    model: str = DEEPSEEK_MODEL,
    sample_rows: int = SAMPLE_ROWS,
) -> dict:
    """
    LLM 诊断层：拿确定性比对出的事实差异 + 源/目标数据，
    解释「人不容易察觉」的错误原因，并给出一句可照抄的澄清话术。

    返回 {"diagnosis": str, "suggested_clarification": str}；失败时返回空串。
    """
    fallback = {"diagnosis": "", "suggested_clarification": ""}
    key = (api_key or "").strip()
    if not key or not isinstance(result_df, (pd.DataFrame, dict)):
        return fallback

    t_sheets = as_sheet_dict(target_df)
    r_sheets = as_sheet_dict(result_df) if isinstance(result_df, (pd.DataFrame, dict)) else {}
    multi = _SINGLE not in t_sheets

    if multi:
        # 多工作表：逐 sheet 列出目标与结果样本；源表回溯暂不分 sheet（避免误配），留空
        extra_src_detail = ""
        target_block = _format_target_for_prompt(target_df, sample_rows)
        result_block = "\n\n".join(
            f"--- 工作表「{n}」---\n{_format_df_for_prompt(df.head(sample_rows))}"
            for n, df in r_sheets.items()
        ) or "（结果为空）"
    else:
        single_target = t_sheets[_SINGLE]
        single_result = list(r_sheets.values())[0] if r_sheets else result_df
        # 把「AI 多保留的行」在源表中的完整数据挖出来——脏数据的铁证（如转化量>点击量）
        # 往往在被过滤的那几行的「源表全部列」里，而样本窗口看不到。
        extra_src_detail = _lookup_extra_rows_in_source(single_result, single_target, source_dfs)
        target_block = _format_df_for_prompt(single_target.head(sample_rows))
        result_block = _format_df_for_prompt(single_result.head(sample_rows))

    try:
        client = OpenAI(api_key=key, base_url=base_url, timeout=REQUEST_TIMEOUT)
        sys_prompt = (
            "你是数据处理诊断助手，服务于不懂技术的财务/运营人员。"
            "下面给你：源表、用户期望的目标表、AI 生成的错误结果，以及程序自动比对出的"
            "【真实差异】。请仅基于这些真实差异分析（不要臆造不存在的差异），找出 AI "
            "到底哪条规则理解错了，尤其注意人在大数据量下不容易察觉的规律（异常值过滤、"
            "分类边界、计算口径、单位等）。\n\n"
            "只输出一个 JSON：{\"diagnosis\": \"一句话说明问题（点出具体哪行/哪列、为什么错）\", "
            "\"suggested_clarification\": \"一句用户可直接照抄给 AI 的澄清话术，口语化、不带技术术语\"}"
        )
        extra_block = (
            f"\n\n【关键线索 · AI 多保留的行在源表中的完整数据（请重点检查这些行是否存在异常，"
            f"如某指标不合常理、数值矛盾等）】\n{extra_src_detail}"
            if extra_src_detail else ""
        )
        user_prompt = (
            f"【源表样本】\n{_format_source_dfs_for_prompt({k: v.head(sample_rows) for k, v in source_dfs.items()})}\n\n"
            f"【期望的目标表样本】\n{target_block}\n\n"
            f"【AI 生成的错误结果样本】\n{result_block}\n\n"
            f"【程序自动比对出的真实差异】\n{diff_summary}{extra_block}\n\n"
            f"【用户已给过的澄清】\n{clarification_history or '（无）'}\n\n"
            f"参考下面的诊断示例风格：\n{_DIAGNOSE_FEWSHOT}\n\n"
            "现在请输出这次的 JSON。"
        )
        resp = client.chat.completions.create(
            model=model, temperature=0.2,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return fallback
        data = json.loads(m.group(0))
        return {
            "diagnosis": str(data.get("diagnosis", "")).strip(),
            "suggested_clarification": str(data.get("suggested_clarification", "")).strip(),
        }
    except Exception:
        return fallback


# -----------------------------------------------------------------------------
# 安全执行生成代码
# -----------------------------------------------------------------------------
def _attribute_parts(node: ast.AST) -> list[str]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return list(reversed(parts))


def validate_generated_code(code: str) -> None:
    """Reject generated code that can escape in-memory deterministic transforms."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"生成代码语法无效：{exc.msg}") from exc

    process_defs = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "process_data"
    ]
    if len(process_defs) != 1 or isinstance(process_defs[0], ast.AsyncFunctionDef):
        raise ValueError("生成代码必须且只能定义一个同步 process_data 函数。")

    for node in ast.walk(tree):
        if isinstance(node, (ast.While, ast.AsyncFor, ast.AsyncWith, ast.Await, ast.Yield, ast.YieldFrom)):
            raise ValueError("生成代码包含不允许的循环或异步控制结构。")
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in _ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"生成代码导入了不允许的模块：{root}")
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if node.level or root not in _ALLOWED_IMPORT_ROOTS:
                raise ValueError(f"生成代码导入了不允许的模块：{root or 'relative import'}")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise ValueError(f"生成代码使用了不允许的能力：{node.id}")
        if isinstance(node, ast.Attribute):
            parts = _attribute_parts(node)
            if any(part.startswith("__") for part in parts):
                raise ValueError("生成代码不能访问 Python 内部属性。")
            forbidden = next((part for part in parts if part in _FORBIDDEN_ATTRIBUTE_PARTS), None)
            if forbidden:
                raise ValueError(f"生成代码使用了不允许的文件、网络或进程能力：{forbidden}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            parts = _attribute_parts(node.func)
            if parts in (["time", "time"], ["datetime", "now"], ["datetime", "today"], ["datetime", "utcnow"]):
                raise ValueError("生成代码不能读取非确定性时间。")
            if parts == ["time", "strftime"] and len(node.args) < 2 and not any(
                keyword.arg in {"t", "tuple"} for keyword in node.keywords
            ):
                raise ValueError("time.strftime 必须提供显式时间值。")


def _safe_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
    root = name.split(".", 1)[0]
    if level or root not in _ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"不允许导入模块：{name}")
    return builtins.__import__(name, globals, locals, fromlist, level)


def _safe_builtins() -> dict[str, Any]:
    allowed = {
        "Exception", "IndexError", "KeyError", "RuntimeError", "TypeError", "ValueError",
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
        "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next",
        "object", "range", "reversed", "round", "set", "slice", "sorted", "str", "sum",
        "tuple", "zip",
    }
    safe = {name: getattr(builtins, name) for name in allowed}
    safe["__import__"] = _safe_import
    return safe


def _strip_unused_nonallowed_imports(code: str) -> str:
    """Compatibility hook used before validation; safe imports remain unchanged."""
    validate_generated_code(code)
    return code


def run_process_data(
    code: str, source_dfs: dict[str, pd.DataFrame]
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """
    在隔离 globals 中执行生成代码并调用 process_data。
    返回单张 DataFrame，或多工作表场景下的 {工作表名: DataFrame}。
    """
    validate_generated_code(code)
    copy_dfs = {k: v.copy() for k, v in source_dfs.items()}
    exec_globals: dict[str, Any] = {
        "np": np,
        "pd": pd,
        "__builtins__": _safe_builtins(),
    }
    exec(code, exec_globals)  # noqa: S102
    if "process_data" not in exec_globals:
        raise RuntimeError("生成代码中未找到函数 process_data")
    result = exec_globals["process_data"](copy_dfs)
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, dict) and result and all(isinstance(v, pd.DataFrame) for v in result.values()):
        return {str(k): v for k, v in result.items()}
    raise TypeError(
        f"process_data 应返回 DataFrame 或 {{工作表名: DataFrame}}，实际 {type(result)}"
    )


# -----------------------------------------------------------------------------
# 核心：生成 + 校验（一轮澄清内最多 N 次模型自修正）
# -----------------------------------------------------------------------------
def generate_and_test_logic(
    source_dfs: dict[str, pd.DataFrame],
    target_df: pd.DataFrame | dict[str, pd.DataFrame],
    api_key: str,
    formula_clues: str = DEFAULT_FORMULA_CLUES,
    formula_hints: str = "",
    clarification_context: str = "（尚无用户澄清）",
    mismatch_context: str = "（无）",
    max_retries: int = MAX_LLM_SELF_RETRIES,
    sample_rows: int = SAMPLE_ROWS,
    base_url: str = DEEPSEEK_BASE_URL,
    model: str = DEEPSEEK_MODEL,
) -> tuple[str | None, str | None, pd.DataFrame | None]:
    """
    返回 (成功代码 or None, 失败时的差异/错误摘要 or None, 最后一次跑出的结果表 or None)。
    不写盘、不打印。
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("缺少有效的 DeepSeek API Key。")
    client = OpenAI(api_key=key, base_url=base_url, timeout=REQUEST_TIMEOUT)

    source_sample = {k: v.head(sample_rows) for k, v in source_dfs.items()}
    clar = (clarification_context or "").strip() or "（无）"
    mismatch = (mismatch_context or "").strip() or "（无）"
    fhints = (formula_hints or "").strip() or "（目标表不包含公式，请根据数据样本推断处理逻辑。）"
    error_feedback = "（本轮尚无执行错误，请根据样本、线索与澄清生成。）"
    last_result: pd.DataFrame | dict[str, pd.DataFrame] | None = None

    for attempt in range(1, max_retries + 1):
        attempt_result: pd.DataFrame | dict[str, pd.DataFrame] | None = None
        user_content = USER_PROMPT_TEMPLATE.format(
            source_data=_format_source_dfs_for_prompt(source_sample, sample_rows),
            target_data=_format_target_for_prompt(target_df, sample_rows),
            formula_hints=fhints,
            formula_clues=formula_clues,
            clarification_context=clar,
            mismatch_context=mismatch,
            error_feedback=error_feedback,
        )
        completion = client.chat.completions.create(
            model=model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        raw = completion.choices[0].message.content or ""
        generated_code = _extract_python_code(raw)

        try:
            result_obj = run_process_data(generated_code, source_dfs)
            attempt_result = result_obj
            last_result = result_obj
            assert_results_match(result_obj, target_df)
        except Exception as e:  # noqa: BLE001
            diff = ""
            if attempt_result is not None:
                try:
                    diff = describe_results_mismatch(attempt_result, target_df)
                except Exception:  # noqa: BLE001
                    diff = ""
            error_feedback = (
                f"第 {attempt} 次模型自修正失败。{type(e).__name__}: {e}\n"
                + (f"\n与目标表差异摘要：\n{diff}\n" if diff else "")
                + "\n请修正代码，只输出完整 Python。"
            )
            if attempt == max_retries:
                final_msg = diff or (
                    describe_results_mismatch(attempt_result, target_df)
                    if attempt_result is not None
                    else str(e)
                )
                return None, final_msg or str(e), last_result
            continue

        return generated_code, None, last_result

    return None, "未知错误：未命中返回", last_result


# -----------------------------------------------------------------------------
# 结构签名
# -----------------------------------------------------------------------------
def build_learn_profile(source_dfs: dict[str, pd.DataFrame]) -> list[dict]:
    """从学习用源表构建结构签名，供后续键名对齐使用。"""
    profile = []
    for key, df in source_dfs.items():
        sheet = key.rsplit("_", 1)[-1] if "_" in key else key
        profile.append({"key": key, "sheet": sheet, "columns": list(df.columns)})
    return profile


def align_keys(
    new_source_dfs: dict[str, pd.DataFrame],
    learn_profile: list[dict],
) -> dict[str, pd.DataFrame]:
    """
    将新表的键重映射为学习时的键，使 process_data 代码可直接执行。
    按 sheet 名 + 列名集合模糊匹配；匹配度 < 0.3 时保留原键。
    """
    import difflib as _dl

    def _cs(a: list, b: list) -> float:
        sa, sb = set(a), set(b)
        if not sa and not sb:
            return 1.0
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def _ss(x: str, y: str) -> float:
        return _dl.SequenceMatcher(None, x.lower(), y.lower()).ratio()

    aligned: dict[str, pd.DataFrame] = {}
    used: set[str] = set()

    if not new_source_dfs:
        return aligned

    for new_key, new_df in new_source_dfs.items():
        new_sheet = new_key.rsplit("_", 1)[-1] if "_" in new_key else new_key
        new_cols = list(new_df.columns)
        best_score, best_learn_key = -1.0, None
        for pe in learn_profile:
            if pe["key"] in used:
                continue
            score = 0.3 * _ss(new_sheet, pe["sheet"]) + 0.7 * _cs(new_cols, pe["columns"])
            if score > best_score:
                best_score, best_learn_key = score, pe["key"]
        if best_learn_key and best_score >= 0.3:
            aligned[best_learn_key] = new_df
            used.add(best_learn_key)
        else:
            aligned[new_key] = new_df

    return aligned


# -----------------------------------------------------------------------------
# LLM 生成逻辑名称
# -----------------------------------------------------------------------------
def generate_logic_name(
    source_dfs: dict[str, pd.DataFrame],
    target_df: pd.DataFrame,
    api_key: str,
    base_url: str = DEEPSEEK_BASE_URL,
    model: str = DEEPSEEK_MODEL,
) -> str:
    """让 LLM 根据表结构生成一句话描述（≤15字），失败时返回降级名称。"""
    try:
        client = OpenAI(api_key=api_key.strip(), base_url=base_url, timeout=REQUEST_TIMEOUT)
        src_info = "\n".join(f"- {k}：列名={list(df.columns)}" for k, df in source_dfs.items())
        t_sheets = as_sheet_dict(target_df)
        if _SINGLE in t_sheets:
            out_info = f"输出列名：{list(t_sheets[_SINGLE].columns)}"
        else:
            out_info = "输出多个工作表：\n" + "\n".join(
                f"  · 「{n}」列名={list(df.columns)}" for n, df in t_sheets.items()
            )
        prompt = (
            f"根据以下表格信息，生成一个简短的中文描述（不超过15个字），"
            f"说明这个处理逻辑的作用。只输出描述文字，不要标点或解释。\n\n"
            f"源表：\n{src_info}\n\n"
            f"{out_info}"
        )
        resp = client.chat.completions.create(
            model=model, temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
        )
        name = (resp.choices[0].message.content or "").strip()[:20]
        return name or _fallback_name(target_df)
    except Exception:  # noqa: BLE001
        return _fallback_name(target_df)


def _fallback_name(target_df: Any) -> str:
    t_sheets = as_sheet_dict(target_df)
    if _SINGLE not in t_sheets:
        return "生成" + "、".join(list(t_sheets.keys())[:2]) + "等多表"
    cols = "、".join(list(t_sheets[_SINGLE].columns)[:2])
    return f"生成{cols}等"


# -----------------------------------------------------------------------------
# 阶段 2：执行 + 失败自愈
# -----------------------------------------------------------------------------
def phase2_process_with_healing(
    locked_code: str,
    new_source_dfs: dict[str, pd.DataFrame],
    learn_sources: dict[str, pd.DataFrame],
    learn_target: pd.DataFrame | dict[str, pd.DataFrame],
    learn_profile: list[dict],
    api_key: str,
    base_url: str = DEEPSEEK_BASE_URL,
    model: str = DEEPSEEK_MODEL,
) -> tuple[pd.DataFrame | dict[str, pd.DataFrame] | None, str | None, str | None]:
    """
    执行锁定逻辑；失败时自愈（最多 3 次）。
    返回 (result_df, healed_code_or_None, error_msg_or_None)。
    healed_code 不为 None 表示代码已更新，调用方应写回存储。
    """
    aligned = align_keys(new_source_dfs, learn_profile)

    # 先直接跑
    try:
        return run_process_data(locked_code, aligned), None, None
    except Exception as e:
        heal_ctx = f"执行报错：{type(e).__name__}: {e}"

    # 自愈循环
    client = OpenAI(api_key=api_key.strip(), base_url=base_url)
    current_code = locked_code

    for attempt in range(1, MAX_LLM_SELF_RETRIES + 1):
        struct_info = "\n".join(
            f"  {k}: 列名={list(df.columns)}, 行数={len(df)}" for k, df in aligned.items()
        )
        user_content = USER_PROMPT_TEMPLATE.format(
            source_data=_format_source_dfs_for_prompt(
                {k: v.head(SAMPLE_ROWS) for k, v in learn_sources.items()}
            ),
            target_data=_format_df_for_prompt(learn_target.head(SAMPLE_ROWS)),
            formula_hints="（阶段2自愈，无额外公式信息）",
            formula_clues=DEFAULT_FORMULA_CLUES,
            clarification_context="（无）",
            mismatch_context="（无）",
            error_feedback=(
                f"【阶段2自愈·第{attempt}次】{heal_ctx}\n"
                f"新表结构：\n{struct_info}\n"
                "请修正 process_data，只输出完整 Python 代码。"
            ),
        )
        try:
            resp = client.chat.completions.create(
                model=model, temperature=0.1,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            new_code = _extract_python_code(resp.choices[0].message.content or "")

            # 先用学习集回测（多工作表感知）
            learn_aligned = align_keys(learn_sources, learn_profile)
            bt_result = run_process_data(new_code, learn_aligned)
            assert_results_match(bt_result, learn_target)
            # 回测通过，再跑新表
            result = run_process_data(new_code, aligned)
            return result, new_code, None

        except Exception as e:  # noqa: BLE001
            heal_ctx = f"第{attempt}次自愈失败：{type(e).__name__}: {e}"
            current_code = new_code if "new_code" in dir() else current_code  # type: ignore[possibly-undefined]

    return None, None, heal_ctx
