"""混频处理：日频净值 + 季频份额 → 日频 AUM 与净申购金额。

对应《附件A 算法引擎计算规范》§6（**关键章节**）：

* §6.2 式(8.1)(8.2)  方案 1 阶梯法（精度 B）
* §6.2 式(8.3)        方案 2 线性插值法（精度 C）
* §6.2 式(8.4)(8.5)   方案 3 季度申赎明细精确法 + 日内按净值分配（精度 A，**默认**）
* §6.3                默认方案、降级判据与开关
* §6.4                精度等级定义（**必须在 UI 与导出中标注**）
* §6.5 式(8.6)(8.7)   日频 AUM 误差上界估计

核心认识（附件A §6.2）：份额变动**不用区间差反推**，直接用披露的
``Sub^sh`` / ``Red^sh``；仅「日内分配权重」是假设。
因此方案 3 的精度是 A（区间口径），而日频 AUM 仍为 B。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from .errors import InsufficientDataError, NetSubMethodWarning
from .types import MixingMethod, PrecisionLevel, WeightScheme

_CENT = Decimal("0.01")


def _d(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# --------------------------------------------------------------------------
# 方案选择与降级（附件A §6.3）
# --------------------------------------------------------------------------


def choose_method(
    *,
    has_sub_red: bool,
    has_start_end_share: bool,
    has_end_share: bool,
) -> MixingMethod:
    """按可得数据自动选择混频方案，附件A §6.3「降级判据」。

    ==================================  ==================
    条件                                选用
    ==================================  ==================
    ``Sub^sh`` 与 ``Red^sh`` 均非空       方案 3（**A**）
    仅有 ``S_0, S_T``                    方案 2（**C**）
    仅有 ``S_T``                         方案 1（**B**）
    无任何份额                            **拒绝计算**
    ==================================  ==================

    Raises:
        InsufficientDataError: ``SHARE_UNAVAILABLE``，无任何份额数据。
    """
    if has_sub_red:
        return MixingMethod.QUARTERLY_DETAIL
    if has_start_end_share:
        return MixingMethod.LINEAR
    if has_end_share:
        return MixingMethod.STEP
    raise InsufficientDataError("SHARE_UNAVAILABLE", "无任何份额数据，拒绝计算")


def precision_for(
    method: MixingMethod,
    *,
    is_daily_aum: bool = False,
    attribution_residual_ok: bool = True,
) -> PrecisionLevel:
    """混频方案的精度等级，附件A §6.4。

    强制规则：

    1. 任何**日频 AUM 曲线**，无论用哪种方法，**一律最高标 B**
       （因为日频份额本身是估算的）；
    2. 任何**季度/半年度/年度区间汇总**，若用方案 3 且残差达标，可标 **A**；
    3. 严禁把 B/C 级数据当 A 级展示。
    """
    if method is MixingMethod.QUARTERLY_DETAIL and not is_daily_aum:
        return PrecisionLevel.A if attribution_residual_ok else PrecisionLevel.B
    if method is MixingMethod.STEP or is_daily_aum:
        return PrecisionLevel.B
    return PrecisionLevel.C


# --------------------------------------------------------------------------
# 方案 1：阶梯法（附件A §6.2 式 8.1/8.2）
# --------------------------------------------------------------------------


def step_shares(
    dates: Sequence[date],
    quarter_ends: Sequence[date],
    quarter_shares: Sequence[float],
) -> list[float | None]:
    """阶梯法估算日频份额，附件A §6.2 式(8.1)。

    ``Ŝ_t = S_{τ(t)}, τ(t) = 最近一个已披露季末 ≤ t``

    Args:
        dates: 需求值的日期序列（升序）。
        quarter_ends: 已披露季末日期（升序）。
        quarter_shares: 与 ``quarter_ends`` 对应的期末份额。

    Returns:
        与 ``dates`` 等长的份额序列；早于首个季末的位置为 ``None``
        （成立前不可得，**禁止前向填充**）。
    """
    if len(quarter_ends) != len(quarter_shares):
        raise ValueError("quarter_ends 与 quarter_shares 长度不一致")
    pairs = sorted(zip(quarter_ends, quarter_shares))
    out: list[float | None] = []
    j = -1
    for d in dates:
        while j + 1 < len(pairs) and pairs[j + 1][0] <= d:
            j += 1
        out.append(None if j < 0 else float(pairs[j][1]))
    return out


def step_aum(
    dates: Sequence[date],
    navs: Sequence[float],
    quarter_ends: Sequence[date],
    quarter_shares: Sequence[float],
) -> list[float | None]:
    """阶梯法估算日频 AUM，附件A §6.2 式(8.2)：``Â_t = Ŝ_t · N_t``。

    Note:
        该曲线**必须标 B 级**（附件A §6.4 规则 1）。
    """
    shares = step_shares(dates, quarter_ends, quarter_shares)
    return [
        None if s is None else s * n for s, n in zip(shares, navs)
    ]


# --------------------------------------------------------------------------
# 方案 2：线性插值（附件A §6.2 式 8.3）
# --------------------------------------------------------------------------


def linear_shares(
    dates: Sequence[date],
    quarter_ends: Sequence[date],
    quarter_shares: Sequence[float],
) -> list[float | None]:
    """线性插值法估算日频份额，附件A §6.2 式(8.3)。

    ``Ŝ_t = S_{τ(t)} + (S_{τ(t+1)} − S_{τ(t)}) · (t − τ(t)) / (τ(t+1) − τ(t))``

    隐含「申赎在季内均匀发生」假设——**无系统性方向，但方差大**。
    两个季末之外（早于首个 / 晚于末个）不做外推，返回 ``None``。
    """
    if len(quarter_ends) != len(quarter_shares):
        raise ValueError("quarter_ends 与 quarter_shares 长度不一致")
    pairs = sorted(zip(quarter_ends, quarter_shares))
    if not pairs:
        return [None] * len(dates)
    out: list[float | None] = []
    for d in dates:
        left = None
        right = None
        for i in range(len(pairs)):
            if pairs[i][0] <= d:
                left = i
            if pairs[i][0] > d:
                right = i
                break
        if left is None or right is None:
            out.append(None)
            continue
        d0, s0 = pairs[left]
        d1, s1 = pairs[right]
        frac = (d - d0).days / (d1 - d0).days
        out.append(float(s0) + (float(s1) - float(s0)) * frac)
    return out


# --------------------------------------------------------------------------
# 方案 3：季度申赎明细精确法（附件A §6.2 式 8.4/8.5）—— 默认
# --------------------------------------------------------------------------


def uniform_weights(n: int) -> list[float]:
    """均匀权重（默认，附件A §6.2，决策 D20）：``w_t = 1/K``。"""
    if n <= 0:
        raise ValueError("权重长度必须为正")
    return [1.0 / n] * n


def v_shape_weights(navs: Sequence[float], *, side: str = "sub") -> list[float]:
    """V 型权重（行为学敏感性方案），附件A §6.2。

    申购侧：**高净值日权重高**（追涨）；赎回侧：**低净值日权重高**（杀跌）。

    这是**可证伪性弱的假设**，不得作为默认值（决策 D20 要求默认均匀）。
    """
    if not navs:
        raise ValueError("净值序列为空")
    lo, hi = min(navs), max(navs)
    span = hi - lo
    if span <= 0:
        return uniform_weights(len(navs))
    if side == "sub":
        raw = [(n - lo) / span for n in navs]
    elif side == "red":
        raw = [(hi - n) / span for n in navs]
    else:
        raise ValueError(f"side 必须是 'sub' 或 'red'，收到 {side!r}")
    total = sum(raw)
    if total <= 0:
        return uniform_weights(len(navs))
    return [w / total for w in raw]


def month_end_weights(dates: Sequence[date], n_last: int = 5) -> list[float]:
    """月末集中权重（渠道冲量敏感性方案），附件A §6.2。

    权重均分到每月的最后 ``n_last`` 个交易日。
    """
    if not dates:
        raise ValueError("日期序列为空")
    last_by_month: dict[tuple[int, int], list[date]] = {}
    for d in dates:
        last_by_month.setdefault((d.year, d.month), []).append(d)
    picked: set[date] = set()
    for _, ds in last_by_month.items():
        picked.update(sorted(ds)[-n_last:])
    if not picked:
        return uniform_weights(len(dates))
    w = 1.0 / len(picked)
    return [w if d in picked else 0.0 for d in dates]


def weights_for(
    scheme: WeightScheme,
    navs: Sequence[float],
    dates: Sequence[date],
    *,
    side: str = "sub",
) -> list[float]:
    """按权重方案生成日内分配权重 ``{w_t}``（``Σ w_t = 1``）。"""
    if scheme is WeightScheme.UNIFORM:
        return uniform_weights(len(navs))
    if scheme is WeightScheme.V_SHAPE:
        return v_shape_weights(navs, side=side)
    if scheme is WeightScheme.MONTH_END:
        return month_end_weights(dates)
    raise ValueError(f"未知权重方案：{scheme}")


def weighted_avg_nav(navs: Sequence[float], weights: Sequence[float]) -> float:
    """加权平均净值 ``N̄ = Σ w_t · N_t``。"""
    if len(navs) != len(weights):
        raise ValueError("navs 与 weights 长度不一致")
    total = sum(weights)
    if total <= 0:
        raise ValueError("权重之和必须为正")
    return sum(n * w for n, w in zip(navs, weights)) / total


def quarterly_net_sub(
    sub_shares: Decimal | int | float | str | None,
    red_shares: Decimal | int | float | str | None,
    navs: Sequence[float],
    dates: Sequence[date],
    *,
    scheme: WeightScheme = WeightScheme.UNIFORM,
) -> Decimal | None:
    """方案 3：用披露申赎明细计算净申购金额，附件A §6.2 式(8.4)/(8.5)。

    ``NetSub = Sub^sh · N̄_sub − Red^sh · N̄_red``

    Args:
        sub_shares: 期间申购份额（披露值）。``None`` → 返回 ``None``。
        red_shares: 期间赎回份额（披露值）。``None`` → 返回 ``None``。
        navs: 该季度内**所有交易日**的单位净值序列。
        dates: 与 ``navs`` 对应的日期序列。
        scheme: 日内分配权重方案，默认均匀。

    Returns:
        ``NetSub``（Decimal 元，费前）；份额缺失返回 ``None``。

    Note:
        🔴 申赎费不得冲减本金额（附件A §4.2a / 陷阱 T3）。
    """
    if sub_shares is None or red_shares is None:
        return None
    if not navs:
        raise InsufficientDataError("NAV_EMPTY", "季度内无净值，无法分配申赎")
    w_sub = weights_for(scheme, navs, dates, side="sub")
    w_red = weights_for(scheme, navs, dates, side="red")
    nav_sub = weighted_avg_nav(navs, w_sub)
    nav_red = weighted_avg_nav(navs, w_red)
    val = _d(sub_shares) * _d(nav_sub) - _d(red_shares) * _d(nav_red)
    return val.quantize(_CENT)


# --------------------------------------------------------------------------
# 误差上界（附件A §6.5 式 8.6/8.7）
# --------------------------------------------------------------------------


def aum_error_bound(
    net_sub_shares: Decimal | int | float | str,
    nav_max: float,
    nav_min: float,
) -> Decimal:
    """季内日频 AUM 的最大估算误差上界，附件A §6.5 式(8.7)。

    ``季内最大估算误差 ≤ |NetSub^sh| · (N_max − N_min)``

    Returns:
        Decimal 元。UI 必须显示为「±X 元（±Y%）」，
        **这个数字让用户能自己判断结论是否可靠**。
    """
    return (abs(_d(net_sub_shares)) * _d(abs(nav_max - nav_min))).quantize(_CENT)


def daily_progress_factor(
    t: date, tau: date, tau_next: date
) -> float:
    """日内进度因子 ``φ``，附件A §6.5 式(8.6)：``0 ≤ φ ≤ 1``。"""
    span = (tau_next - tau).days
    if span <= 0:
        return 0.0
    return min(1.0, max(0.0, (t - tau).days / span))


def daily_aum_error(
    net_sub_shares: Decimal | int | float | str,
    nav_t: float,
    phi: float,
) -> Decimal:
    """某日的 AUM 估算误差，附件A §6.5 式(8.6)。

    ``σ̂_A(t) ≤ |NetSub^sh| · N_t · φ``
    """
    return (abs(_d(net_sub_shares)) * _d(nav_t) * _d(phi)).quantize(_CENT)


def nav_by_date_lookup(
    nav_series: Mapping[date, float], dates: Sequence[date]
) -> list[float]:
    """按日期序列取净值；缺失即抛错（缺失不得静默当 0）。"""
    out: list[float] = []
    for d in dates:
        if d not in nav_series:
            raise InsufficientDataError("NAV_GAP", f"{d} 无净值记录")
        out.append(nav_series[d])
    return out


def mixing_degrade_warning(from_method: MixingMethod, to_method: MixingMethod) -> str:
    """降级留痕文案（附件A §6.3 要求日志完整）。"""
    msg = (
        f"混频方案降级：{from_method.value} → {to_method.value}；"
        f"精度等级随之下降，UI 必须显示对应徽章"
    )
    import warnings

    warnings.warn(msg, NetSubMethodWarning, stacklevel=2)
    return msg
