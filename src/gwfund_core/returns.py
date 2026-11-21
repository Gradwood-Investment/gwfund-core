"""收益率：单期净收益、TWR（时间加权）、MWR（资金加权 / IRR）、缺口。

对应《附件A 算法引擎计算规范》：

* §1.2 式(1.2)  单期净收益率 ``r_t = (N_t + d_t)/N_{t-1} - 1``
* §4.5 式(5.1)  TWR 链式连乘
* §4.5 式(5.2)  MWR = IRR（牛顿法 + 二分法双保险）
* §4.5 式(5.3)  基民收益缺口 ``Gap = TWR - MWR``
* §4.6          TWR / MWR 实现要求（多解检测、短期间不年化）
* §9  T9        成立不满一年不得年化（不得出现 +1000% 年化）
* §9  T11       净值 4 位小数舍入累积——长区间用首尾复权净值，不用日收益连乘

**铁律（附件A §2.1）**：净值是费后净值，``r_t`` 已是净收益率。
禁止再写 ``net_return = twr - fee_rate``——那是二次扣费，会把收益低估约 1.4 pct/年。
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass

from .errors import InsufficientDataError, MwrMultiRootWarning
from .types import MwrResult

# IRR 根的可行区间（附件A §4.6：「区间 [-0.999, 10]」）
ROOT_LOW = -0.999
ROOT_HIGH = 10.0

# 多解扫描网格点数（在 log(1+r) 空间均匀取样，覆盖 [-0.999, 10]）
_SCAN_POINTS = 2000


# --------------------------------------------------------------------------
# 单期与链式收益
# --------------------------------------------------------------------------


def daily_return(
    nav_prev: float,
    nav_curr: float,
    div_per_share: float = 0.0,
) -> float | None:
    """单期净收益率，附件A §1.2 式(1.2)。

    ``r_t = (N_t + d_t) / N_{t-1} - 1``

    Args:
        nav_prev: 上一期单位净值 ``N_{t-1}``（费后），元/份。
        nav_curr: 本期单位净值 ``N_t``，元/份。
        div_per_share: 本期每份现金分红 ``d_t``（**除息日计**），元/份。

    Returns:
        单期收益率（小数）；``nav_prev <= 0`` 时返回 ``None``——
        附件A §5.8「所有百分比计算的分母不得为 0，须返回 ``null`` 而非 0」。
    """
    if nav_prev is None or nav_curr is None:
        return None
    if nav_prev <= 0:
        return None
    return (nav_curr + div_per_share) / nav_prev - 1.0


def chain_return(period_returns: Sequence[float]) -> float:
    """把一串单期收益率链式连乘为区间收益率，附件A §4.5 式(5.1)。

    ``TWR = ∏(1 + r_t) - 1``

    Note:
        §4.6 要求 TWR「必须用日频 r_t」，不得用区间首尾净值直接相除。
        但对**长区间**，式(5.1) 的连乘会累积净值 4 位小数舍入误差（T11），
        因此长区间应改用 :func:`twr_from_adjusted_nav`。
    """
    acc = 1.0
    for r in period_returns:
        acc *= 1.0 + r
    return acc - 1.0


def adjusted_nav_series(
    navs: Sequence[float],
    divs: Sequence[float] | None = None,
) -> list[float]:
    """构造复权净值序列（指标 M103）。

    复权口径（决策 D07）：分红按除权日**再投连乘**，即
    ``A_t = A_{t-1} × (1 + r_t)``，``A_0 = N_0``。

    Args:
        navs: 单位净值序列，按日期升序。
        divs: 与 ``navs`` 等长的每份现金分红序列；``None`` 视为全 0。

    Returns:
        与 ``navs`` 等长的复权净值序列。

    Raises:
        InsufficientDataError: 首期净值 <= 0，无法作为基准。
    """
    if not navs:
        raise InsufficientDataError("NAV_EMPTY", "净值序列为空")
    if navs[0] <= 0:
        raise InsufficientDataError("NAV_NONPOSITIVE", f"首期净值 {navs[0]} <= 0")
    if divs is None:
        divs = [0.0] * len(navs)
    if len(divs) != len(navs):
        raise ValueError(f"navs 与 divs 长度不一致：{len(navs)} vs {len(divs)}")

    out = [navs[0]]
    for i in range(1, len(navs)):
        r = daily_return(navs[i - 1], navs[i], divs[i])
        if r is None:
            # 分母异常：保持平推，不制造虚假收益（缺失 ≠ 0）
            out.append(out[-1])
        else:
            out.append(out[-1] * (1.0 + r))
    return out


def twr_from_adjusted_nav(adj_start: float, adj_end: float) -> float:
    """用复权净值首尾算区间 TWR（T11：长区间首选此法，避免舍入累积）。

    ``TWR = A_T / A_0 - 1``

    Note:
        该方法与 :func:`chain_return` 数学等价（因为复权净值本身就是 r_t 的连乘），
        但只有一次除法，不会累积逐日舍入误差。
    """
    if adj_start is None or adj_start <= 0:
        raise InsufficientDataError("NAV_NONPOSITIVE", f"复权净值基准 {adj_start} <= 0")
    return adj_end / adj_start - 1.0


def twr_consistency_gap(chain: float, endpoint: float) -> float:
    """两种 TWR 口径的差异绝对值，用于 T11 自检（要求 < 0.1%）。"""
    return abs(chain - endpoint)


def dividend_from_acc_nav(
    acc_prev: float,
    acc_curr: float,
    unit_prev: float,
    unit_curr: float,
) -> float:
    """由「累计净值 − 单位净值」的差分还原每份现金分红 ``d_t``。

    ``d_t = (N^acc_t − N^acc_{t−1}) − (N_t − N_{t−1})``

    原理：累计净值 = 单位净值 + 累计分红（只做加法，不做再投，决策 D07），
    因此两者的**增量之差**就是当日的每份派现额。

    Args:
        acc_prev: 上一期累计净值 ``N^acc_{t-1}``。
        acc_curr: 本期累计净值 ``N^acc_t``。
        unit_prev: 上一期单位净值 ``N_{t-1}``。
        unit_curr: 本期单位净值 ``N_t``。

    Returns:
        每份现金分红 ``d_t``，元/份（无分红时为 0.0）。
        浮点噪声按 1e-9 归零，避免把舍入差异当成分红。

    Note:
        这是**分红金额的唯一可用来源**：东财 ``pingzhongdata`` 的
        ``unitMoney`` 字段金额已被服务端剥离（只剩「每10份派现金元」这样的空模板），
        而 ``acc_nav`` 保留完整数值（《附件B》已实测）。
        因此该推导是「现金分红总额 ``Div_cash``」口径的唯一入口。

    实测校验（000001，2026-11-21）：
        推出 25 笔分红、合计 **2.5730 元/份**，与《附件A》§8.2 对账样例
        「25 笔，合计 2.573 元/份」逐位一致。
    """
    if None in (acc_prev, acc_curr, unit_prev, unit_curr):
        return 0.0
    d = (acc_curr - acc_prev) - (unit_curr - unit_prev)
    return 0.0 if abs(d) < 1e-9 else d


def derive_dividend_series(
    acc_navs: Sequence[float],
    unit_navs: Sequence[float],
) -> list[float]:
    """整条序列的每份分红，返回与输入等长的序列（首期为 0.0）。

    Args:
        acc_navs: 累计净值序列（升序）。
        unit_navs: 单位净值序列（升序），长度须与 ``acc_navs`` 一致。

    Returns:
        ``d_t`` 序列，首期为 ``0.0``（无前一期可比）。

    Raises:
        ValueError: 两序列长度不一致。

    Note:
        ⚠️ 该序列**不等于**复权净值。累计净值只做加法，复权净值做再投连乘
        （决策 D07）——对有分红的基金两者会显著不同。
        实测 000001 全区间：复权净值末值 6.0951 vs 累计净值 3.5100。
        **算业绩只能用复权净值**。
    """
    if len(acc_navs) != len(unit_navs):
        raise ValueError(f"长度不一致：{len(acc_navs)} vs {len(unit_navs)}")
    out = [0.0] * len(acc_navs)
    for i in range(1, len(acc_navs)):
        out[i] = dividend_from_acc_nav(
            acc_navs[i - 1], acc_navs[i], unit_navs[i - 1], unit_navs[i]
        )
    return out


# --------------------------------------------------------------------------
# IRR / MWR
# --------------------------------------------------------------------------


def _npv(rate: float, cashflows: Sequence[float], times: Sequence[float]) -> float:
    """净现值 ``Σ CF_t / (1+rate)^t``。"""
    base = 1.0 + rate
    total = 0.0
    for cf, t in zip(cashflows, times):
        if t == 0.0:
            total += cf
        else:
            total += cf / (base**t)
    return total


def _npv_derivative(rate: float, cashflows: Sequence[float], times: Sequence[float]) -> float:
    """NPV 对 rate 的一阶导 ``Σ -t·CF_t / (1+rate)^(t+1)``。"""
    base = 1.0 + rate
    total = 0.0
    for cf, t in zip(cashflows, times):
        if t == 0.0:
            continue
        total += -t * cf / (base ** (t + 1.0))
    return total


def _bisect(
    cashflows: Sequence[float],
    times: Sequence[float],
    lo: float,
    hi: float,
    tol: float,
    max_iter: int = 200,
) -> float | None:
    """在 ``[lo, hi]`` 上二分求 NPV=0 的根；端点同号则返回 ``None``。"""
    f_lo = _npv(lo, cashflows, times)
    f_hi = _npv(hi, cashflows, times)
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = _npv(mid, cashflows, times)
        if abs(f_mid) < tol or (hi - lo) / 2.0 < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


def _newton(
    cashflows: Sequence[float],
    times: Sequence[float],
    start: float,
    tol: float,
    max_iter: int = 100,
) -> tuple[float | None, int]:
    """牛顿法求根；失败（导数近 0 / 越界 / 不收敛）返回 ``(None, iterations)``。"""
    rate = start
    for it in range(1, max_iter + 1):
        f = _npv(rate, cashflows, times)
        if abs(f) < tol:
            return rate, it
        df = _npv_derivative(rate, cashflows, times)
        if abs(df) < 1e-14 or not math.isfinite(df):
            return None, it
        nxt = rate - f / df
        if not math.isfinite(nxt) or nxt <= -1.0 or nxt > ROOT_HIGH * 10:
            return None, it
        if abs(nxt - rate) < tol:
            return nxt, it
        rate = nxt
    return None, max_iter


def _scan_roots(
    cashflows: Sequence[float],
    times: Sequence[float],
    tol: float,
) -> list[float]:
    """在 ``(ROOT_LOW, ROOT_HIGH]`` 上扫描全部根（附件A §4.6 多解检测）。"""
    lo_log = math.log1p(ROOT_LOW)  # log(1 + (-0.999)) = log(0.001)
    hi_log = math.log1p(ROOT_HIGH)
    rates: list[float] = []
    prev_rate = ROOT_LOW
    prev_f = _npv(prev_rate, cashflows, times)
    for i in range(1, _SCAN_POINTS + 1):
        log_u = lo_log + (hi_log - lo_log) * i / _SCAN_POINTS
        rate = math.expm1(log_u)
        f = _npv(rate, cashflows, times)
        if prev_f == 0.0:
            rates.append(prev_rate)
        elif prev_f * f < 0:
            root = _bisect(cashflows, times, prev_rate, rate, tol)
            if root is not None:
                rates.append(root)
        prev_rate, prev_f = rate, f
    if prev_f == 0.0:
        rates.append(prev_rate)
    return _dedupe(rates, tol=1e-7)


def _dedupe(values: Sequence[float], tol: float = 1e-7) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
    return out


def irr(
    cashflows: Sequence[float],
    times: Sequence[float] | None = None,
    *,
    tol: float = 1e-12,
    scan_for_multiple_roots: bool = True,
) -> MwrResult:
    """求内部收益率 IRR（= MWR 的区间值），附件A §4.6。

    现金流符号约定（附件A §4.6「现金流符号」）：
        **投资为负、收回为正**。``CF_0 = -A_0``；期间申赎 ``CF_t = -NetSub_t``；
        期末 ``CF_T = +A_T + Div^cash``。

    Args:
        cashflows: 现金流序列，第 0 个为 ``t=0`` 时刻。
        times: 与 ``cashflows`` 等长的时间坐标（单位：年）。
            ``None`` 视为等间隔（0, 1, 2, ...）。
        tol: 收敛容差（对 NPV 的绝对容差，按金额量级归一化后使用）。
        scan_for_multiple_roots: 是否扫描多解。V3 黄金用例建议开启。

    Returns:
        :class:`MwrResult`；``value`` 为区间 IRR，``annualized`` 由调用方决定
        （本函数不知道区间长度，因此恒为 ``None``）。

    Raises:
        InsufficientDataError: 现金流为空或全部同号（IRR 不存在）。
    """
    cfs = [float(c) for c in cashflows]
    if len(cfs) < 2:
        raise InsufficientDataError("CF_TOO_SHORT", "至少需要 2 笔现金流")
    if not (any(c > 0 for c in cfs) and any(c < 0 for c in cfs)):
        raise InsufficientDataError(
            "CF_SAME_SIGN",
            "现金流全部同号，IRR 不存在（投资须为负、收回须为正）",
        )
    ts = [float(t) for t in times] if times is not None else [float(i) for i in range(len(cfs))]
    if len(ts) != len(cfs):
        raise ValueError(f"times 与 cashflows 长度不一致：{len(ts)} vs {len(cfs)}")

    # 容差按金额量级归一化：金额单位是「元」，1e-12 元没有意义，用相对量级
    scale = max(1.0, sum(abs(c) for c in cfs))
    eff_tol = tol * scale

    root, iters = _newton(cfs, ts, 0.0, eff_tol)
    method = "newton"
    if root is None:
        root = _bisect(cfs, ts, ROOT_LOW, ROOT_HIGH, eff_tol)
        method = "bisection"
        iters = 0
    if root is None:
        raise InsufficientDataError(
            "IRR_NO_SOLUTION", f"在 [{ROOT_LOW}, {ROOT_HIGH}] 内未找到根"
        )

    roots: list[float] = [root]
    if scan_for_multiple_roots:
        roots = _scan_roots(cfs, ts, eff_tol) or [root]
    multi = len(roots) > 1
    if multi:
        warnings.warn(
            f"MWR_MULTI_ROOT：检测到 {len(roots)} 个根 {[round(r, 6) for r in roots]}，"
            f"已取最接近 0 者（附件A §4.6）",
            MwrMultiRootWarning,
            stacklevel=2,
        )
        root = min(roots, key=abs)

    return MwrResult(
        value=root,
        annualized=None,
        multi_root=multi,
        roots=tuple(roots),
        iterations=iters,
        method=method,
    )


def mwr_from_flows(
    cashflows: Sequence[float],
    times: Sequence[float] | None = None,
    *,
    period_days: float | None = None,
) -> MwrResult:
    """MWR 求解 + 可选年化（附件A §4.5 式(5.2)、§4.6「短期间」）。

    Args:
        cashflows: 见 :func:`irr`。
        times: 见 :func:`irr`。
        period_days: 区间实际日历天数。``None`` 或 < 365 时**不年化**（T9）。

    Returns:
        :class:`MwrResult`，``annualized`` 在没有年化时为 ``None``。
    """
    res = irr(cashflows, times)
    annualized: float | None = None
    if period_days is not None and period_days >= 365.0:
        base = 1.0 + res.value
        if base > 0:
            annualized = base ** (365.0 / period_days) - 1.0
    return MwrResult(
        value=res.value,
        annualized=annualized,
        multi_root=res.multi_root,
        roots=res.roots,
        iterations=res.iterations,
        method=res.method,
    )


# --------------------------------------------------------------------------
# 缺口与年化
# --------------------------------------------------------------------------


def investor_gap(twr: float, mwr: float) -> float:
    """基民收益缺口（指标 M301），附件A §4.5 式(5.3)。

    ``Gap = TWR - MWR``，单位是**百分点**（返回值仍为小数，×100 即 pct）。

    ``Gap > 0`` 表示基民因择时（多为追高）跑输基金本身。

    Warning:
        UI 必须并列展示 TWR、MWR 与样本区间，**禁止单独展示 MWR**（SOW §5.4）。
    """
    return twr - mwr


@dataclass(frozen=True)
class AnnualizeResult:
    value: float | None
    annualized: bool
    reason: str | None


def annualize(period_return: float, days: float, *, min_days: float = 365.0) -> AnnualizeResult:
    """区间收益年化，附件A §5.3 M203 与陷阱 T9。

    ``年化 = (1 + R)^(365/天数) - 1``

    区间**不足 1 年不年化**——否则 30 天涨 10% 会变成 +1000% 的荒唐年化（T9）。

    Args:
        period_return: 区间收益率（小数）。
        days: 区间实际日历天数。
        min_days: 起年化门槛，默认 365 天。

    Returns:
        :class:`AnnualizeResult`；未年化时 ``value`` 为 ``None``、
        ``reason`` 说明原因（UI 必须显示该原因）。
    """
    if days is None or days <= 0:
        return AnnualizeResult(None, False, "区间天数无效")
    if days < min_days:
        return AnnualizeResult(None, False, f"区间仅 {days:.0f} 天，不足 1 年不年化")
    base = 1.0 + period_return
    if base <= 0:
        return AnnualizeResult(None, False, "区间亏损 100%，无法年化")
    return AnnualizeResult(base ** (365.0 / days) - 1.0, True, None)


def annualize_by_periods(period_return: float, periods_per_year: float) -> float:
    """按「期数/年」年化——仅用于等间隔抽样样例（如附件A §4.5 的 3 期 = 1 季）。

    ``年化 = (1 + R)^(periods_per_year) - 1``

    Note:
        这是**展示约定**，不是日历口径。生产区间收益请用 :func:`annualize`。
    """
    if periods_per_year <= 0:
        raise ValueError("periods_per_year 必须为正")
    base = 1.0 + period_return
    if base <= 0:
        raise InsufficientDataError("TOTAL_LOSS", "区间亏损 100%，无法年化")
    return base**periods_per_year - 1.0
