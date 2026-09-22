"""
表速通 · 逻辑缓存存储层 (storage.py)

本地文件存储：
  data/logic_cache.json        索引（名称、时间、结构签名）
  data/cache/{id}_code.py      锁定的处理逻辑
  data/cache/{id}_src.pkl      学习用源表
  data/cache/{id}_tgt.pkl      学习用目标样例
"""
from __future__ import annotations

import difflib
import json
import pickle
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

MAX_ENTRIES = 5
HIGH_CONF = 0.72   # 高置信：直接推荐
LOW_CONF  = 0.35   # 低于此：无匹配

_BASE  = Path(__file__).parent / "data"
_CACHE = _BASE / "cache"
_IDX   = _BASE / "logic_cache.json"


class StorageFullError(RuntimeError):
    """逻辑库达到容量上限，调用方应保留结果并允许用户重试。"""


def _ensure():
    _BASE.mkdir(exist_ok=True)
    _CACHE.mkdir(exist_ok=True)


# ── 索引读写 ──────────────────────────────────────────────────────────────────

def load_index() -> list[dict]:
    _ensure()
    if not _IDX.exists():
        return []
    try:
        return json.loads(_IDX.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_index(entries: list[dict]):
    _ensure()
    _IDX.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def count() -> int:
    return len(load_index())


def is_full() -> bool:
    return count() >= MAX_ENTRIES


# ── CRUD ─────────────────────────────────────────────────────────────────────

def save_entry(
    name: str,
    locked_code: str,
    learn_profile: list[dict],
    learn_sources: dict[str, pd.DataFrame],
    learn_target: pd.DataFrame,
) -> str:
    """保存新逻辑，返回 entry_id。"""
    _ensure()
    eid = uuid.uuid4().hex[:8]
    (_CACHE / f"{eid}_code.py").write_text(locked_code, encoding="utf-8")
    with open(_CACHE / f"{eid}_src.pkl", "wb") as f:
        pickle.dump(learn_sources, f)
    with open(_CACHE / f"{eid}_tgt.pkl", "wb") as f:
        pickle.dump(learn_target, f)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entries = load_index()
    entries.append({
        "id": eid,
        "name": name,
        "learn_profile": learn_profile,
        "created_at": now,
        "last_used_at": now,
    })
    _write_index(entries)
    return eid


def save_compose_entry(
    name: str,
    plan: dict,
    profile: list[dict],
    output_schema: list[dict],
    classification_mapping: dict[str, str] | None = None,
    structure_profile: list[dict] | None = None,
    applicability: dict | None = None,
) -> str:
    """保存智能制表的紧凑方案，不复制会话源数据。"""
    _ensure()
    entries = load_index()
    if len(entries) >= MAX_ENTRIES:
        raise StorageFullError(f"逻辑库已达到 {MAX_ENTRIES} 条上限，请先整理旧逻辑。")
    eid = uuid.uuid4().hex[:8]
    (_CACHE / f"{eid}_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (_CACHE / f"{eid}_mapping.json").write_text(json.dumps(classification_mapping or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = {
        "id": eid,
        "name": name,
        "origin": "smart_table",
        "schema_version": 2,
        "profile": profile,
        "learn_profile": structure_profile or [],
        "output_schema": output_schema,
        "applicability": applicability or {},
        "rule_pack_versions": {
            "classification": plan.get("classification", {}).get("rule_pack_version", "1.0"),
        },
        "plan_file": f"{eid}_plan.json",
        "classification_mapping_file": f"{eid}_mapping.json",
        "created_at": now,
        "last_used_at": now,
    }
    entries.append(entry)
    _write_index(entries)
    return eid


def load_entry(eid: str) -> dict:
    """加载完整逻辑数据（含代码和 DataFrame）。"""
    meta = next((e for e in load_index() if e["id"] == eid), None)
    if meta is None:
        raise KeyError(f"逻辑 {eid} 不存在")
    if meta.get("origin", "learn") == "smart_table":
        plan_path = _CACHE / meta.get("plan_file", f"{eid}_plan.json")
        mapping_path = _CACHE / meta.get("classification_mapping_file", f"{eid}_mapping.json")
        if not plan_path.exists():
            raise KeyError(f"智能制表逻辑 {eid} 方案文件不存在")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}
        return {**meta, "plan": plan, "classification_mapping": mapping}
    locked_code = (_CACHE / f"{eid}_code.py").read_text(encoding="utf-8")
    with open(_CACHE / f"{eid}_src.pkl", "rb") as f:
        learn_sources = pickle.load(f)
    with open(_CACHE / f"{eid}_tgt.pkl", "rb") as f:
        learn_target = pickle.load(f)
    return {**meta, "locked_code": locked_code,
            "learn_sources": learn_sources, "learn_target": learn_target}


def delete_entry(eid: str):
    _write_index([e for e in load_index() if e["id"] != eid])
    for suf in ["_code.py", "_src.pkl", "_tgt.pkl", "_plan.json", "_mapping.json"]:
        p = _CACHE / f"{eid}{suf}"
        if p.exists():
            p.unlink()


def rename_entry(eid: str, new_name: str):
    entries = load_index()
    for e in entries:
        if e["id"] == eid:
            e["name"] = new_name
            break
    _write_index(entries)


def touch_entry(eid: str):
    """更新最近使用时间。"""
    entries = load_index()
    for e in entries:
        if e["id"] == eid:
            e["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            break
    _write_index(entries)


def update_code_and_profile(eid: str, new_code: str, new_profile: list[dict]):
    """P2 自愈后同步更新代码和结构签名。"""
    (_CACHE / f"{eid}_code.py").write_text(new_code, encoding="utf-8")
    entries = load_index()
    for e in entries:
        if e["id"] == eid:
            e["learn_profile"] = new_profile
            e["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            break
    _write_index(entries)


# ── 相似度匹配 ────────────────────────────────────────────────────────────────

def _col_sim(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _str_sim(x: str, y: str) -> float:
    return difflib.SequenceMatcher(None, x.lower(), y.lower()).ratio()


def _score_profile(new_dfs: dict[str, pd.DataFrame], profile: list[dict]) -> float:
    """计算新 DFs 与 learn_profile 的结构相似度（0~1）。"""
    if not profile:
        return 0.0
    new_items = [
        (k.rsplit("_", 1)[-1] if "_" in k else k, list(df.columns))
        for k, df in new_dfs.items()
    ]
    if not new_items:
        return 0.0
    total = 0.0
    for pe in profile:
        best = max(
            0.3 * _str_sim(ns, pe["sheet"]) + 0.7 * _col_sim(nc, pe["columns"])
            for ns, nc in new_items
        )
        total += best
    return total / len(profile)


def match(new_dfs: dict[str, pd.DataFrame]) -> tuple[dict | None, float, list[dict]]:
    """
    扫描缓存，返回 (最佳匹配元数据, 置信度, 全部条目含得分排序列表)。
    置信度 >= HIGH_CONF → 直接推荐；< LOW_CONF → 无匹配。
    """
    entries = load_index()
    if not entries:
        return None, 0.0, []
    scored = sorted(
        [(e, _score_profile(new_dfs, e.get("learn_profile", []))) for e in entries],
        key=lambda x: x[1], reverse=True,
    )
    best_meta, best_score = scored[0]
    all_scored = [{"score": round(s, 3), **e} for e, s in scored]
    return best_meta, best_score, all_scored
