"""
表速通 · 首页演示资产 (demo_logic.py)

提供「一键体验」所需的预存逻辑与样例数据：
  - LOCKED_CODE：已学好的用户分层处理逻辑（pandas），运行不调用大模型
  - 样例数据指向 case19_yonghu_fenceng（已随仓库提交）

设计意图：新用户/面试官无需填 Key、无需准备文件，点一下即可看到
「源表 → 处理结果 → Diff 对比」的完整效果，直观展示核心价值。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_DEMO_DIR = Path(__file__).parent / "biaosutong_test_cases" / "case19_yonghu_fenceng"

DEMO_TITLE = "用户分层：RFM 价值分层"
DEMO_STORY = (
    "某电商运营部每月要把一份用户行为明细，按消费额、活跃度自动打上"
    "**会员等级、活跃状态、重点维护**标记，给精细化营销用。"
    "以前用 Excel 套几层嵌套 IF 公式要反复核对，"
    "用表速通教一次，之后每月新数据丢进来 30 秒出结果。"
)

# 已锁定的处理逻辑（等价于目标表里的 ROUND / 嵌套IF / AND-OR 复合公式）
LOCKED_CODE = '''
import pandas as pd


def process_data(source_dfs):
    df = list(source_dfs.values())[0].copy()
    out = pd.DataFrame()
    out["用户ID"] = df["用户ID"]
    out["累计消费金额"] = df["累计消费金额"].astype(int)
    out["累计订单数"] = df["累计订单数"].astype(int)
    out["近30天登录天数"] = df["近30天登录天数"].astype(int)
    out["客单价"] = (out["累计消费金额"] / out["累计订单数"]).round(2)

    def _tier(x):
        if x >= 50000:
            return "钻石"
        if x >= 20000:
            return "黄金"
        if x >= 5000:
            return "白银"
        return "普通"

    def _status(d):
        if d >= 15:
            return "活跃"
        if d >= 5:
            return "一般"
        return "沉睡"

    out["会员等级"] = out["累计消费金额"].apply(_tier)
    out["活跃状态"] = out["近30天登录天数"].apply(_status)
    out["重点维护"] = [
        "是" if (t in ("钻石", "黄金") and s == "活跃") else "否"
        for t, s in zip(out["会员等级"], out["活跃状态"])
    ]
    return out
'''


def load_demo_source() -> pd.DataFrame:
    """学习用源表（用户行为明细）。"""
    return pd.read_excel(_DEMO_DIR / "source_data.xlsx")


def load_demo_target() -> pd.DataFrame:
    """目标样例（分层结果，含公式计算值）。"""
    return pd.read_excel(_DEMO_DIR / "target.xlsx")


def load_demo_new_source() -> pd.DataFrame:
    """新一期源表（演示「处理新表」）。"""
    return pd.read_excel(_DEMO_DIR / "new_source.xlsx")


def demo_source_dfs() -> dict[str, pd.DataFrame]:
    """构造 engine 可用的 source_dfs（键名随意，逻辑取第一个）。"""
    return {"用户行为明细_Sheet1": load_demo_new_source()}


def is_available() -> bool:
    return (_DEMO_DIR / "source_data.xlsx").exists()
