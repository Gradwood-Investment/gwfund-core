"""生成四端一致性黄金向量契约 `golden_vectors.json`。

设计（对应 SOW §0.1 / 决策 D31 / 附件C §13）：

* **Tier-1（手算基线）**：期望值来自《附件A》手算样例或独立解析解，
  **不来自本引擎**——这样引擎出错时测试才会失败，而不是「自己验自己」。容差 1e-6。
* **Tier-2（回归基线）**：期望值在本版本由引擎计算后冻结，用于**防止无意的行为漂移**。
  它**不是独立真值**，故单独标注 `tier=2`，避免与 Tier-1 混淆。

四端（core / api / web / desktop / miniapp）都必须加载本文件逐项对拍；
差异超容差即阻断 CI（SOW §7.3 C12）。

用法::

    python 10_dev/contracts/build_golden_vectors.py            # 重新生成
    python 10_dev/contracts/build_golden_vectors.py --check    # 只校验不写盘
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE_SRC = HERE.parent / "gwfund-core" / "src"
sys.path.insert(0, str(CORE_SRC))

from gwfund_core import attribution, chase, fees, identities, mixing, returns  # noqa: E402

OUT = HERE / "golden_vectors.json"
TOLERANCE = 1e-6


def _f(x: object) -> float:
    """Decimal → float（JSON 只支持 float；Tier-1 容差 1e-6 足够）。"""
    return float(x) if isinstance(x, Decimal) else float(x)


# ==========================================================================
# 通用调用规格（Tier-2 回归向量的批量声明方式）
# ==========================================================================
#
# 每个向量携带 ``call: {module, fn, args?, kwargs?, extract}``；
# 对拍器按规格执行同一调用并逐字段比对。
# 参数编码约定（保证 JSON 可序列化）：
#   {"__date__": "2026-06-01"}  → datetime.date
#   {"__dec__": "1.20"}         → decimal.Decimal
# 提取约定：``extract: {"输出名": "属性名或字典键"}``
# --------------------------------------------------------------------------


def _d(s: str) -> dict:
    return {"__dec__": s}


def _date(s: str) -> dict:
    return {"__date__": s}


def _t2(
    vid: str,
    name: str,
    source: str,
    module: str,
    fn: str,
    *,
    args: list | None = None,
    kwargs: dict | None = None,
    extract: dict | None = None,
) -> dict:
    """构造一个 Tier-2 回归向量：期望值由引擎当场算出并冻结。"""
    import importlib

    mod = importlib.import_module(f"gwfund_core.{module}")
    func = getattr(mod, fn)
    real_args = [_decode(a) for a in (args or [])]
    real_kwargs = {k: _decode(v) for k, v in (kwargs or {}).items()}
    res = func(*real_args, **real_kwargs)
    spec = extract or {"value": "__self__"}
    expected = {k: _encode(_extract(res, p)) for k, p in spec.items()}
    return {
        "id": vid,
        "tier": 2,
        "name": name,
        "source": source,
        "module": module,
        "call": {
            "fn": fn,
            "args": [_encode_arg(a) for a in (args or [])],
            "kwargs": {k: _encode_arg(x) for k, x in (kwargs or {}).items()},
            "extract": spec,
        },
        "expected": expected,
        "notes": "通用调用规格生成（Tier-2 回归基线，非独立真值）",
    }


def _encode_arg(v: object) -> object:
    """把参数编码为 JSON 可序列化形式（与 :func:`_decode` 互逆）。"""
    if isinstance(v, Decimal):
        return {"__dec__": str(v)}
    if hasattr(v, "isoformat") and v.__class__.__name__ == "date":
        return {"__date__": v.isoformat()}
    if isinstance(v, (list, tuple)):
        return [_encode_arg(x) for x in v]
    if isinstance(v, dict):
        return {k: _encode_arg(x) for k, x in v.items()}
    return v


def _decode(v: object) -> object:
    if isinstance(v, dict):
        if "__date__" in v:
            from datetime import date as _date_cls

            return _date_cls.fromisoformat(v["__date__"])
        if "__dec__" in v:
            return Decimal(v["__dec__"])
    return v


def _encode(v: object) -> object:
    if isinstance(v, (Decimal,)):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_encode(x) for x in v]
    if isinstance(v, dict):
        return {k: _encode(x) for k, x in v.items()}
    return v


def _extract(obj: object, path: str) -> object:
    if path == "__self__":
        return obj
    if hasattr(obj, path):
        return getattr(obj, path)
    if isinstance(obj, dict) and path in obj:
        return obj[path]
    if isinstance(obj, (list, tuple)) and path.isdigit():
        return obj[int(path)]
    raise KeyError(f"无法从 {type(obj).__name__} 提取 {path!r}")


def tier2_generic() -> list[dict]:
    """Tier-2 回归向量（批量）：覆盖 returns / fees / identities / attribution /
    mixing / chase / metrics 七个模块的主要公开函数。"""
    from datetime import date, timedelta

    dates10 = [date(2026, 4, 1) + timedelta(days=i) for i in range(10)]
    navs10 = [1.0 + 0.01 * i for i in range(10)]
    w = [0.5, 0.3, 0.2]
    v: list[dict] = []

    # ---------------- returns ----------------
    v.append(_t2("GV-13", "单期净收益（含分红）", "式(1.2)", "returns", "daily_return",
                 args=[1.20, 1.18, 0.03]))
    v.append(_t2("GV-14", "单期净收益：分母为 0 返回 None", "式(1.2)+§5.8", "returns", "daily_return",
                 args=[0.0, 1.0, 0.0]))
    v.append(_t2("GV-15", "复权净值序列（1 次分红）", "M103 / 决策 D07", "returns",
                 "adjusted_nav_series",
                 args=[[1.00, 0.95, 1.00, 1.05], [0.0, 0.05, 0.0, 0.0]]))
    v.append(_t2("GV-16", "分红还原：Δacc − Δunit", "§8.2 步3", "returns",
                 "derive_dividend_series",
                 args=[[1.00, 1.00, 1.05, 1.10], [1.00, 0.95, 1.00, 1.05]]))
    v.append(_t2("GV-17", "年化：满 365 天才年化", "M203 / T9", "returns", "annualize",
                 args=[0.21, 730], extract={"value": "value", "annualized": "annualized"}))
    v.append(_t2("GV-18", "年化：不足 1 年不年化", "T9", "returns", "annualize",
                 args=[0.10, 200], extract={"value": "value", "annualized": "annualized"}))
    v.append(_t2("GV-19", "IRR：等间隔三期", "式(5.2)", "returns", "irr",
                 args=[[-1_000_000, -500_000, 0, 1_520_000]],
                 extract={"value": "value", "multi_root": "multi_root"}))
    v.append(_t2("GV-20", "IRR：决策 D10 半年期现金流", "D10 / 式(5.2)", "returns", "irr",
                 args=[[-100.0, -900.0, 840.0], [0.0, 0.5, 1.0]],
                 extract={"value": "value", "multi_root": "multi_root"}))

    # ---------------- fees ----------------
    v.append(_t2("GV-21", "单日管理费（非闰年）", "式(3.1)", "fees", "daily_fee",
                 args=[10_000_000_000, "1.20", _date("2026-06-01")]))
    v.append(_t2("GV-22", "单日托管费（闰年 366）", "式(3.2)+§3.1 细节2", "fees", "daily_fee",
                 args=[10_000_000_000, "0.20", _date("2024-06-03")]))
    v.append(_t2("GV-23", "三类费用单日合计", "式(3.1)-(3.3)", "fees", "daily_accrual",
                 args=[1_000_000_000, _date("2026-06-01"), "1.20", "0.20", "0.40"],
                 extract={"total": "total", "mgmt": "mgmt"}))
    v.append(_t2("GV-24", "赎回费分档：6 天", "§3.2 闭区间", "fees", "lookup_tier_rate",
                 args=[[{"holding_days": [0, 6], "rate_pct": 1.50},
                        {"holding_days": [7, 364], "rate_pct": 0.50},
                        {"holding_days": [365, 729], "rate_pct": 0.25},
                        {"holding_days": [730, None], "rate_pct": 0.00}], 6]))
    v.append(_t2("GV-25", "赎回费分档：730 天", "§3.2 闭区间", "fees", "lookup_tier_rate",
                 args=[[{"holding_days": [0, 6], "rate_pct": 1.50},
                        {"holding_days": [7, 364], "rate_pct": 0.50},
                        {"holding_days": [365, 729], "rate_pct": 0.25},
                        {"holding_days": [730, None], "rate_pct": 0.00}], 730]))
    v.append(_t2("GV-26", "业绩报酬：涨 3% 未达 5% 门槛", "SPEC-GAP-01", "fees",
                 "performance_fee_daily",
                 args=[10_000_000_000, 1.03, 1.00, 0.05, 20, _date("2026-06-01")],
                 extract={"fee": "0", "hwm": "1"}))
    v.append(_t2("GV-27", "业绩报酬：涨 8% 达门槛", "SPEC-GAP-01", "fees",
                 "performance_fee_daily",
                 args=[10_000_000_000, 1.08, 1.00, 0.05, 20, _date("2026-06-01")],
                 extract={"fee": "0", "hwm": "1"}))
    v.append(_t2("GV-28", "冲击成本分档：50 亿", "§3.3 分档表", "fees", "impact_cost_pct",
                 args=[5_000_000_000]))
    v.append(_t2("GV-29", "买入成本率（不含印花税）", "SPEC-GAP-03", "fees",
                 "trade_cost_rate_pct",
                 args=[1_000_000_000], kwargs={"side": "buy"}))
    v.append(_t2("GV-30", "卖出成本率（含印花税）", "SPEC-GAP-03", "fees",
                 "trade_cost_rate_pct",
                 args=[1_000_000_000], kwargs={"side": "sell"}))
    v.append(_t2("GV-31", "年报反算隐含成本率", "式(3.7)", "fees", "implied_cost_rate",
                 args=[_d("3000000"), _d("2000000000")]))

    # ---------------- identities ----------------
    v.append(_t2("GV-32", "基民净盈亏 P_inv", "式(4.1)", "identities", "p_inv",
                 args=[840.0, 0.0, 100.0, 900.0]))
    v.append(_t2("GV-33", "净申购金额（费前）", "式(4.2a)", "identities", "net_sub_amount",
                 args=[1_000_000.0, 400_000.0, 1.10, 1.05]))
    v.append(_t2("GV-34", "恒等式 A 同频容差内", "式(A)/T-2", "identities", "check_identity_a",
                 args=[1000.0, 1100.0, 100.005, 0.0, 0.0],
                 extract={"passed": "passed", "residual": "residual"}))
    v.append(_t2("GV-35", "恒等式 A 同频超容差", "式(A)/T-2", "identities", "check_identity_a",
                 args=[1000.0, 1100.0, 100.02, 0.0, 0.0],
                 extract={"passed": "passed"}))
    v.append(_t2("GV-36", "恒等式 B 独立构造", "式(B)", "identities", "check_identity_b",
                 args=[1000.0, 1200.0, 50.0, 100.0, 150.0],
                 extract={"passed": "passed"}))
    v.append(_t2("GV-37", "恒等式 C' 含 Other", "式(C')", "identities", "check_identity_c",
                 args=[980.0, 850.0, 150.0, 20.0],
                 extract={"passed": "passed", "residual": "residual"}))
    v.append(_t2("GV-38", "Other 归一化残差", "式(4.5)", "identities",
                 "unexplained_residual_pct", args=[40_000.0, 10_000_000.0, 10_000_000.0]))
    v.append(_t2("GV-39", "Other 疑似科目归错", "§4.5 量级预期", "identities",
                 "is_other_suspect", args=[20_000.0, 1_000_000.0, 1_000_000.0]))

    # ---------------- attribution ----------------
    v.append(_t2("GV-40", "份额归因：红利再投不产生假净申购", "式(6.3)/T1", "attribution",
                 "share_attribution",
                 args=[1_000_000.0, 1_100_000.0, 0.0, 100_000.0],
                 extract={"passed": "passed", "residual": "residual"}))
    v.append(_t2("GV-41", "收益分解：div_effect 取负号", "式(6.4)/SPEC-GAP-02",
                 "attribution", "return_decomposition",
                 args=[1000.0, 100.0, 200.0, 50.0],
                 extract={"div_effect": "div_effect", "delta_a_ratio": "delta_a_ratio",
                          "closure_passed": "closure_passed"}))
    v.append(_t2("GV-42", "独立路径 R_net（平均 AUM）", "§5.1 第③段", "attribution",
                 "r_net_from_return",
                 args=[1000.0, 0.10], kwargs={"average_aum": 2000.0}))
    v.append(_t2("GV-43", "披露路径 R_net", "式(C)", "attribution",
                 "r_net_from_disclosure", args=[1000.0, 150.0]))

    # ---------------- mixing ----------------
    v.append(_t2("GV-44", "阶梯法：首个季末前为 None", "式(8.1)", "mixing", "step_shares",
                 args=[[date(2025, 1, 15)], [date(2025, 3, 31)], [100.0]]))
    v.append(_t2("GV-45", "线性插值：两季末中点", "式(8.3)", "mixing", "linear_shares",
                 args=[[date(2025, 5, 15)], [date(2025, 3, 31), date(2025, 6, 30)],
                       [100.0, 110.0]]))
    v.append(_t2("GV-46", "方案选择：有申赎明细 → 方案3", "§6.3 降级判据", "mixing",
                 "choose_method",
                 kwargs={"has_sub_red": True, "has_start_end_share": True,
                         "has_end_share": True}))
    v.append(_t2("GV-47", "精度等级：日频 AUM 最高 B", "§6.4 规则1", "mixing", "precision_for",
                 args=["quarterly_detail"], kwargs={"is_daily_aum": True}))
    v.append(_t2("GV-48", "AUM 误差上界（§6.5 样例）", "式(8.7)", "mixing", "aum_error_bound",
                 args=[-187_000_000.0, 1.63674, 1.02006]))

    # ---------------- chase ----------------
    v.append(_t2("GV-49", "净值历史分位序列", "式(9.2)", "chase", "percentile_series",
                 args=[[1.0, 1.5, 1.2, 1.6]]))
    v.append(_t2("GV-50", "追高金额（高位净申购）", "式(9.1)", "chase", "chase_amount",
                 args=[[0.0, 0.0, 5.0e8], [0.5, 0.9, 1.0]], kwargs={"theta": 0.8}))
    v.append(_t2("GV-51", "追高占比（无申购 → None）", "式(9.4)+§5.8", "chase", "chase_ratio",
                 args=[0.0, 0.0]))
    v.append(_t2("GV-52", "追高亏损只计亏损部分", "式(9.5)", "chase", "chase_loss",
                 args=[[1.0e8, 1.0e8], [0.9, 0.9], [1.0, 1.0], 0.8]))
    v.append(_t2("GV-53", "监管口径方向相反 → BLOCK", "式(9.6)", "chase", "caliber_check",
                 args=[0.75, 0.20]))

    # ---------------- metrics ----------------
    v.append(_t2("GV-54", "年化波动率（常收益 → 0）", "M204", "metrics",
                 "annualized_volatility", args=[[0.01] * 30]))
    v.append(_t2("GV-55", "最大回撤：峰谷定位", "M205", "metrics", "max_drawdown",
                 args=[[1.0, 1.2, 0.9, 1.1]],
                 extract={"mdd": "mdd", "peak_index": "peak_index",
                          "trough_index": "trough_index"}))
    v.append(_t2("GV-56", "下行波动（全部样本为分母）", "M207 定义", "metrics",
                 "downside_deviation", args=[[0.01, 0.01, -0.02, -0.02]],
                 kwargs={"periods_per_year": 1}))
    v.append(_t2("GV-57", "卡玛比率", "M208", "metrics", "calmar_ratio", args=[0.20, 0.10]))
    v.append(_t2("GV-58", "每元管理费对应基民盈亏", "M306", "metrics", "fee_per_yuan_pnl",
                 args=[-160_000_000, 18_420_000]))
    v.append(_t2("GV-59", "持有体验分布（单调涨）", "M307", "metrics",
                 "holding_experience",
                 args=[[1.0 + 0.001 * i for i in range(300)]], kwargs={"window": 250},
                 extract={"samples": "samples", "win_rate": "win_rate"}))
    v.append(_t2("GV-60", "费用率（半年 → 年化）", "M310", "metrics", "fee_ratio",
                 args=[7_000_000, 1_000_000_000, 182.5]))
    v.append(_t2("GV-61", "换手率：全切换 = 1.0", "式(3.6)", "metrics",
                 "turnover_from_weights",
                 args=[{"A": 1.0, "B": 0.0}, {"A": 0.0, "B": 1.0}, {"A": 0.0, "B": 0.0}, 0.0]))
    v.append(_t2("GV-62", "披露换手率：取买卖较低者", "§3.3 披露口径", "metrics",
                 "turnover_disclosed", args=[120, 80, 200]))

    # ---------------- reconcile（对账 8 步）----------------
    v.append(_t2("GV-63", "对账步1：份额×净值达标", "附件A §8.2 步1 / T-3", "reconcile",
                 "step1_share_nav_identity",
                 args=[1_000_000, 1.0, 1_000_050],
                 extract={"level": "level", "step": "step"}))
    v.append(_t2("GV-64", "对账步1：偏差 3% 应 BLOCK", "附件A §8.3 判级", "reconcile",
                 "step1_share_nav_identity",
                 args=[1_000_000, 1.0, 970_000], extract={"level": "level"}))
    v.append(_t2("GV-65", "对账步2：红利再投缺失应被发现", "式(1.4)/陷阱 T1", "reconcile",
                 "step2_share_continuity",
                 kwargs={"s_end_wan": 1_100_000, "s_prev_wan": 1_000_000,
                         "net_sub_shares_wan": 0, "div_reinvest_wan": 0},
                 extract={"level": "level", "residual_in_detail": "detail"}))
    v.append(_t2("GV-66", "对账步5：年 ±2% 容差", "附件C T-4", "reconcile",
                 "step5_mgmt_fee",
                 args=[1025, 1000], kwargs={"period": "year"}, extract={"level": "level"}))
    v.append(_t2("GV-67", "对账步3：无公告 → SKIP 非 PASS", "附件A §8.2 步3", "reconcile",
                 "step3_dividend_amount", args=[18_730, None], extract={"level": "level"}))

    del dates10, navs10, w
    return v



# ==========================================================================
# Tier-1：手算基线（期望值不来自引擎）
# ==========================================================================


def tier1_vectors() -> list[dict]:
    vectors: list[dict] = []

    # ---- GV-01 附件A §4.5 三期手算样例 ----
    navs = [1.000, 1.100, 1.045, 1.0450]
    rs = [returns.daily_return(navs[i - 1], navs[i]) for i in range(1, len(navs))]
    twr = returns.chain_return(rs)
    cf = [-1_000_000, -500_000, 0, 1_520_000]
    mwr = returns.irr(cf).value
    vectors.append(
        {
            "id": "GV-01",
            "tier": 1,
            "name": "附件A §4.5 三期手算样例",
            "source": "《附件A》§4.5 式(5.1)(5.2)(5.3)",
            "module": "returns",
            "inputs": {"navs": navs, "cashflows": cf, "times": [0, 1, 2, 3]},
            "expected": {
                # TWR 解析解：1.10 × 0.95 × 1.00 − 1
                "twr": 0.045,
                # MWR 解析解：100y³+50y²−152=0，y=1.0049783 → x=0.497828%
                # 《裁定书一》R1：附件A 已按真值修订（DEV-001 收口）
                "mwr": 0.004978283759868921,
                "mwr_pct": 0.4978283759868921,
                "gap_pct": 4.002171624013101,
                # DEV-001b：年化缺口 17.2456 pp（原 17.2438 系截断值相减之误）
                "gap_ann_pp": 17.2456,
            },
            "notes": "DEV-001 已裁定：附件A 按真值修订为 0.497828% / 4.002172pct / 2.0062%；"
            "同时断言 NPV(mwr)=0 作为不依赖文档的自洽校验",
            # 文档只公布到 4 位小数，故该字段按「半个末位」放宽容差（T-1 的例外，须显式声明）
            "tolerance_overrides": {"gap_ann_pp": 1e-4},
        }
    )

    # ---- GV-02 决策 D10 MWR 黄金用例 ----
    res2 = returns.irr([-100.0, -900.0, 840.0], [0.0, 0.5, 1.0]).value
    vectors.append(
        {
            "id": "GV-02",
            "tier": 1,
            "name": "决策 D10 MWR 黄金用例",
            "source": "决策记录 D10（秉枢复算：840y²−900y−100=0）",
            "module": "returns",
            "inputs": {"cashflows": [-100.0, -900.0, 840.0], "times": [0.0, 0.5, 1.0]},
            "expected": {
                "twr": 0.20,  # 1.5 × 0.8 − 1
                "mwr": -0.273125287861484,
                "gap_pct": 47.3125287861484,
                "p_inv": -160.0,  # 840 − 1000
            },
            "notes": "D10 定：MWR=−27.31%、缺口=+47.31pct，容差 ±0.1pct",
        }
    )

    # ---- GV-03 附件A §3.5 费用计提 ----
    acc = fees.daily_accrual(10_000_000_000, date(2026, 6, 1), "1.20", "0.20", 0)
    acc_leap = fees.daily_accrual(10_000_000_000, date(2024, 6, 3), "1.20", "0.20", 0)
    vectors.append(
        {
            "id": "GV-03",
            "tier": 1,
            "name": "附件A §3.5 费用计提（100 亿 / 1.20% / 0.20%）",
            "source": "《附件A》§3.5",
            "module": "fees",
            "inputs": {
                "a_prev": 10_000_000_000,
                "rate_mgmt_pct": "1.20",
                "rate_custody_pct": "0.20",
                "rate_sales_pct": 0,
                "date_non_leap": "2026-06-01",
                "date_leap": "2024-06-03",
            },
            "expected": {
                "mgmt_2026": 328_767.12,
                "custody_2026": 54_794.52,
                "total_2026": 383_561.64,
                "mgmt_2024_leap": 327_868.85,
            },
            "notes": "分母用 A_{t-1}；闰年 366；年化费率线性除（附件A §3.1 三条细节）",
        }
    )

    # ---- GV-04 附件A §5.3 归因（000001 2026Q2）----
    YI = 100_000_000.0
    a0, at, ns = 26.44 * YI, 39.38 * YI, -2.4841 * YI
    rn = attribution.r_net_from_identity(a0, at, ns, 0.0)
    # GV-04 是**复现文档示例**（附件A §5.3 直接给出 R_net=15.4241亿），
    # 其残差按定义为 0；这不是生产路径，故抑制「自证」警告。
    import warnings as _w

    from gwfund_core.attribution import SelfProvisionWarning

    with _w.catch_warnings():
        _w.simplefilter("ignore", SelfProvisionWarning)
        wf = attribution.waterfall(a0, ns, rn, 0.0, at)
    vectors.append(
        {
            "id": "GV-04",
            "tier": 1,
            "name": "附件A §5.3 规模归因（000001 2026Q2）",
            "source": "《附件A》§5.3 实测数据",
            "module": "attribution",
            "inputs": {
                "a_start": 26.44 * YI,
                "a_end": 39.38 * YI,
                "net_sub": -2.4841 * YI,
                "div_cash": 0.0,
            },
            "expected": {
                "r_net": 15.4241 * YI,
                "residual": 0.0,
                "residual_pct": 0.0,
            },
            "notes": "瀑布六段，残差必须 0.00%（<0.5% 门禁）",
        }
    )
    return vectors


# ==========================================================================
# Tier-2：回归基线（本版本冻结，防漂移，非独立真值）
# ==========================================================================


def tier2_vectors() -> list[dict]:
    out: list[dict] = []

    # ---- GV-05 年化：T9 不足 1 年不年化 ----
    r30 = returns.annualize(0.10, 30)
    r365 = returns.annualize(0.10, 365)
    r730 = returns.annualize(0.21, 730)
    out.append(
        {
            "id": "GV-05",
            "tier": 2,
            "name": "年化与 T9「不足 1 年不年化」",
            "source": "《附件A》§5.3 M203 + 陷阱 T9",
            "module": "returns",
            "inputs": {"cases": [{"r": 0.10, "days": 30}, {"r": 0.10, "days": 365},
                                {"r": 0.21, "days": 730}]},
            "expected": {
                # annualized 是「是否已年化」的布尔标志；未年化时 value 必须为 None
                "annualized_30": r30.annualized,
                "value_30": r30.value,
                "value_365": _f(r365.value),
                "value_730": _f(r730.value),
            },
            "notes": "30 天涨 10% 不得报出 +1000% 年化；满 1 年才年化",
        }
    )

    # ---- GV-06 闰年与非闰年日费用比 ----
    f26 = fees.daily_fee(1_000_000_000, "1.20", date(2026, 6, 1))
    f24 = fees.daily_fee(1_000_000_000, "1.20", date(2024, 6, 3))
    out.append(
        {
            "id": "GV-06",
            "tier": 2,
            "name": "闰年日费用比 = 365/366",
            "source": "《附件A》§3.1 细节 2",
            "module": "fees",
            "inputs": {"a_prev": 1_000_000_000, "rate_pct": "1.20",
                       "non_leap": "2026-06-01", "leap": "2024-06-03"},
            "expected": {"ratio_non_leap_over_leap": _f(f26 / f24)},
            "notes": "闰年分母变大 → 单日费用变小；年度合计不变",
        }
    )

    # ---- GV-07 混频方案 3 净申购（均匀权重）----
    dates = [date(2026, 4, 1)]
    from datetime import timedelta

    dates = [date(2026, 4, 1) + timedelta(days=i) for i in range(10)]
    navs10 = [1.0 + 0.01 * i for i in range(10)]
    sub, red = 1_000_000.0, 400_000.0
    net_sub = mixing.quarterly_net_sub(sub, red, navs10, dates)
    out.append(
        {
            "id": "GV-07",
            "tier": 2,
            "name": "混频方案 3：季度申赎明细 + 均匀权重",
            "source": "《附件A》§6.2 式(8.4)(8.5)，决策 D20 默认均匀",
            "module": "mixing",
            "inputs": {"sub_shares": sub, "red_shares": red,
                       "navs": navs10, "weight_scheme": "uniform"},
            "expected": {"net_sub": _f(net_sub),
                         "mean_nav": sum(navs10) / len(navs10)},
            "notes": "NetSub 为费前金额；申赎费一律不得冲减（陷阱 T3）",
        }
    )

    # ---- GV-08 追高资金：000001 无追高（V10 判据）----
    YI = 100_000_000.0
    s1 = chase.chase_summary(
        [-1.90 * YI, -0.91 * YI, -1.19 * YI, -1.87 * YI],
        [0.9, 0.95, 1.05, 1.1123, 1.0823, 1.02006, 1.63674],
        theta=0.8,
    )
    out.append(
        {
            "id": "GV-08",
            "tier": 2,
            "name": "追高资金：000001 观测期无追高（V10）",
            "source": "《附件A》§7.2 + §11 V10",
            "module": "chase",
            "inputs": {"net_subs_yi": [-1.90, -0.91, -1.19, -1.87], "theta": 0.8},
            "expected": {"chase_amount": 0.0, "has_chasing": False, "chase_ratio": None},
            "notes": "必须能输出「无追高资金」，不得强行拟合",
        }
    )

    # ---- GV-09 追高资金：正向样例（高位大额申购）----
    s2 = chase.chase_summary(
        [0.0, 0.0, 0.0, 0.0, 0.0, 5.0 * YI], [1.0, 1.1, 1.2, 1.3, 1.4, 1.5], theta=0.8
    )
    out.append(
        {
            "id": "GV-09",
            "tier": 2,
            "name": "追高资金：高位大额申购应被识别",
            "source": "《附件A》§7.2 正向样例要求",
            "module": "chase",
            "inputs": {"net_subs_yi": [0, 0, 0, 0, 0, 5.0], "navs": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
                       "theta": 0.8},
            "expected": {"chase_amount": 5.0 * YI, "chase_ratio": 1.0, "has_chasing": True},
            "notes": "最高位 5 亿净申购，占比 100%",
        }
    )

    # ---- GV-10 规模分档浮动管理费 ----
    tier_aum = [(Decimal("1e10"), "1.20"), (None, "0.60")]
    f150 = fees.daily_fee_by_aum_tier(150 * Decimal("1e8"), date(2026, 6, 1), tier_aum)
    out.append(
        {
            "id": "GV-10",
            "tier": 2,
            "name": "规模分档浮动管理费（150 亿：100 亿@1.20% + 50 亿@0.60%）",
            "source": "《附件A》§3.2 浮动费率·规模分档型",
            "module": "fees",
            "inputs": {"a_prev": 150e8, "tier": [["1e10", "1.20"], [None, "0.60"]],
                       "date": "2026-06-01"},
            "expected": {"daily_fee": _f(f150)},
            "notes": "浮动费率必须逐日模拟，不得用年化平均费率近似",
        }
    )
    # ---- GV-11 最大回撤（M205）----
    from gwfund_core import metrics

    dd = metrics.max_drawdown([1.0, 1.2, 0.9, 1.1])
    out.append(
        {
            "id": "GV-11",
            "tier": 2,
            "name": "最大回撤 M205（先峰后谷）",
            "source": "《附件A》§5.3 M205",
            "module": "metrics",
            "inputs": {"equity_curve": [1.0, 1.2, 0.9, 1.1]},
            "expected": {"mdd": 0.25, "peak_index": 1, "trough_index": 2},
            "notes": "净值 1.2 → 0.9，回撤 1 − 0.9/1.2 = 25%",
        }
    )

    # ---- GV-12 持有体验（M307）----
    navs_up = [1.0 + 0.001 * i for i in range(300)]
    he = metrics.holding_experience(navs_up, window=250)
    out.append(
        {
            "id": "GV-12",
            "tier": 2,
            "name": "持有体验分布 M307（单调上涨 → 胜率 100%）",
            "source": "《附件A》§5.3 M307",
            "module": "metrics",
            "inputs": {"navs": navs_up, "window": 250},
            "expected": {
                "samples": 300 - 250,
                "win_rate": 1.0,
            },
            "notes": "「随便哪天买、拿 250 个交易日」的胜率——与净值收益率是两回事",
        }
    )
    return out


def build() -> dict:
    from gwfund_core import __version__

    vectors = tier1_vectors() + tier2_vectors() + tier2_generic()
    t1 = sum(1 for v in vectors if v["tier"] == 1)
    t2 = len(vectors) - t1
    return {
        "schema_version": "1.1.0",
        "generated_at": "2026-11-21T18:40:00+08:00",
        "core_version": __version__,
        "spec_version": "附件A v1.1（含 SPEC-GAP-01~05）/ SOW v1.1",
        "tolerance": TOLERANCE,
        "purpose": "四端一致性对拍（SOW §7.3 C12 / 附件C §13）；差异 > tolerance 阻断 CI",
        "tier_legend": {
            "1": "手算基线——期望值来自附件A 手算样例或独立解析解，不来自引擎",
            "2": "回归基线——期望值在本版本由引擎冻结，用于防止无意行为漂移，非独立真值",
        },
        "counts": {"tier1": t1, "tier2": t2, "total": len(vectors), "target_M1": 50},
        "vectors": vectors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成/校验黄金向量契约")
    ap.add_argument("--check", action="store_true", help="只校验现有文件是否为最新")
    args = ap.parse_args(argv)

    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not OUT.exists():
            print(f"[FAIL] {OUT} 不存在")
            return 1
        current = OUT.read_text(encoding="utf-8")
        if current.strip() != text.strip():
            print(f"[FAIL] {OUT} 与引擎当前输出不一致——存在行为漂移，请先确认再重新生成")
            return 1
        print(f"[PASS] {OUT} 与引擎一致（{len(data['vectors'])} 组向量）")
        return 0

    OUT.write_text(text, encoding="utf-8")
    t1 = sum(1 for v in data["vectors"] if v["tier"] == 1)
    t2 = len(data["vectors"]) - t1
    print(f"[OK] 写出 {OUT}")
    print(f"     向量 {len(data['vectors'])} 组（Tier-1 手算 {t1} / Tier-2 回归 {t2}），容差 {TOLERANCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
