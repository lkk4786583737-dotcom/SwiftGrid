"""
表速通 · 界面层 (app.py)  v2.0
多逻辑缓存版：本地持久化，无需改文件名，失败自愈。
运行：streamlit run app.py
"""
from __future__ import annotations

import io
import threading
import time

import pandas as pd
import streamlit as st

import demo_logic
import engine
import storage

try:
    import plotly.express as px
    _PLOTLY = True
except Exception:  # noqa: BLE001
    _PLOTLY = False

st.set_page_config(page_title="表速通 Biaosutong", page_icon="📊", layout="wide")

HIGH_CONF = storage.HIGH_CONF
LOW_CONF  = storage.LOW_CONF

# ─────────────────────────────────────────────────────────────────────────────
# 会话状态
# ─────────────────────────────────────────────────────────────────────────────
def _init_state():
    defaults: dict = {
        "mode": "learn",           # "learn" | "run"
        "task_counter": 0,         # 递增以重置文件上传器
        # ── 学习阶段 ──
        "pending_code": None,
        "preview_result": None,
        "learn_sources": None,
        "learn_target": None,
        "learn_profile": None,
        "clarifications_p1": [],
        "last_mismatch": None,
        "last_mismatch_info": None,  # classify_frame_mismatch 返回的结构化诊断
        "show_clue_input": False,  # 仅 P1 自修正全失败后才显示
        "trigger_learn": False,    # 澄清后就地重跑学习（不需重新上传）
        "formula_hints_cache": "", # 缓存公式线索，澄清重跑时复用
        "last_diagnosis": "",      # LLM 诊断：为什么错（人话）
        "suggested_clarification": "",  # LLM 起草的澄清话术（预填输入框）
        "welcomed": False,         # 新用户欢迎弹窗已展示过
        "pending_name": None,      # LLM 生成的逻辑名（待确认）
        # ── 配置（API / 企业私有部署）──
        "api_key": "",             # DeepSeek / 企业模型 Key
        "ent_base_url": "",        # 企业内网模型地址（空=用默认 DeepSeek）
        "ent_model": "",           # 企业模型名称（空=用默认）
        # ── 首页一键体验演示 ──
        "demo_result": None,       # 演示处理结果 DataFrame
        "demo_new_source": None,   # 演示新源表 DataFrame
        # ── 执行阶段 ──
        "run_sources": None,       # 新表 DFs
        "run_best_meta": None,     # 最高分逻辑元数据
        "run_confidence": 0.0,
        "run_all_scored": [],      # 全部缓存含得分
        "run_matched_id": None,    # 用户确认使用的逻辑 ID
        "run_result": None,
        "run_error": None,
        "run_healed": False,       # P2 自愈是否更新了逻辑
        # ── 侧边栏重命名 ──
        "renaming_id": None,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


_init_state()

tc = st.session_state["task_counter"]   # 简写，用于文件上传器 key


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────
def _read_uploads(files) -> list[tuple[str, io.BytesIO]]:
    return [(f.name, io.BytesIO(f.getvalue())) for f in files]


# ── 万能输入：上传文件 或 直接粘贴表格 ──────────────────────────────────────────
PASTE_ROW_HINT = 100   # 粘贴行数超过此值，提示改用上传文件


def _parse_pasted_table(text: str):
    """解析粘贴的表格文本（Tab/逗号/多空格分隔）。返回 (DataFrame|None, error|None)。"""
    raw = (text or "").strip("\n")
    if not raw.strip():
        return None, None
    first = raw.splitlines()[0]
    if "\t" in first:
        sep = "\t"
    elif "," in first:
        sep = ","
    else:
        sep = r"\s{2,}"
    try:
        df = pd.read_csv(io.StringIO(raw), sep=sep, engine="python")
        df.columns = [str(c).strip() for c in df.columns]
        if df.shape[0] < 1 or df.shape[1] < 1:
            return None, "没解析出有效的行列，请确认粘贴的是表格内容。"
        return df, None
    except Exception as e:  # noqa: BLE001
        return None, f"解析失败：{e}"


def _render_file_preview(files):
    """上传文件预览（每个文件每个 sheet 前 8 行），帮用户确认没传错表。"""
    items = files if isinstance(files, list) else [files]
    for f in items:
        if f is None:
            continue
        try:
            xl = pd.ExcelFile(io.BytesIO(f.getvalue()), engine="openpyxl")
            for s in xl.sheet_names:
                df = xl.parse(s)
                st.caption(f"📄 {f.name} · 工作表「{s}」　{df.shape[0]} 行 × {df.shape[1]} 列")
                st.dataframe(df.head(8), use_container_width=True, height=200)
        except Exception as e:  # noqa: BLE001
            st.warning(f"{f.name} 预览失败：{e}")


def _render_paste_preview(text: str):
    """粘贴解析结果预览 + 行数过多提示。"""
    df, err = _parse_pasted_table(text)
    if err:
        st.error(f"⚠ {err}")
        return
    if df is None:
        return
    st.caption(f"✅ 识别到 {df.shape[0]} 行 × {df.shape[1]} 列")
    st.dataframe(df.head(8), use_container_width=True, height=200)
    if df.shape[0] > PASTE_ROW_HINT:
        st.info(
            f"粘贴的数据较多（{df.shape[0]} 行）。数据量大时建议改用「上传文件」，"
            "能更好地保留数据类型与公式，处理也更稳。",
            icon="💡",
        )


def _render_input_slot_preview(files, paste_text):
    """输入位的统一预览区：文件优先，其次粘贴，都没有给占位提示。"""
    with st.container(border=True):
        if files:
            _render_file_preview(files)
        elif paste_text and paste_text.strip():
            _render_paste_preview(paste_text)
        else:
            st.caption("上传文件或粘贴表格后，这里会显示预览，方便你确认没弄错表。")


def _resolve_source_input(files, paste_text):
    """把源表输入位解析成 source_dfs；返回 (dict|None, 是否来自文件)。"""
    if files:
        return engine.load_dfs_from_uploaded(_read_uploads(files)), True
    if paste_text and paste_text.strip():
        df, err = _parse_pasted_table(paste_text)
        if df is None:
            raise ValueError(err or "粘贴内容无法解析")
        return {"粘贴表_数据": df}, False
    return None, False


def _resolve_target_input(file, paste_text):
    """把目标输入位解析成 target；返回 (DataFrame|dict|None, 文件对象|None)。"""
    if file:
        return engine.load_target(file.name, io.BytesIO(file.getvalue())), file
    if paste_text and paste_text.strip():
        df, err = _parse_pasted_table(paste_text)
        if df is None:
            raise ValueError(err or "粘贴内容无法解析")
        return df, None
    return None, None


# ── 图表（Plotly）──────────────────────────────────────────────────────────────
_CHART_NAMES = {"line": "折线图", "bar": "柱状图", "pie": "饼图",
                "scatter": "散点图", "hist": "直方图"}


def _looks_like_time(col, s) -> bool:
    name = str(col)
    if any(k in name for k in ["日期", "时间", "年月", "月份", "date", "time", "周"]):
        return True
    try:
        parsed = pd.to_datetime(s.dropna().astype(str).head(20), errors="coerce")
        return len(parsed) > 0 and parsed.notna().mean() > 0.7
    except Exception:  # noqa: BLE001
        return False


def _column_roles(df: pd.DataFrame):
    time_cols, cat_cols, num_cols = [], [], []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            num_cols.append(c)
        elif _looks_like_time(c, s):
            time_cols.append(c)
        else:
            cat_cols.append(c)
    return time_cols, cat_cols, num_cols


def _suggest_charts(time_cols, cat_cols, num_cols):
    sug = []
    if time_cols and num_cols:
        sug.append({"type": "line", "x": time_cols[0], "y": num_cols[0],
                    "label": f"📈 折线趋势：{num_cols[0]} 随 {time_cols[0]} 变化"})
    if cat_cols and num_cols:
        sug.append({"type": "bar", "x": cat_cols[0], "y": num_cols[0],
                    "label": f"📊 柱状对比：各「{cat_cols[0]}」的 {num_cols[0]}"})
        sug.append({"type": "pie", "x": cat_cols[0], "y": num_cols[0],
                    "label": f"🥧 占比饼图：{num_cols[0]} 按「{cat_cols[0]}」分布"})
    if len(num_cols) >= 2:
        sug.append({"type": "scatter", "x": num_cols[0], "y": num_cols[1],
                    "label": f"✸ 散点关系：{num_cols[0]} vs {num_cols[1]}"})
    if num_cols and not sug:
        sug.append({"type": "hist", "x": num_cols[0], "y": None,
                    "label": f"▮ 分布直方图：{num_cols[0]}"})
    return sug


def _build_chart(df, ctype, x, y):
    if ctype == "line":
        d = df.sort_values(x) if x in df.columns else df
        return px.line(d, x=x, y=y, markers=True)
    if ctype == "bar":
        return px.bar(df, x=x, y=y)
    if ctype == "pie":
        return px.pie(df, names=x, values=y)
    if ctype == "scatter":
        return px.scatter(df, x=x, y=y)
    return px.histogram(df, x=x)


def _render_chart_tab(df: pd.DataFrame, key_prefix: str):
    """基于数据结构推荐图表，用户可改图型与坐标轴，Plotly 渲染。"""
    if not _PLOTLY:
        st.info("未安装 plotly，无法绘图。请先 `pip install plotly`。")
        return
    if df is None or df.empty:
        st.info("没有可绘图的数据。")
        return
    time_cols, cat_cols, num_cols = _column_roles(df)
    if not num_cols:
        st.info("当前表没有数值列，暂不适合自动制图。")
        return

    sug = _suggest_charts(time_cols, cat_cols, num_cols)
    rec_labels = [s["label"] for s in sug]
    pick = st.selectbox("系统推荐（可改）", rec_labels, key=f"chartrec_{key_prefix}")
    spec = sug[rec_labels.index(pick)]

    all_types = ["line", "bar", "pie", "scatter", "hist"]
    c0, c1, c2 = st.columns(3)
    ctype = c0.selectbox(
        "图表类型", all_types,
        index=all_types.index(spec["type"]),
        format_func=lambda t: _CHART_NAMES[t], key=f"ctype_{key_prefix}",
    )
    cols = list(df.columns)
    x_label = "分类轴" if ctype in ("bar", "pie") else "X 轴"
    x = c1.selectbox(
        x_label, cols,
        index=cols.index(spec["x"]) if spec["x"] in cols else 0,
        key=f"cx_{key_prefix}",
    )
    y = None
    if ctype != "hist":
        ydef = spec["y"] if spec["y"] in cols else (num_cols[0] if num_cols else cols[0])
        y = c2.selectbox(
            "数值轴" if ctype in ("bar", "pie") else "Y 轴", cols,
            index=cols.index(ydef), key=f"cy_{key_prefix}",
        )

    try:
        fig = _build_chart(df, ctype, x, y)
        fig.update_layout(
            margin=dict(l=10, r=10, t=30, b=10),
            colorway=["#06B6A4", "#0EA5E9", "#F59E0B", "#EF4444", "#8B5CF6", "#10B981"],
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:  # noqa: BLE001
        st.warning(f"这个图型/坐标轴组合画不出来（{e}），换一组试试。")


def _df_to_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False)
    return buf.getvalue()


def _result_to_xlsx(result) -> bytes:
    """导出结果为 Excel：单表写一个 sheet；多表（dict）写多个工作表。"""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        if isinstance(result, dict):
            for name, df in result.items():
                df.to_excel(w, index=False, sheet_name=str(name)[:31])
        else:
            result.to_excel(w, index=False)
    return buf.getvalue()


def _is_multi(obj) -> bool:
    """结果/目标是否为多工作表（dict）形态。"""
    return isinstance(obj, dict)


def _result_shape_text(result) -> str:
    """结果概况文本，单表与多表通用。"""
    if isinstance(result, dict):
        parts = "；".join(f"「{n}」{df.shape[0]}×{df.shape[1]}" for n, df in result.items())
        return f"{len(result)} 张表（{parts}）"
    return f"{result.shape[0]} 行 × {result.shape[1]} 列"


def _render_preview(result, height: int = 360, key_prefix: str = "pv"):
    """渲染结果预览：单表直接展示；多表用子标签页逐 sheet 展示。"""
    if isinstance(result, dict):
        names = list(result.keys())
        for tab, n in zip(st.tabs([f"📄 {n}" for n in names]), names):
            with tab:
                df = result[n]
                st.caption(f"{df.shape[0]} 行 × {df.shape[1]} 列")
                st.dataframe(df, use_container_width=True, height=height)
    else:
        st.dataframe(result, use_container_width=True, height=height)


def _reset_learn():
    for k in ("pending_code", "preview_result", "learn_sources", "learn_target",
              "learn_profile", "last_mismatch", "last_mismatch_info",
              "pending_name", "show_clue_input"):
        st.session_state[k] = [] if k == "clarifications_p1" else None
    st.session_state["clarifications_p1"] = []
    st.session_state["show_clue_input"] = False
    st.session_state["trigger_learn"] = False
    st.session_state["formula_hints_cache"] = ""
    st.session_state["last_diagnosis"] = ""
    st.session_state["suggested_clarification"] = ""


def _render_mismatch_guidance(info: dict, used: int):
    """根据诊断类型渲染结构化引导提示（先定位问题，再教用户澄清）。"""
    attempt = used + 1
    cat = info.get("category", "value_error")

    def _cols(lst, n=4):
        return "、".join(f"「{c}」" for c in lst[:n])

    # ── 先「定位问题」：确定性事实差异 + LLM 诊断（为什么错）──
    # 数据量大时用户自己根本察觉不到哪错了，先指出来并解释，再问怎么改。
    examples = (info.get("row_examples", []) or []) + (info.get("value_examples", []) or [])
    diagnosis = st.session_state.get("last_diagnosis", "")
    if examples or diagnosis:
        with st.container(border=True):
            if diagnosis:
                st.markdown(f"**🩺 AI 诊断**　{diagnosis}")
            if examples:
                st.markdown("**🔎 对不上的具体位置：**")
                for ex in examples:
                    st.markdown(f"- {ex}")
            if not diagnosis:
                st.caption(
                    "对照这些行/格，想想是哪条规则 AI 没理解对"
                    "（比如某类异常数据该被过滤、某列的计算口径不同），再按下方提示补充说明。"
                )

    if cat == "precision_only":
        st.warning(f"第 {attempt} 次尝试 · AI 的逻辑方向正确，只是数值小数位不一致")
        st.markdown(
            f"**问题**：{_cols(info['precision_cols'])} 的计算结果精度和目标表略有偏差\n\n"
            "**只需告诉 AI 一件事 →**\n"
            "> 这些数值列需要保留几位小数？有没有特殊取整规则？\n\n"
            "*例：「所有百分比保留两位小数」或「金额取整不保留小数」*"
        )

    elif cat == "missing_cols":
        miss = _cols(info["missing_cols"])
        extra = _cols(info["extra_cols"])
        st.warning(f"第 {attempt} 次尝试 · AI 生成的列与目标表不一致")
        body = f"**问题**：目标表需要的 {miss} 在 AI 结果里没有生成"
        if extra:
            body += f"；多生成了 {extra}（目标表不需要）"
        st.markdown(body + "\n\n**请确认以下几点 →**\n"
                    f"> 1. {miss} 的数据来源是什么？是从哪个字段计算，还是需要关联另一张表？\n"
                    "> 2. 上传的源表和目标样例文件有没有传反？\n\n"
                    "*例：「毛利率 = (销售额 - 成本) ÷ 销售额 × 100，结果保留两位小数」*")

    elif cat == "row_count":
        diff = info["row_count_diff"]
        if info["row_direction"] == "more":
            st.warning(f"第 {attempt} 次尝试 · AI 生成了多余的行（多了 {diff} 行）")
            st.markdown(
                "**请告诉 AI →**\n"
                "> 目标表是不是对源表做了**分组汇总**（多行合并成一行）？\n"
                "> · 如果是：按哪个字段分组？数量/金额列怎么合并（求和/平均/最大值）？\n"
                "> · 如果是筛选：删掉了哪些类型的行？条件是什么？"
            )
        else:
            st.warning(f"第 {attempt} 次尝试 · AI 生成的行数不够（少了 {diff} 行）")
            st.markdown(
                "**请告诉 AI →**\n"
                "> AI 可能误以为要汇总（合并行），但实际上应该保留每行明细？\n"
                "> 目标表里保留的是哪些行？有没有过滤条件？"
            )

    elif cat == "value_error":
        cols_str = _cols(info["bad_value_cols"])
        st.warning(f"第 {attempt} 次尝试 · AI 生成的数值与目标表不一致")
        st.markdown(
            f"**问题**：{cols_str} 的计算结果与目标表不符\n\n"
            "**请确认以下几点 →**\n"
            f"> 1. {cols_str} 是怎么算出来的？有没有特殊的判断条件或区间划分？\n"
            "> 2. 目标样例是对应这批源数据的结果吗？请确认文件配对正确\n\n"
            "*例：「客户等级：消费 ≥10000 为 VIP，否则普通」*"
        )

    elif cat == "mixed":
        parts = []
        if info["missing_cols"]:
            parts.append(f"缺少列：{_cols(info['missing_cols'])}")
        if info["extra_cols"]:
            parts.append(f"多余列：{_cols(info['extra_cols'])}")
        if info["row_count_diff"]:
            dir_str = "多了" if info["row_direction"] == "more" else "少了"
            parts.append(f"行数{dir_str} {info['row_count_diff']} 行")
        if info["bad_value_cols"]:
            parts.append(f"{_cols(info['bad_value_cols'])} 值有偏差")
        bullets = "\n".join(f"> · {p}" for p in parts)
        hints = []
        if info["missing_cols"]:
            hints.append(f"> 1. 「{info['missing_cols'][0]}」这列的数据怎么来的？")
        if info["row_count_diff"]:
            n = len(hints) + 1
            hints.append(f"> {n}. 行数差异：目标表是做了分组汇总还是筛选了某些行？")
        hints.append(f"> {len(hints)+1}. 请检查源表和目标样例文件有没有传反")
        st.warning(f"第 {attempt} 次尝试 · AI 生成结果在多处与目标表不一致")
        st.markdown(f"**问题摘要：**\n{bullets}\n\n**请优先说明 →**\n" + "\n".join(hints))

    else:  # crash / unknown
        st.warning(f"第 {attempt} 次尝试 · AI 运行时遇到错误")
        st.markdown(
            "**常见原因 →**\n"
            "> 1. 源表里的列名和目标样例里的列名不完全一致（注意空格、中英文标点）\n"
            "> 2. 上传的文件有没有传反？\n"
            "> 3. 表格是否含有合并单元格？\n\n"
            "请用大白话描述源表到目标表的处理步骤，帮助 AI 重新推断。"
        )


def _reset_run():
    for k in ("run_sources", "run_best_meta", "run_matched_id",
              "run_result", "run_error", "run_healed", "run_all_scored"):
        st.session_state[k] = [] if k == "run_all_scored" else None
    st.session_state["run_confidence"] = 0.0
    st.session_state["run_healed"] = False


def _switch_mode(mode: str):
    st.session_state["mode"] = mode
    _reset_run()
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# 配置 / 连接状态
# ─────────────────────────────────────────────────────────────────────────────
def _model_kwargs() -> dict:
    """根据企业配置返回传给 engine 的 base_url/model；空则用默认 DeepSeek。"""
    kw = {}
    if st.session_state["ent_base_url"].strip():
        kw["base_url"] = st.session_state["ent_base_url"].strip()
    if st.session_state["ent_model"].strip():
        kw["model"] = st.session_state["ent_model"].strip()
    return kw


def _conn_badge() -> str:
    """右上角连接状态徽章。"""
    if not st.session_state["api_key"].strip():
        return "⚪ 未配置"
    if st.session_state["ent_base_url"].strip():
        return "🏢 企业内网模型"
    return "🟢 DeepSeek 已连接"


@st.dialog("⚙️ 配置")
def _show_config():
    # 用独立控件 key，避免弹窗关闭后 Streamlit 回收控件值清空持久状态
    st.markdown("**模型 API Key**")
    cfg_key = st.text_input(
        "key", value=st.session_state["api_key"], type="password",
        label_visibility="collapsed", placeholder="粘贴 API Key（sk-...）",
        key="cfg_api_key",
    )
    st.caption("Key 仅存于本次会话内存，关闭页面即清除，不落盘、不上传。")

    st.markdown("---")
    with st.expander("🏢 企业私有部署（可选）", expanded=bool(st.session_state["ent_base_url"])):
        cfg_base = st.text_input(
            "模型地址 base_url", value=st.session_state["ent_base_url"],
            placeholder="https://企业内网地址/v1（OpenAI 兼容）", key="cfg_base_url",
        )
        cfg_model = st.text_input(
            "模型名称 model", value=st.session_state["ent_model"],
            placeholder="如 qwen2.5-72b、deepseek-v4 等", key="cfg_model",
        )
        st.info(
            "填写后，逻辑推断将走企业内网模型；处理新表本就在本地执行，"
            "**真实数据全程不出私有环境**。",
            icon="🔒",
        )

    if st.button("保存配置", type="primary", use_container_width=True):
        st.session_state["api_key"]      = cfg_key.strip()
        st.session_state["ent_base_url"] = cfg_base.strip()
        st.session_state["ent_model"]    = cfg_model.strip()
        st.rerun()


def _diff_columns(before: pd.DataFrame, after: pd.DataFrame):
    """返回 (新增列, 移除列, 保留列)。"""
    bset, aset = list(before.columns), list(after.columns)
    added   = [c for c in aset if c not in bset]
    removed = [c for c in bset if c not in aset]
    kept    = [c for c in aset if c in bset]
    return added, removed, kept


def _render_diff(before: pd.DataFrame, after: pd.DataFrame):
    """可视化「源表 → 结果」的变化：列级 diff + 结果表新列高亮。"""
    added, removed, kept = _diff_columns(before, after)

    m1, m2, m3 = st.columns(3)
    m1.metric("➕ 新增列", len(added))
    m2.metric("➖ 移除列", len(removed))
    m3.metric("行数变化", f"{len(after) - len(before):+d}")

    if added:
        st.markdown("**➕ AI 新生成的列：** " + "、".join(f"`{c}`" for c in added))
    if removed:
        st.markdown("**➖ 处理后移除的列：** " + "、".join(f"`{c}`" for c in removed))

    st.caption("结果表（绿色 = AI 新生成的列）")
    try:
        sty = after.style.apply(
            lambda col: [
                "background-color: #D1FAE5" if col.name in added else "" for _ in col
            ],
            axis=0,
        )
        st.dataframe(sty, use_container_width=True, height=360)
    except Exception:
        st.dataframe(after, use_container_width=True, height=360)


def _render_result_tabs(before: pd.DataFrame | None, after,
                        log_lines: list[str], key_prefix: str = ""):
    """右侧可视化区：数据预览 / 转换日志 / Diff 对比 三标签页 + 导出。支持多工作表结果。"""
    multi = _is_multi(after)
    t1, t2, t3, t4 = st.tabs(["📑 数据预览", "🧾 转换日志", "🔍 Diff 对比", "📊 图表"])
    with t1:
        _render_preview(after, height=380, key_prefix=key_prefix)
    with t2:
        for line in log_lines:
            st.markdown(line)
    with t3:
        if multi:
            st.caption("多工作表结果，各表的列结构如下：")
            for n, df in after.items():
                st.markdown(f"**📄 {n}**　{df.shape[0]} 行 × {df.shape[1]} 列")
                st.markdown("列：" + "、".join(f"`{c}`" for c in df.columns))
        elif before is not None:
            _render_diff(before, after)
        else:
            st.info("本次没有可对比的源表。")
    with t4:
        if multi:
            names = list(after.keys())
            sheet = st.selectbox("选择要制图的工作表", names, key=f"chart_sheet_{key_prefix}")
            _render_chart_tab(after[sheet], f"{key_prefix}_{sheet}")
        else:
            _render_chart_tab(after, key_prefix)

    st.download_button(
        "📥 导出处理后的 Excel" + ("（多工作表）" if multi else ""),
        data=_result_to_xlsx(after),
        file_name="表速通_处理结果.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", use_container_width=True, key=f"dl_{key_prefix}",
    )


def _render_empty_state():
    """无 Key 时的全宽落地页：价值主张 + 一键体验演示（不需要 Key）。"""
    st.markdown(
        "<div style='text-align:center;padding:8px 0 4px;'>"
        "<h2 style='margin-bottom:4px;'>教一次，反复用</h2>"
        "<p style='color:#4B6358;font-size:17px;'>"
        "上传一次「做好的样例」，AI 学会你的表格处理逻辑；之后新表丢进来，秒出结果。"
        "</p></div>",
        unsafe_allow_html=True,
    )

    if demo_logic.is_available() and st.session_state["demo_result"] is None:
        with st.container(border=True):
            st.markdown(f"#### ⚡ 一键体验：{demo_logic.DEMO_TITLE}")
            st.markdown(demo_logic.DEMO_STORY)
            st.caption("无需填 Key、无需准备文件 —— 点一下，直接看到「源表 → 结果 → Diff」")
            if st.button("⚡ 运行演示案例", type="primary", use_container_width=True):
                try:
                    new_src = demo_logic.load_demo_new_source()
                    result = engine.run_process_data(
                        demo_logic.LOCKED_CODE, demo_logic.demo_source_dfs()
                    )
                    st.session_state["demo_new_source"] = new_src
                    st.session_state["demo_result"] = result
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"演示运行失败：{e}")

    # 演示结果展示
    if st.session_state["demo_result"] is not None:
        st.success("✅ 演示完成！这正是「处理新表」的真实效果——全程本地运行，不调用大模型。")
        before = st.session_state["demo_new_source"]
        after  = st.session_state["demo_result"]
        log = [
            f"- 读取新源表：{before.shape[0]} 行 × {before.shape[1]} 列",
            "- 命中已学逻辑「用户分层」，本地执行 pandas 处理",
            "- 计算客单价（ROUND 2 位）",
            "- 按消费额划分会员等级（钻石/黄金/白银/普通）",
            "- 按登录天数判定活跃状态（活跃/一般/沉睡）",
            "- 复合条件标记重点维护用户",
            f"- 输出结果：{after.shape[0]} 行 × {after.shape[1]} 列",
        ]
        _render_result_tabs(before, after, log, key_prefix="demo")
        if st.button("↺ 重置演示"):
            st.session_state["demo_result"] = None
            st.session_state["demo_new_source"] = None
            st.rerun()

    st.markdown("---")
    st.markdown(
        "**想处理自己的表格？** 点左侧 **⚙️ 配置** 填入 API Key 即可开始教 AI 学新逻辑。"
    )
    with st.expander("还没有 Key？点这里查看获取方式"):
        st.markdown(
            "1. 访问 [platform.deepseek.com](https://platform.deepseek.com) 注册账号\n"
            "2. 进入 **API Keys** 页面，点击「新建 API Key」\n"
            "3. 复制后点左侧 ⚙️ 配置 粘贴进去\n\n"
            "_企业用户可在配置卡片的「企业私有部署」里填内网模型地址，数据不出私有环境。_"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 侧边栏
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"**连接状态**　{_conn_badge()}")
    if st.button("⚙️ 配置 API / 企业部署", use_container_width=True):
        _show_config()
    api_key: str = st.session_state["api_key"]

    st.markdown("---")

    # 模式切换
    cache_count = storage.count()
    col_l, col_r = st.columns(2)
    if col_l.button(
        "教会新逻辑",
        type="primary" if st.session_state["mode"] == "learn" else "secondary",
        use_container_width=True,
    ):
        # 始终彻底重置并清空上传控件（递增 task_counter），回到干净的上传状态
        _reset_learn()
        _reset_run()
        st.session_state["mode"] = "learn"
        st.session_state["task_counter"] += 1
        st.rerun()
    if cache_count > 0:
        if col_r.button(
            "处理新表",
            type="primary" if st.session_state["mode"] == "run" else "secondary",
            use_container_width=True,
        ):
            _reset_run()
            st.session_state["mode"] = "run"
            st.session_state["task_counter"] += 1
            st.rerun()

    st.markdown("---")

    # 逻辑库
    st.subheader(f"📚 我的逻辑库 ({cache_count}/{storage.MAX_ENTRIES})")
    entries = storage.load_index()

    if not entries:
        st.caption("尚无保存的逻辑，先教会我一个吧。")
    else:
        for entry in entries:
            eid = entry["id"]
            with st.container():
                # 重命名模式
                if st.session_state["renaming_id"] == eid:
                    new_name = st.text_input(
                        "新名称", value=entry["name"],
                        key=f"rename_input_{eid}", label_visibility="collapsed",
                    )
                    c1, c2 = st.columns(2)
                    if c1.button("确认", key=f"rename_ok_{eid}", use_container_width=True):
                        storage.rename_entry(eid, new_name.strip() or entry["name"])
                        st.session_state["renaming_id"] = None
                        st.rerun()
                    if c2.button("取消", key=f"rename_cancel_{eid}", use_container_width=True):
                        st.session_state["renaming_id"] = None
                        st.rerun()
                else:
                    name_col, btn_col = st.columns([3, 1])
                    name_col.markdown(f"**{entry['name']}**")
                    name_col.caption(f"最近使用：{entry['last_used_at']}")
                    with btn_col:
                        if st.button("✏️", key=f"edit_{eid}", help="重命名"):
                            st.session_state["renaming_id"] = eid
                            st.rerun()
                        if st.button("🗑️", key=f"del_{eid}", help="删除"):
                            storage.delete_entry(eid)
                            if st.session_state.get("run_matched_id") == eid:
                                _reset_run()
                            st.rerun()
            st.markdown("---")

    # 高级选项
    with st.expander("▶ 高级选项（技术人员用）"):
        # 下载当前选中逻辑的脚本
        active_id = st.session_state.get("run_matched_id")
        if active_id:
            try:
                entry_data = storage.load_entry(active_id)
                st.download_button(
                    "⬇️ 下载处理脚本 (.py)",
                    data=entry_data["locked_code"].encode("utf-8"),
                    file_name="business_logic.py",
                    mime="text/x-python",
                )
            except Exception:
                pass
        else:
            st.caption("处理新表并确认逻辑后可在此下载脚本。")

        st.markdown("---")
        st.markdown("**导入已有脚本**")
        logic_up = st.file_uploader(
            "上传 business_logic.py", type=["py"], key=f"logic_up_{tc}",
        )
        name_for_import = st.text_input("给这个逻辑起个名字", key=f"import_name_{tc}",
                                        placeholder="例：月度销售汇总")
        if logic_up and name_for_import.strip() and st.button("导入此逻辑"):
            if storage.is_full():
                st.error("逻辑库已满（5/5），请先删除一个再导入。")
            else:
                code = logic_up.getvalue().decode("utf-8")
                dummy_src: dict[str, pd.DataFrame] = {}
                dummy_tgt = pd.DataFrame()
                dummy_profile: list[dict] = []
                storage.save_entry(
                    name=name_for_import.strip(),
                    locked_code=code,
                    learn_profile=dummy_profile,
                    learn_sources=dummy_src,
                    learn_target=dummy_tgt,
                )
                st.success("导入成功！")
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# 主标题 + 顶部状态徽章
# ─────────────────────────────────────────────────────────────────────────────
_t_left, _t_right = st.columns([7, 3])
with _t_left:
    st.title("📊 表速通 Biaosutong")
with _t_right:
    st.markdown(
        f"<div style='text-align:right;padding-top:24px;'>{_conn_badge()}</div>",
        unsafe_allow_html=True,
    )
st.caption("丢两张表就行：源表 + 目标样例，自动学会处理逻辑；之后新表一丢直接拿结果。")

# ── 无 API Key 时展示全宽空状态（含一键体验演示），阻止后续渲染 ──────────────
if not api_key:
    _render_empty_state()
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# 学习阶段
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state["mode"] == "learn":
    st.subheader("第一步 · 教会新的处理逻辑")

    st.caption("每个输入框都能 **上传 Excel** 或 **直接粘贴表格**（从 Excel/飞书复制）——谁方便用谁。")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**① 源数据表**")
        src_files = st.file_uploader(
            "上传 Excel（可多个）", type=["xlsx", "xls"],
            accept_multiple_files=True, key=f"learn_src_{tc}",
        )
        src_paste = st.text_area(
            "或直接粘贴表格", key=f"learn_src_paste_{tc}", height=90,
            placeholder="从 Excel/飞书复制整块表格，粘到这里…",
        )
        _render_input_slot_preview(src_files, src_paste)
    with c2:
        st.markdown("**② 目标表样例（做好的结果长什么样）**")
        tgt_file = st.file_uploader(
            "上传 Excel", type=["xlsx", "xls"], key=f"learn_tgt_{tc}",
        )
        tgt_paste = st.text_area(
            "或直接粘贴目标样例", key=f"learn_tgt_paste_{tc}", height=90,
            placeholder="粘贴你已经做好的结果表样例…",
        )
        _render_input_slot_preview(tgt_file, tgt_paste)

    src_ready = bool(src_files or src_paste.strip())
    tgt_ready = bool(tgt_file or tgt_paste.strip())
    can_learn = bool(src_ready and tgt_ready and api_key)
    learn_btn = st.button("🚀 开始学习", type="primary", disabled=not can_learn)

    # 触发学习：① 用户点「开始学习」（新上传）② 澄清后就地重跑（复用已存源表）
    fresh_learn = learn_btn and can_learn
    rerun_learn = st.session_state.get("trigger_learn", False)
    if fresh_learn or rerun_learn:
        st.session_state["trigger_learn"] = False

        if fresh_learn:
            # 解析输入（文件或粘贴），存入 session 供后续澄清重跑复用
            try:
                source_dfs, _src_from_file = _resolve_source_input(src_files, src_paste)
                target_df, _tgt_file_obj   = _resolve_target_input(tgt_file, tgt_paste)
                if source_dfs is None or target_df is None:
                    st.error("请提供源表和目标样例（上传文件或粘贴表格）。")
                    st.stop()
                st.session_state["learn_sources"] = source_dfs
                st.session_state["learn_target"]  = target_df
                st.session_state["learn_profile"] = engine.build_learn_profile(source_dfs)
            except Exception as e:  # noqa: BLE001
                st.error(f"读取/解析失败：{e}")
                st.stop()
            if _is_multi(target_df):
                st.info(
                    f"🗂 目标样例含 {len(target_df)} 个工作表（"
                    + "、".join(target_df.keys())
                    + "）— 将学习「一次产出多张结果表」的逻辑"
                )
            # 公式扫描仅对「上传的目标文件」有意义；粘贴模式直接跳过
            formula_hints = ""
            if _tgt_file_obj is None:
                st.caption("📋 粘贴模式：无公式可扫，直接推理逻辑。")
                st.session_state["formula_hints_cache"] = ""
            else:
                try:
                    formula_info = engine.extract_target_formulas(
                        io.BytesIO(_tgt_file_obj.getvalue())
                    )
                    formula_hints = formula_info["hint_text"]
                    fc = formula_info["formula_cells"]
                    fb = formula_info["broken_formulas"]
                    if fc > 0:
                        if fb == 0:
                            st.info(f"🔍 检测到 {fc} 个公式，全部可信 — 将优先参考公式辅助推理")
                        else:
                            st.warning(f"🔍 检测到 {fc} 个公式（{fb} 个可能错乱）— 将标注后辅助推理")
                except Exception:
                    formula_hints = ""
                st.session_state["formula_hints_cache"] = formula_hints
        else:
            # 澄清重跑：复用已存源表与公式线索，无需重新上传
            source_dfs = st.session_state["learn_sources"]
            target_df  = st.session_state["learn_target"]
            formula_hints = st.session_state.get("formula_hints_cache", "")
            if source_dfs is None or target_df is None:
                st.error("学习数据已丢失，请重新上传源表与目标样例。")
                st.stop()
            st.info("📝 已收到你的补充说明，正在结合上下文重新推断…")

        clar_ctx     = "\n".join(st.session_state["clarifications_p1"]) or "（尚无用户澄清）"
        mismatch_ctx = st.session_state["last_mismatch"] or "（无）"
        model_kw     = _model_kwargs()   # 主线程取，避免线程内访问 session

        _p1_bar = st.progress(0.0, text="AI 正在读取并分析表结构…")
        _p1_result: list = []
        _p1_error: list = []

        def _run_p1():
            try:
                _p1_result.append(engine.generate_and_test_logic(
                    source_dfs=source_dfs,
                    target_df=target_df,
                    api_key=api_key,
                    formula_clues=engine.DEFAULT_FORMULA_CLUES,
                    formula_hints=formula_hints,
                    clarification_context=clar_ctx,
                    mismatch_context=mismatch_ctx,
                    **model_kw,
                ))
            except Exception as _e:
                _p1_error.append(_e)

        _p1_thread = threading.Thread(target=_run_p1, daemon=True)
        _p1_thread.start()

        _p1_stages = [
            (0.00, "AI 正在读取并分析表结构…"),
            (0.15, "AI 正在推断处理逻辑…"),
            (0.50, "校验生成的逻辑是否与目标表一致…"),
            (0.80, "逻辑验证中，即将完成…"),
        ]
        _p1_elapsed = 0.0
        while _p1_thread.is_alive():
            _p1_elapsed += 0.25
            _pct = min(0.85, _p1_elapsed / 28.0)
            _label = _p1_stages[0][1]
            for _thresh, _msg in _p1_stages:
                if _pct >= _thresh:
                    _label = _msg
            if _p1_elapsed > 60:
                _label = "⏳ 已超过 1 分钟仍在反复校验，可能逻辑较复杂… 稍后可在下方补充说明帮 AI 更快对齐"
            _p1_bar.progress(_pct, text=_label)
            time.sleep(0.25)

        if not _p1_result:
            _p1_bar.empty()
            err_msg = str(_p1_error[0]) if _p1_error else "未知错误"
            st.error(f"AI 调用失败：{err_msg}")
            st.stop()

        code, mismatch, last_result = _p1_result[0]

        if code is not None:
            try:
                preview = engine.run_process_data(code, source_dfs)
            except Exception:  # noqa: BLE001
                preview = None
            _p1_bar.progress(0.90, text="生成逻辑描述名…")
            logic_name = engine.generate_logic_name(source_dfs, target_df, api_key, **model_kw)
            _p1_bar.progress(1.0, text="✅ 完成！")
            time.sleep(0.4)
            _p1_bar.empty()
            st.session_state["pending_code"]    = code
            st.session_state["preview_result"]  = preview
            st.session_state["pending_name"]    = logic_name
            st.session_state["show_clue_input"] = False
            st.rerun()
        else:
            _p1_bar.empty()
            learn_tgt = st.session_state.get("learn_target")
            if last_result is not None and learn_tgt is not None:
                try:
                    mismatch_info = engine.classify_results_mismatch(last_result, learn_tgt)
                except Exception:
                    mismatch_info = {"category": "value_error", "missing_cols": [],
                                     "extra_cols": [], "row_count_diff": 0,
                                     "row_direction": None, "bad_value_cols": [],
                                     "precision_cols": []}
            else:
                mismatch_info = {"category": "crash", "missing_cols": [],
                                 "extra_cols": [], "row_count_diff": 0,
                                 "row_direction": None, "bad_value_cols": [],
                                 "precision_cols": []}
            # LLM 诊断层：拿确定性比对的事实差异（含精确定位的行/格），解释为什么错
            diag = {"diagnosis": "", "suggested_clarification": ""}
            if last_result is not None:
                _loc = (mismatch_info.get("row_examples", []) or []) \
                    + (mismatch_info.get("value_examples", []) or [])
                _diff_full = mismatch or ""
                if _loc:
                    _diff_full += "\n\n【程序精确定位到的差异行/格】\n" + "\n".join(_loc)
                _diag_ph = st.empty()
                _diag_ph.info("🩺 正在分析失败原因，并为你起草澄清建议…")
                try:
                    diag = engine.diagnose_mismatch(
                        source_dfs=source_dfs,
                        target_df=target_df,
                        result_df=last_result,
                        diff_summary=_diff_full,
                        clarification_history="\n".join(st.session_state["clarifications_p1"]),
                        api_key=api_key,
                        **model_kw,
                    )
                except Exception:
                    diag = {"diagnosis": "", "suggested_clarification": ""}
                _diag_ph.empty()
            st.session_state["last_diagnosis"]          = diag.get("diagnosis", "")
            st.session_state["suggested_clarification"] = diag.get("suggested_clarification", "")
            st.session_state["last_mismatch"]      = mismatch
            st.session_state["last_mismatch_info"] = mismatch_info
            st.session_state["show_clue_input"]    = True
            st.rerun()

    # ── 待确认逻辑 ──────────────────────────────────────────────────────────
    if st.session_state["pending_code"]:
        st.success("✅ AI 已学会，与目标表完全一致！")

        if st.session_state["preview_result"] is not None:
            pv = st.session_state["preview_result"]
            st.write(f"结果预览：（{_result_shape_text(pv)}）")
            _render_preview(pv, key_prefix="pending")

        with st.expander("技术人员可查看生成的处理逻辑代码"):
            st.code(st.session_state["pending_code"], language="python")

        st.markdown("**给这个逻辑起个名字（可直接修改）：**")
        logic_name_input = st.text_input(
            "逻辑名称", value=st.session_state["pending_name"] or "",
            key="pending_name_input", label_visibility="collapsed",
        )

        cc1, cc2 = st.columns(2)
        save_clicked = cc1.button("💾 保存到逻辑库", type="primary")
        redo_clicked = cc2.button("✖️ 不满意，重新学习")

        if save_clicked:
            if storage.is_full():
                st.error(
                    f"逻辑库已满（{storage.MAX_ENTRIES}/{storage.MAX_ENTRIES}），"
                    "请先在左侧删除一个逻辑再保存。"
                )
            else:
                final_name = logic_name_input.strip() or st.session_state["pending_name"] or "处理逻辑"
                eid = storage.save_entry(
                    name=final_name,
                    locked_code=st.session_state["pending_code"],
                    learn_profile=st.session_state["learn_profile"] or [],
                    learn_sources=st.session_state["learn_sources"] or {},
                    learn_target=st.session_state["learn_target"] if st.session_state["learn_target"] is not None else pd.DataFrame(),
                )
                _reset_learn()
                st.session_state["task_counter"] += 1
                st.session_state["mode"] = "run"
                # 不预设 run_matched_id：让用户先上传新表再匹配执行，
                # 否则会在没有新表数据时立即执行导致 None 报错
                st.success(f"「{final_name}」已保存！请在此上传新表开始处理。")
                st.rerun()

        if redo_clicked:
            st.session_state["pending_code"]   = None
            st.session_state["preview_result"] = None
            st.session_state["pending_name"]   = None
            st.rerun()

    # ── 引导澄清（仅在 P1 自修正失败后才显示）──────────────────────────────
    if st.session_state["show_clue_input"] and not st.session_state["pending_code"]:
        used = len(st.session_state["clarifications_p1"])
        left = engine.MAX_CLARIFICATION_ROUNDS - used
        info = st.session_state.get("last_mismatch_info") or {"category": "value_error",
            "missing_cols": [], "extra_cols": [], "row_count_diff": 0,
            "row_direction": None, "bad_value_cols": [], "precision_cols": []}

        _render_mismatch_guidance(info, used)

        if st.session_state["last_mismatch"]:
            with st.expander("查看完整差异诊断（技术细节）"):
                st.text(st.session_state["last_mismatch"])

        if left > 0:
            suggested = st.session_state.get("suggested_clarification", "")
            if suggested:
                st.caption("💡 已为你起草澄清建议（可直接用，也可修改）：")
            clue = st.text_area(
                f"补充说明（还剩 {left} 次机会）",
                value=suggested,
                placeholder="根据上面的提示，用大白话描述处理规则",
                height=80, key=f"clue_input_{tc}_{used}",
            )
            if st.button("提交说明，让 AI 重试", disabled=not clue.strip(), type="primary"):
                st.session_state["clarifications_p1"].append(
                    f"[澄清第{used + 1}轮] {clue.strip()}"
                )
                st.session_state["show_clue_input"] = False
                st.session_state["trigger_learn"] = True   # 就地重跑，不退回上传页
                st.rerun()
        else:
            st.error(
                "已达 3 次澄清上限仍未对齐。建议重新检查源表和目标样例是否匹配，"
                "确认无误后点击下方按钮重新上传。"
            )
            if st.button("🔄 重新学习（清空当前进度）"):
                _reset_learn()
                st.session_state["task_counter"] += 1
                st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# 执行阶段
# ─────────────────────────────────────────────────────────────────────────────
elif st.session_state["mode"] == "run":
    st.subheader("第二步 · 用已有逻辑处理新表")

    if storage.count() == 0:
        st.info("逻辑库为空，请先切换到「教会新逻辑」教会我一个处理逻辑。")
        st.stop()

    # ── 输入新表：上传文件 或 直接粘贴 ────────────────────────────────────────
    st.caption("可 **上传 Excel**（支持多文件）或 **直接粘贴表格**。")
    new_files = st.file_uploader(
        "上传新一期的源数据表（文件名随意，支持多文件）",
        type=["xlsx", "xls"], accept_multiple_files=True,
        key=f"run_src_{tc}",
    )
    new_paste = st.text_area(
        "或直接粘贴新表", key=f"run_paste_{tc}", height=90,
        placeholder="从 Excel/飞书复制整块新表，粘到这里…",
    )
    _render_input_slot_preview(new_files, new_paste)

    run_ready = bool(new_files or new_paste.strip())
    detect_btn = st.button(
        "🔍 检测并匹配逻辑", type="primary",
        disabled=not (run_ready and api_key),
    )

    if detect_btn and run_ready:
        try:
            run_dfs, _ = _resolve_source_input(new_files, new_paste)
            if run_dfs is None:
                st.error("请提供新表（上传文件或粘贴表格）。")
                st.stop()
        except Exception as e:  # noqa: BLE001
            st.error(f"读取/解析失败：{e}")
            st.stop()

        best_meta, confidence, all_scored = storage.match(run_dfs)
        st.session_state["run_sources"]    = run_dfs
        st.session_state["run_best_meta"]  = best_meta
        st.session_state["run_confidence"] = confidence
        st.session_state["run_all_scored"] = all_scored
        st.session_state["run_matched_id"] = None
        st.session_state["run_result"]     = None
        st.session_state["run_error"]      = None
        st.rerun()

    # ── 匹配结果展示 ──────────────────────────────────────────────────────────
    run_dfs   = st.session_state["run_sources"]
    conf      = st.session_state["run_confidence"]
    best_meta = st.session_state["run_best_meta"]

    if run_dfs and best_meta and not st.session_state["run_matched_id"] and st.session_state["run_result"] is None:

        if conf >= HIGH_CONF:
            # 高置信：推荐一个，请用户确认
            st.info(
                f"检测到最匹配的逻辑：**「{best_meta['name']}」**（匹配度 {conf:.0%}），确认使用吗？"
            )
            c1, c2 = st.columns(2)
            if c1.button("✅ 确认，使用这个逻辑", type="primary"):
                st.session_state["run_matched_id"] = best_meta["id"]
                st.rerun()
            if c2.button("换一个逻辑"):
                st.session_state["run_confidence"] = 0.0  # 强制进入列表视图
                st.rerun()

        elif conf >= LOW_CONF:
            # 中等置信：展示列表让用户选
            st.warning("没有把握完全匹配，请从下面选择要使用的逻辑：")
            for item in st.session_state["run_all_scored"]:
                col_n, col_b = st.columns([4, 1])
                col_n.markdown(f"**{item['name']}**")
                col_n.caption(f"匹配度 {item['score']:.0%}　最近使用：{item['last_used_at']}")
                if col_b.button("选择", key=f"pick_{item['id']}"):
                    st.session_state["run_matched_id"] = item["id"]
                    st.rerun()

        else:
            # 无匹配
            st.error("这张表和逻辑库里的所有逻辑都差异较大，无法自动匹配。")
            st.markdown("你可以：")
            if st.button("手动指定逻辑（从列表选）"):
                st.session_state["run_confidence"] = LOW_CONF  # 强制展示列表
                st.rerun()
            if st.button("去教会新逻辑"):
                _reset_learn()
                _reset_run()
                st.session_state["mode"] = "learn"
                st.rerun()

    # ── 执行逻辑 ──────────────────────────────────────────────────────────────
    matched_id = st.session_state["run_matched_id"]
    if matched_id and run_dfs and st.session_state["run_result"] is None and not st.session_state["run_error"]:
        try:
            entry = storage.load_entry(matched_id)
        except Exception as e:  # noqa: BLE001
            st.error(f"加载逻辑失败：{e}")
            st.stop()

        _p2_bar = st.progress(0.0, text="加载处理逻辑…")
        _p2_result: list = []
        _p2_error: list = []
        model_kw_p2 = _model_kwargs()   # 主线程取，避免线程内访问 session

        def _run_p2():
            try:
                _p2_result.append(engine.phase2_process_with_healing(
                    locked_code=entry["locked_code"],
                    new_source_dfs=run_dfs,
                    learn_sources=entry["learn_sources"],
                    learn_target=entry["learn_target"],
                    learn_profile=entry["learn_profile"],
                    api_key=api_key,
                    **model_kw_p2,
                ))
            except Exception as _e:
                _p2_error.append(_e)

        _p2_thread = threading.Thread(target=_run_p2, daemon=True)
        _p2_thread.start()

        _p2_stages = [
            (0.00, "加载处理逻辑…"),
            (0.15, "正在处理数据…"),
            (0.60, "校验处理结果…"),
            (0.85, "即将完成…"),
        ]
        _p2_elapsed = 0.0
        while _p2_thread.is_alive():
            _p2_elapsed += 0.25
            _pct2 = min(0.92, _p2_elapsed / 20.0)
            _label2 = _p2_stages[0][1]
            for _thresh2, _msg2 in _p2_stages:
                if _pct2 >= _thresh2:
                    _label2 = _msg2
            _p2_bar.progress(_pct2, text=_label2)
            time.sleep(0.25)

        if not _p2_result:
            _p2_bar.empty()
            err_msg = str(_p2_error[0]) if _p2_error else "未知错误"
            st.error(f"处理失败：{err_msg}")
            st.stop()

        result, healed_code, error_msg = _p2_result[0]
        _p2_bar.progress(1.0, text="✅ 处理完成！")
        time.sleep(0.4)
        _p2_bar.empty()

        if result is not None:
            storage.touch_entry(matched_id)
            if healed_code:
                new_profile = engine.build_learn_profile(entry["learn_sources"])
                storage.update_code_and_profile(matched_id, healed_code, new_profile)
                st.session_state["run_healed"] = True
            st.session_state["run_result"] = result
        else:
            st.session_state["run_error"] = error_msg
        st.rerun()

    # ── 结果展示 ──────────────────────────────────────────────────────────────
    if st.session_state["run_result"] is not None:
        if st.session_state["run_healed"]:
            st.info("已根据本次表格自动调整处理方式，结果如下。")
        else:
            st.success("✅ 处理完成！")

        result_df = st.session_state["run_result"]
        before_df = None
        if run_dfs:
            try:
                before_df = list(run_dfs.values())[0]
            except Exception:
                before_df = None

        added = []
        if before_df is not None and not _is_multi(result_df):
            added, _, _ = _diff_columns(before_df, result_df)
        log = [
            f"- 读取新源表：{before_df.shape[0]} 行 × {before_df.shape[1]} 列"
            if before_df is not None else "- 已读取新源表",
            "- 命中已学逻辑，本地执行 pandas 处理（数据不出本机）",
        ]
        if _is_multi(result_df):
            log.append(f"- 产出 {len(result_df)} 张结果表：" + "、".join(result_df.keys()))
        if added:
            log.append("- 生成新列：" + "、".join(added))
        log.append(f"- 输出结果：{_result_shape_text(result_df)}")

        _render_result_tabs(before_df, result_df, log, key_prefix="run")

        if st.button("🆕 开始新任务"):
            _reset_run()
            st.session_state["task_counter"] += 1
            st.rerun()

    # ── 执行失败 ──────────────────────────────────────────────────────────────
    if st.session_state["run_error"]:
        err = st.session_state["run_error"]
        st.error(
            "处理失败，自动修复也没能解决。可能是这批新表的结构变化较大，"
            "或这个逻辑不适用于当前表格。"
        )
        with st.expander("查看技术细节"):
            st.text(err)

        e1, e2 = st.columns(2)
        if e1.button("换一个逻辑试试"):
            st.session_state["run_matched_id"] = None
            st.session_state["run_error"]      = None
            st.session_state["run_confidence"] = LOW_CONF  # 直接展示列表
            st.rerun()
        if e2.button("重新教会我（上传新样例）"):
            _reset_run()
            _reset_learn()
            st.session_state["mode"] = "learn"
            st.session_state["task_counter"] += 1
            st.rerun()
