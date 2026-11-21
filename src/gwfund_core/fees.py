"""日度费用计提（管理费 / 托管费 / 销售服务费 / 浮动费率 / 交易费用）。

对应《附件A 算法引擎计算规范》：

* §3.1 式(3.1)(3.2)(3.3)  三类固定费率逐日计提
* §3.1 式(3.4)            累计期间费用
* §3.2                    费率表结构与浮动费率基金（业绩报酬型 / 规模分档型）
* §3.3 式(3.5)(3.6)(3.7)  交易费用估算、换手率、年报校准
* §3.5                    手算样例（100 亿 / 1.20% / 0.20%，闰年对照）

**三条关键实现细节（缺一即错，附件A §3.1）**：

1. 分母用 ``A_{t-1}``（前一交易日资产净值）**而不是** ``A_t``；
2. 闰年分母 366，其余 365——硬编码 365 会在闰年**高估**费用 0.27%；
3. 年化费率转日费率是**线性除**（``rate / D_y``），不是复利开方。
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .types import FeeAccrual

# 金额量化到「分」（SOW §5.8：金额内部 Decimal，展示时舍入）
CENT = Decimal("0.01")

# 年化费率百分数 → 小数
_HUNDRED = Decimal(100)

# 交易成本默认参数（附件A §3.3，仅作起点，**不得当作事实**）
COMMISSION_PCT = Decimal("0.08")  # 佣金，双边，万八
STAMP_DUTY_PCT = Decimal("0.05")  # 印花税，卖出单边（2023-08-28 起）
TRANSFER_FEE_PCT = Decimal("0.001")  # 过户费，双边
IMPACT_TIERS_PCT = (  # 冲击成本按季末 AUM 分档（附件A §3.3）
    (Decimal("1e9"), Decimal("0.05")),  # < 10 亿元
    (Decimal("5e9"), Decimal("0.10")),  # 10–50 亿元
    (Decimal("2e10"), Decimal("0.20")),  # 50–200 亿元
    (None, Decimal("0.30")),  # > 200 亿元
)


def _d(value: Decimal | int | float | str) -> Decimal:
    """任何数值 → Decimal（走 str 避免 float 二进制误差）。"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def days_in_year(on: date) -> int:
    """该自然年的计息天数 ``D_y(t)``，附件A §3.1。

    闰年 366，否则 365。闰年判定用 ``calendar.isleap``，
    避免手写 ``% 4 / % 100 / % 400`` 出错。
    """
    return 366 if calendar.isleap(on.year) else 365


# --------------------------------------------------------------------------
# 固定费率三类（附件A §3.1）
# --------------------------------------------------------------------------


def daily_fee(
    a_prev: Decimal | int | float | str,
    rate_pct: Decimal | int | float | str,
    on: date,
) -> Decimal:
    """单类费用的单日计提额，附件A §3.1 式(3.1)/(3.2)/(3.3)。

    ``F_t = A_{t-1} × (rate% / 100) / D_y(t)``

    Args:
        a_prev: **前一交易日**资产净值 ``A_{t-1}``，元。
            传当日 ``A_t`` 会造成「费用随当日涨跌同向变动」，且在净值下跌日低估费用。
        rate_pct: 年化费率，百分数（``1.20`` 表示 1.20%）。
        on: 计提日。

    Returns:
        当日费用，Decimal 元，量化到分（ROUND_HALF_UP）。
    """
    a = _d(a_prev)
    rate = _d(rate_pct)
    if a < 0:
        raise ValueError(f"资产净值不得为负：{a}")
    dy = Decimal(days_in_year(on))
    return (a * rate / _HUNDRED / dy).quantize(CENT, rounding=ROUND_HALF_UP)


def daily_accrual(
    a_prev: Decimal | int | float | str,
    on: date,
    rate_mgmt_pct: Decimal | int | float | str = 0,
    rate_custody_pct: Decimal | int | float | str = 0,
    rate_sales_pct: Decimal | int | float | str = 0,
) -> FeeAccrual:
    """三类固定费率费用的单日计提，附件A §3.1。

    Returns:
        :class:`FeeAccrual`，含 mgmt / custody / sales 与 ``total``。
    """
    return FeeAccrual(
        on=on,
        a_prev=_d(a_prev),
        mgmt=daily_fee(a_prev, rate_mgmt_pct, on),
        custody=daily_fee(a_prev, rate_custody_pct, on),
        sales=daily_fee(a_prev, rate_sales_pct, on),
    )


def cumulative_fee(accruals: Iterable[FeeAccrual]) -> dict[str, Decimal]:
    """累计期间费用，附件A §3.1 式(3.4)。

    Returns:
        ``{"mgmt": ..., "custody": ..., "sales": ..., "total": ...}``，均为 Decimal 元。
    """
    mgmt = custody = sales = Decimal(0)
    for a in accruals:
        mgmt += a.mgmt
        custody += a.custody
        sales += a.sales
    return {
        "mgmt": mgmt,
        "custody": custody,
        "sales": sales,
        "total": mgmt + custody + sales,
    }


# --------------------------------------------------------------------------
# 浮动费率（附件A §3.2）
# --------------------------------------------------------------------------


def daily_fee_by_aum_tier(
    a_prev: Decimal | int | float | str,
    on: date,
    tier_aum: Sequence[tuple[Decimal | int | float | str, Decimal | int | float | str]],
) -> Decimal:
    """规模分档型浮动管理费的单日计提，附件A §3.2。

    ``F_mgmt,t = [min(A, θ)·rate_1 + max(0, A − θ)·rate_2] / D_y``

    Args:
        a_prev: 前一交易日资产净值 ``A_{t-1}``。
        on: 计提日。
        tier_aum: 分档定义，形如 ``[(threshold_元, rate_pct), ..., (None, rate_pct)]``，
            最后一个阈值为 ``None`` 表示「超出全部以上档位」。
            构造示例（100 亿以下 1.20%，超出部分 0.60%）：
            ``[(Decimal("1e10"), "1.20"), (None, "0.60")]``。

    Returns:
        当日管理费，Decimal 元。

    Raises:
        ValueError: 分档未以 ``None`` 收尾。
    """
    a = _d(a_prev)
    if a < 0:
        raise ValueError(f"资产净值不得为负：{a}")
    if not tier_aum or tier_aum[-1][0] is not None:
        raise ValueError("tier_aum 必须以 (None, rate_pct) 收尾，否则超出部分无费率")

    total = Decimal(0)
    lower = Decimal(0)
    for threshold, rate_pct in tier_aum:
        if threshold is None:
            portion = max(Decimal(0), a - lower)
        else:
            th = _d(threshold)
            portion = min(a, th) - lower
            if portion < 0:
                portion = Decimal(0)
        total += portion * _d(rate_pct) / _HUNDRED
        if threshold is not None:
            lower = _d(threshold)
        if threshold is not None and a <= lower:
            break
    return (total / Decimal(days_in_year(on))).quantize(CENT, rounding=ROUND_HALF_UP)


def performance_fee_daily(
    a_prev: Decimal | int | float | str,
    nav_prev: float,
    hwm_prev: float,
    hurdle: float,
    share_pct: float,
    on: date,
) -> tuple[Decimal, float]:
    """业绩报酬型浮动管理费的单日计提，附件A §3.2。

    ``F_perf,t = max(0, (N_t − HWM_{t−1}) / HWM_{t−1} − hurdle) × x% × A_{t−1}``

    Args:
        a_prev: 前一交易日资产净值。
        nav_prev: 当日单位净值 ``N_t``。
        hwm_prev: 上一计提日的高水位 ``HWM_{t−1}``。
        hurdle: 门槛收益率（小数，``0.05`` 表示 5%）。
        share_pct: 超额收益分成比例（百分数，``20`` 表示 20%）。
        on: 计提日。

    Returns:
        ``(当日业绩报酬 Decimal 元, 更新后的高水位)``。

    Note:
        附件A §3.2 明确：浮动费率**必须逐日模拟**，不允许用「年化平均费率」近似，
        因为高水位是路径依赖的，典型偏差 0.1–0.5 pct/年。
    """
    if hwm_prev is None or hwm_prev <= 0:
        raise ValueError(f"高水位必须为正：{hwm_prev}")
    excess = nav_prev / hwm_prev - 1.0 - hurdle
    if excess <= 0:
        return Decimal(0).quantize(CENT), hwm_prev
    fee = _d(a_prev) * _d(excess) * _d(share_pct) / _HUNDRED
    new_hwm = nav_prev if nav_prev > hwm_prev else hwm_prev
    return fee.quantize(CENT, rounding=ROUND_HALF_UP), new_hwm


# --------------------------------------------------------------------------
# 交易费用（附件A §3.3）
# --------------------------------------------------------------------------


def impact_cost_pct(aum: Decimal | int | float | str) -> Decimal:
    """按季末 AUM 取冲击成本费率（百分数），附件A §3.3 分档表。"""
    a = _d(aum)
    for threshold, rate in IMPACT_TIERS_PCT:
        if threshold is None or a < threshold:
            return rate
    return IMPACT_TIERS_PCT[-1][1]


def trade_cost_rate_pct(
    aum: Decimal | int | float | str,
    *,
    commission_pct: Decimal | int | float | str = COMMISSION_PCT,
    include_stamp_duty: bool = True,
    include_transfer: bool = True,
) -> Decimal:
    """单边交易成本率（百分数），附件A §3.3 式(3.5) 的括号项。

    ``c_comm + c_stamp·1[卖出] + c_trans + c_impact``
    """
    rate = _d(commission_pct)
    if include_stamp_duty:
        rate += STAMP_DUTY_PCT
    if include_transfer:
        rate += TRANSFER_FEE_PCT
    rate += impact_cost_pct(aum)
    return rate


def trade_fee_from_turnover(
    turnover: Decimal | int | float | str,
    a_prev: Decimal | int | float | str,
    aum: Decimal | int | float | str,
    *,
    commission_pct: Decimal | int | float | str = COMMISSION_PCT,
    include_stamp_duty: bool = True,
) -> Decimal:
    """按换手率估算交易费用，附件A §3.3 式(3.5)。

    ``F_trade,t = Turnover_t × A_{t−1} × 单边成本率``

    Warning:
        该值是**估算**，必须标注估算等级，并用年报披露值校准（式 3.7）。
        单边/双边口径：``turnover`` 定义为「单边换手率」，若上游给的是双边，
        调用方须先除以 2——本函数不做隐式换算。
    """
    a = _d(a_prev)
    tur = _d(turnover)
    rate = trade_cost_rate_pct(
        aum, commission_pct=commission_pct, include_stamp_duty=include_stamp_duty
    )
    return (tur * a * rate / _HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)


def implied_cost_rate(
    annual_trade_fee: Decimal | int | float | str,
    turnover_aum_sum: Decimal | int | float | str,
) -> Decimal:
    """用年报「交易费用」反算隐含成本率，附件A §3.3 式(3.7)。

    ``c_implied = F_trade^annual / Σ_t (Turnover_t × A_{t−1})``

    Returns:
        隐含成本率（**小数**，非百分数），供与 ``c_assumed`` 比较：
        偏差 > 30% 时以年报为准重算并记录校准系数。
    """
    denom = _d(turnover_aum_sum)
    if denom <= 0:
        raise ValueError("Σ(Turnover × A_prev) 必须为正，无法反算隐含成本率")
    return _d(annual_trade_fee) / denom


def calibration_factor(
    implied_rate: Decimal | int | float | str,
    assumed_rate: Decimal | int | float | str,
) -> float:
    """校准系数 ``c_implied / c_assumed``，用于把估算值缩放到年报口径。"""
    assumed = _d(assumed_rate)
    if assumed == 0:
        raise ValueError("假设成本率不得为 0")
    return float(_d(implied_rate) / assumed)


def needs_recalibration(
    implied_rate: Decimal | int | float | str,
    assumed_rate: Decimal | int | float | str,
    *,
    threshold: float = 0.30,
) -> bool:
    """是否触发年报校准重算（附件A §3.3：偏差 > 30%）。"""
    implied = _d(implied_rate)
    assumed = _d(assumed_rate)
    if assumed == 0:
        return True
    return abs(float(implied / assumed) - 1.0) > threshold


# --------------------------------------------------------------------------
# 申购/赎回费分档（附件A §3.2，闭区间语义）
# --------------------------------------------------------------------------


def lookup_tier_rate(
    tier: Sequence[dict],
    holding_days: int,
) -> Decimal:
    """按持有期查分档费率（百分数），附件A §3.2。

    分档边界为**闭区间**，与披露原文一致，即
    ``{"holding_days": [0, 6], "rate_pct": 1.50}`` 表示 ``0 <= days <= 6``。

    Args:
        tier: 形如 ``[{"holding_days": [0, 6], "rate_pct": 1.50}, ...,
            {"holding_days": [730, None], "rate_pct": 0.00}]``。
            上界 ``None`` 表示无上限。
        holding_days: 实际持有天数。

    Returns:
        对应档位费率（百分数）。

    Raises:
        ValueError: 无匹配档位（说明费率表不完整，不得静默返回 0）。

    Note:
        实测 ``110022`` 赎回费率 4 档：
        ``<=6 天 1.50% / 7–364 天 0.50% / 365–729 天 0.25% / >=730 天 0.00%``。
        代码必须用 ``>=`` / ``<=``，不能想当然用 ``>``。
    """
    if holding_days < 0:
        raise ValueError(f"持有天数不得为负：{holding_days}")
    for row in tier:
        lo, hi = row["holding_days"]
        lo_ok = holding_days >= lo
        hi_ok = hi is None or holding_days <= hi
        if lo_ok and hi_ok:
            return _d(row["rate_pct"])
    raise ValueError(
        f"持有 {holding_days} 天在费率分档中无匹配项——费率表不完整，不得静默返回 0"
    )
