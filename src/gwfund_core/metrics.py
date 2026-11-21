"""绩效与体验指标：M204–M208（风险收益）、M306/M307/M310/M313（费用与体验）。

本模块补齐《裁定书二》§3.1 列出的 9 项「规范有、代码无」指标。
按 MC-01（算法只有一份），这些必须进 core，**不得在各端各写一遍**。

对应《附件A》§5.3 指标字典：

=====  ========================  ============================================
ID     指标                      公式
=====  ========================  ============================================
M204   年化波动率                ``std(r) × √252``
M205   最大回撤                  ``max(1 − NAV_t / max_{s≤t} NAV_s)``
M206   夏普比率                  ``(年化收益 − 无风险) / 年化波动``
M207   索提诺比率                超额收益 / 下行波动
M208   卡玛比率                  年化收益 / 最大回撤
M306   每元管理费对应基民盈亏    ``P_inv ÷ 累计管理费``
M307   持有体验分布              滚动 N 日收益的分布、胜率、最差 5%
M310   费用率                    ``Fee ÷ 区间平均 AUM``（年化）
M313   换手率                    披露值优先；无披露用式(3.6) 估算
=====  ========================  ============================================

**缺失值纪律**（附件A §2.2 / §5.8）：分母为 0 或样本不足时返回 ``None``，
**禁止返回 0**——0 是「算出来是零」，``None`` 是「算不出来」，两者含义完全不同。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal

from .errors import InsufficientDataError

TRADING_DAYS_PER_YEAR = 252
"""A 股年均交易日数，用于波动率年化（附件A §5.3 M204）。"""

_CENT = Decimal("0.01")


def _d(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float], *, ddof: int = 1) -> float | None:
    """样本标准差；样本量 <= ddof 时返回 ``None``（不得返回 0）。"""
    n = len(xs)
    if n <= ddof:
        return None
    mu = _mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / (n - ddof)
    return math.sqrt(var)


# --------------------------------------------------------------------------
# M204 年化波动率
# --------------------------------------------------------------------------


def annualized_volatility(
    period_returns: Sequence[float],
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """年化波动率 M204，附件A §5.3。

    ``σ_ann = std(r) × √(periods_per_year)``

    Args:
        period_returns: 单期净收益率序列（日频）。
        periods_per_year: 每年期数，日频默认 252。

    Returns:
        年化波动率（小数）；样本不足 2 个时返回 ``None``。
    """
    sd = _stdev(list(period_returns))
    if sd is None:
        return None
    return sd * math.sqrt(periods_per_year)


# --------------------------------------------------------------------------
# M205 最大回撤
# --------------------------------------------------------------------------


class DrawdownResult:
    """最大回撤结果（M205），带峰谷定位便于下钻。"""

    __slots__ = ("mdd", "peak_index", "trough_index", "peak_value", "trough_value")

    def __init__(
        self,
        mdd: float,
        peak_index: int,
        trough_index: int,
        peak_value: float,
        trough_value: float,
    ) -> None:
        self.mdd = mdd
        self.peak_index = peak_index
        self.trough_index = trough_index
        self.peak_value = peak_value
        self.trough_value = trough_value

    def __repr__(self) -> str:  # pragma: no cover - 便于排障
        return (
            f"DrawdownResult(mdd={self.mdd:.6%}, peak#{self.peak_index}={self.peak_value}, "
            f"trough#{self.trough_index}={self.trough_value})"
        )


def max_drawdown(equity_curve: Sequence[float]) -> DrawdownResult | None:
    """最大回撤 M205，附件A §5.3。

    ``MDD = max_t (1 − NAV_t / max_{s≤t} NAV_s)``

    Args:
        equity_curve: **复权净值**序列（不是累计净值——决策 D07）。

    Returns:
        :class:`DrawdownResult`；序列不足 2 点或存在非正值时返回 ``None``。
    """
    if not equity_curve or len(equity_curve) < 2:
        return None
    if any(v is None or v <= 0 for v in equity_curve):
        return None

    peak = equity_curve[0]
    peak_idx = 0
    best_mdd = 0.0
    best_peak_idx = 0
    best_trough_idx = 0

    for i, v in enumerate(equity_curve):
        if v > peak:
            peak = v
            peak_idx = i
        dd = 1.0 - v / peak
        if dd > best_mdd:
            best_mdd = dd
            best_peak_idx = peak_idx
            best_trough_idx = i

    return DrawdownResult(
        mdd=best_mdd,
        peak_index=best_peak_idx,
        trough_index=best_trough_idx,
        peak_value=equity_curve[best_peak_idx],
        trough_value=equity_curve[best_trough_idx],
    )


# --------------------------------------------------------------------------
# M206 夏普 / M207 索提诺 / M208 卡玛
# --------------------------------------------------------------------------


def sharpe_ratio(
    period_returns: Sequence[float],
    *,
    risk_free_annual: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """夏普比率 M206，附件A §5.3。

    ``Sharpe = (年化收益 − 无风险利率) / 年化波动率``

    Args:
        period_returns: 单期净收益率序列。
        risk_free_annual: 年化无风险利率（小数）。附件A D17 要求标注来源；
            查不到时用常数 3% 并标注。
        periods_per_year: 每年期数。

    Returns:
        夏普比率；波动率为 0 或样本不足时返回 ``None``。
    """
    vol = annualized_volatility(period_returns, periods_per_year=periods_per_year)
    if vol is None or vol == 0:
        return None
    n = len(period_returns)
    if n == 0:
        return None
    growth = 1.0
    for r in period_returns:
        growth *= 1.0 + r
    ann_return = growth ** (periods_per_year / n) - 1.0
    return (ann_return - risk_free_annual) / vol


def downside_deviation(
    period_returns: Sequence[float],
    *,
    target: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """下行波动（年化），索提诺比率的分母。

    ``DD = √( mean( min(0, r − target)² ) ) × √(periods_per_year)``

    注意分母是**全部样本数**（不是仅负样本数）——这是索提诺的标准定义，
    只数负样本会系统性低估下行风险。
    """
    if not period_returns:
        return None
    n = len(period_returns)
    sq = [(min(0.0, r - target)) ** 2 for r in period_returns]
    return math.sqrt(sum(sq) / n) * math.sqrt(periods_per_year)


def sortino_ratio(
    period_returns: Sequence[float],
    *,
    risk_free_annual: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """索提诺比率 M207，附件A §5.3。

    ``Sortino = (年化收益 − 无风险) / 下行波动``

    Returns:
        索提诺比率；下行波动为 0 或样本为空时返回 ``None``。
    """
    dd = downside_deviation(period_returns, periods_per_year=periods_per_year)
    if dd is None or dd == 0:
        return None
    n = len(period_returns)
    if n == 0:
        return None
    growth = 1.0
    for r in period_returns:
        growth *= 1.0 + r
    ann_return = growth ** (periods_per_year / n) - 1.0
    return (ann_return - risk_free_annual) / dd


def calmar_ratio(annual_return: float, mdd: float) -> float | None:
    """卡玛比率 M208，附件A §5.3。

    ``Calmar = 年化收益 / 最大回撤``

    Returns:
        卡玛比率；回撤为 0 或收益率不可得时返回 ``None``。
    """
    if annual_return is None or mdd is None or mdd == 0:
        return None
    return annual_return / mdd


# --------------------------------------------------------------------------
# M306 每元管理费对应基民盈亏
# --------------------------------------------------------------------------


def fee_per_yuan_pnl(
    p_inv: Decimal | int | float | str,
    cumulative_mgmt_fee: Decimal | int | float | str,
) -> float | None:
    """每元管理费对应基民盈亏 M306，附件A §5.3。

    ``M306 = P_inv ÷ 累计管理费``（单位：倍）

    Args:
        p_inv: 基民净盈亏 ``P_inv``（元）。
        cumulative_mgmt_fee: 累计管理费（元）。

    Returns:
        每元管理费对应的基民盈亏（倍）。
        ``> 0`` 表示基民整体赚钱；``< 0`` 表示基民亏钱而管理费照收。
        管理费为 0 时返回 ``None``（分母为 0 不得返回 0）。
    """
    fee = _d(cumulative_mgmt_fee)
    if fee == 0:
        return None
    return float(_d(p_inv) / fee)


# --------------------------------------------------------------------------
# M307 持有体验分布
# --------------------------------------------------------------------------


def holding_experience(
    navs: Sequence[float],
    *,
    window: int = 250,
    quantiles: Sequence[float] = (0.05, 0.25, 0.5, 0.75, 0.95),
) -> dict[str, object]:
    """持有体验分布 M307，附件A §5.3。

    对每个交易日，计算「该日买入并持有 ``window`` 日的滚动收益」，
    再统计这批滚动收益的分布、胜率与最差 5%。

    Args:
        navs: **复权净值**序列。
        window: 滚动窗口（交易日），默认 250 ≈ 1 年。
        quantiles: 需要输出的分位点。

    Returns:
        ``{"window", "samples", "win_rate", "worst_5pct", "best_5pct",
        "mean", "median", "min", "max", "quantiles"}``；
        样本不足时 ``samples=0`` 且各统计量为 ``None``（不得返回 0）。

    Note:
        「持有体验」回答的是「随便哪天买、拿一年，赚钱的概率有多大」——
        这与净值收益率是两回事（附件A §1.1 的核心区分）。
    """
    if window <= 0:
        raise ValueError(f"window 必须为正，收到 {window}")
    if not navs or len(navs) <= window:
        return {
            "window": window,
            "samples": 0,
            "win_rate": None,
            "worst_5pct": None,
            "best_5pct": None,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "quantiles": {},
        }

    rolls: list[float] = []
    for i in range(len(navs) - window):
        start, end = navs[i], navs[i + window]
        if start and start > 0:
            rolls.append(end / start - 1.0)

    if not rolls:
        raise InsufficientDataError("ROLL_EMPTY", "无有效滚动收益样本")

    ordered = sorted(rolls)
    n = len(ordered)

    def _q(p: float) -> float:
        """线性插值分位（与 numpy 默认 linear 一致）。"""
        if n == 1:
            return ordered[0]
        pos = p * (n - 1)
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return ordered[lo]
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

    return {
        "window": window,
        "samples": n,
        "win_rate": sum(1 for r in rolls if r >= 0) / n,
        "worst_5pct": _q(0.05),
        "best_5pct": _q(0.95),
        "mean": _mean(rolls),
        "median": _q(0.5),
        "min": ordered[0],
        "max": ordered[-1],
        "quantiles": {f"p{int(q * 100)}": _q(q) for q in quantiles},
    }


# --------------------------------------------------------------------------
# M310 费用率
# --------------------------------------------------------------------------


def fee_ratio(
    fee: Decimal | int | float | str,
    average_aum: Decimal | int | float | str,
    days: float,
) -> float | None:
    """费用率 M310，附件A §5.3。

    ``M310 = Fee ÷ 区间平均 AUM``（年化）

    Args:
        fee: 区间费用合计（元）。
        average_aum: 区间平均资产净值（元）。用日均 ``S×N`` 而非期初期末均值更准。
        days: 区间日历天数。

    Returns:
        年化费用率（小数）；平均 AUM 为 0 或天数非正时返回 ``None``。
    """
    aum = _d(average_aum)
    if aum == 0 or days is None or days <= 0:
        return None
    return float(_d(fee) / aum) * (365.0 / days)


# --------------------------------------------------------------------------
# M313 换手率（式 3.6 估算 + 披露值优先）
# --------------------------------------------------------------------------


def turnover_from_weights(
    weights_prev: Mapping[str, float],
    weights_curr: Mapping[str, float],
    returns_by_symbol: Mapping[str, float],
    portfolio_return: float,
) -> float | None:
    """由持仓权重变动估算换手率 M313，附件A §3.3 式(3.6)。

    ``Turnover_t ≈ ½ Σ_k | w_{k,t} − w_{k,t−1} · (1 + r_{k,t}) / (1 + r_port,t) |``

    被动漂移项 ``(1+r_k)/(1+r_port)`` 的作用：股价自己涨上来的权重变化**不算换手**。

    Args:
        weights_prev: 上期权重 ``{symbol: w}``。
        weights_curr: 本期权重。
        returns_by_symbol: 各标的区间收益 ``{symbol: r}``；缺该标的时按 0 处理。
        portfolio_return: 组合区间收益 ``r_port``。

    Returns:
        单边换手率（0–1 量级，可 >1）；两期权重皆空时返回 ``None``。

    Warning:
        🔴 **披露值优先**（附件A §3.3，2026-05-01 起强制披露）。
        本函数只在无披露值时使用，且必须标注「估算」并给出与披露值的偏离度。
        两者口径不可混用：披露值取买卖金额**较低者**，本式取权重变动绝对值之和的一半，
        单边行情下披露值系统性低于估算值（实测差异 20%–40%，属口径差非错误）。
    """
    symbols = set(weights_prev) | set(weights_curr)
    if not symbols:
        return None
    if not weights_prev or not weights_curr:
        return None
    denom = 1.0 + portfolio_return
    if denom == 0:
        return None
    total = 0.0
    for s in symbols:
        w0 = weights_prev.get(s, 0.0)
        w1 = weights_curr.get(s, 0.0)
        r = returns_by_symbol.get(s, 0.0)
        drifted = w0 * (1.0 + r) / denom
        total += abs(w1 - drifted)
    return 0.5 * total


def turnover_disclosed(
    buy_cost: Decimal | int | float | str,
    sell_proceeds: Decimal | int | float | str,
    avg_holding_value: Decimal | int | float | str,
) -> float | None:
    """披露口径换手率 M313（精度等级 A），附件A §3.3。

    ``Turnover^disc = min(买入成本总额, 卖出收入总额) ÷ 有持仓交易日日均持有股票市值``

    取**买卖金额较低者**是为了抑制双边放大（买 100 卖 100 只算一次换手）。

    Args:
        buy_cost: 区间买入股票成本总额。
        sell_proceeds: 区间卖出股票收入总额。
        avg_holding_value: 区间内有股票持仓交易日的日均持有股票市值。

    Returns:
        换手率（小数）；分母为 0 时返回 ``None``。
    """
    denom = _d(avg_holding_value)
    if denom == 0:
        return None
    return float(min(_d(buy_cost), _d(sell_proceeds)) / denom)


def turnover_caliber_gap(estimated: float, disclosed: float) -> float | None:
    """估算值与披露值的相对偏离，附件A §3.3。

    ``|est/disc − 1| > 50%`` ⇒ **估算模型失效**，交易费用应改用年报校准系数反推。

    Returns:
        相对偏离（小数）；披露值为 0 时返回 ``None``。
    """
    if disclosed == 0:
        return None
    return abs(estimated / disclosed - 1.0)
