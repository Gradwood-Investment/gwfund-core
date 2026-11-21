"""规模归因：瀑布、份额归因、收益分解。

对应《附件A 算法引擎计算规范》§5：

* §5.1 式(6.1)  四段瀑布 ``A_0 → +NetSub → +R_net → −Div_cash → +Other → A_T``
* §5.1 式(6.2)  归因残差 ``ε_attr < 0.5%``（质量门）
* §5.2 式(6.3)  第二层份额归因 ``ΔS = NetSub^sh + Div^reinv + ΔS^split``
* §5.2 式(6.4)  第三层收益分解 ``ΔA/A_0 = NetSub/A_0 + R_net率 − Div/A_0``
* §5.3          手算样例（000001 2026Q2：26.44 亿 → 39.38 亿）

产品含义：``ΔA/A_0 ≠ 净值增长率``。当 ``NetSub ≠ 0`` 时两者不等，
**这是产品必须解释的核心差异**（「规模涨了 48.9%，但净值只涨了 60.6%，
且客户其实净赎回了 9.4%」）。
"""

from __future__ import annotations

from decimal import Decimal

from .errors import AttributionResidualError
from .types import WaterfallResult, WaterfallSegment

_CENT = Decimal("0.01")
REL_TOL_ATTR = Decimal("0.005")
"""归因残差阈值 0.5%（附件A §5.1 式(6.2)）。"""


def _d(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def r_net_from_identity(
    a_start: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    other: Decimal | int | float | str = 0,
) -> Decimal:
    """由式(A) 反解近似 ``R_net``（归因第 ③ 段的实现方式）。

    ``R_net ≈ A_T − A_0 − NetSub + Div_cash − Other``

    Note:
        附件A §5.1 把第 ③ 段「投资收益贡献」定义为上式。
        这是**归因口径**，不等于利润表的 ``R_gross − Fee``；
        两者差额应全部进 ``Other``，对账见 §8。
    """
    return _d(a_end) - _d(a_start) - _d(net_sub) + _d(div_cash) - _d(other)


def waterfall(
    a_start: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    r_net: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    other: Decimal | int | float | str = 0,
    *,
    raise_on_fail: bool = False,
) -> WaterfallResult:
    """规模归因瀑布（第一层·金额归因），附件A §5.1 式(6.1)。

    六段结构：

    ==  ================  ============================  ==========
    段  项                计算                          符号预期
    ==  ================  ============================  ==========
    ①   期初 AUM          ``A_0 = S_0·N_0``              —
    ②   净申购贡献        ``NetSub``                    可正可负
    ③   投资收益贡献      ``R_net``                     可正可负
    ④   分红派现          ``−Div_cash``                 ≤ 0
    ⑤   其他              ``Other``                     通常 ≈ 0
    ⑥   期末 AUM          ``A_T = S_T·N_T``              —
    ==  ================  ============================  ==========

    Args:
        other: 其他权益变动净额。**注意**：赎回费归基金资产**不在**此项内
            （它属 ``R_gross`` 的其他收入），见附件A §4.4。
        raise_on_fail: ``True`` 时残差超阈值直接抛 :class:`AttributionResidualError`。

    Returns:
        :class:`WaterfallResult`，含各段金额、占期初 AUM 比例与残差。

    Raises:
        AttributionResidualError: ``raise_on_fail=True`` 且残差 ≥ 0.5%。
    """
    a0 = _d(a_start)
    at = _d(a_end)
    ns = _d(net_sub)
    rn = _d(r_net)
    dv = _d(div_cash)
    ot = _d(other)

    explained = a0 + ns + rn - dv + ot
    residual = (explained - at).quantize(_CENT)
    denom = max(abs(a0), abs(at))
    residual_pct = float(abs(residual) / denom) if denom else 0.0
    passed = Decimal(str(residual_pct)) < REL_TOL_ATTR

    def pct(x: Decimal) -> float | None:
        return float(x / a0) if a0 else None

    segments = (
        WaterfallSegment("a_start", "期初 AUM", a0, pct(a0)),
        WaterfallSegment("net_sub", "净申购贡献", ns, pct(ns)),
        WaterfallSegment("r_net", "投资收益贡献", rn, pct(rn)),
        WaterfallSegment("div_cash", "分红派现", -dv, pct(-dv)),
        WaterfallSegment("other", "其他权益变动", ot, pct(ot)),
        WaterfallSegment("a_end", "期末 AUM", at, pct(at)),
    )

    if raise_on_fail and not passed:
        raise AttributionResidualError(
            f"归因残差 {residual}（{residual_pct:.6%}）超阈值 0.5%"
        )

    return WaterfallResult(
        segments=segments,
        a_start=a0,
        a_end=at,
        residual=residual,
        residual_pct=residual_pct,
        passed=passed,
    )


def share_attribution(
    s_start: Decimal | int | float | str,
    s_end: Decimal | int | float | str,
    net_sub_shares: Decimal | int | float | str,
    div_reinvest_shares: Decimal | int | float | str = 0,
    split_adjust_shares: Decimal | int | float | str = 0,
    *,
    tol_shares: Decimal = Decimal("0.01"),
) -> dict[str, object]:
    """第二层份额归因，附件A §5.2 式(6.3)。

    ``ΔS = NetSub^sh + Div^reinv + ΔS^split``

    Args:
        tol_shares: 份额残差容差。实测 F10 披露精度为 0.01 亿份 = 100 万份，
            因此调用方应把容差按数据精度单位换算后传入。

    Returns:
        ``{"delta_s", "net_sub_shares", "div_reinvest", "split_adjust",
        "residual", "passed"}``。

    Note:
        🔴 若代码只写 ``S_t = S_{t-1} + NetSub``，则在「红利再投」与「份额折算」
        两种情形下会**静默产生虚假净申购**——本系统最致命的实现错误
        （附件A §1.2 式(1.4) 与陷阱 T1/T2）。
    """
    ds = _d(s_end) - _d(s_start)
    ns = _d(net_sub_shares)
    dr = _d(div_reinvest_shares)
    sp = _d(split_adjust_shares)
    residual = (ds - ns - dr - sp).quantize(Decimal("0.0001"))
    return {
        "delta_s": ds,
        "net_sub_shares": ns,
        "div_reinvest": dr,
        "split_adjust": sp,
        "residual": residual,
        "passed": abs(residual) <= tol_shares,
    }


def return_decomposition(
    a_start: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    r_net: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
) -> dict[str, float | None]:
    """第三层收益分解，附件A §5.2 式(6.4)。

    ``ΔA/A_0 = NetSub/A_0 + R_net率 − Div_cash/A_0``

    Returns:
        ``{"delta_a_ratio", "sub_dilution", "nav_growth", "div_effect"}``；
        ``A_0 = 0`` 时全部为 ``None``（不得返回 0）。

    Warning:
        ``delta_a_ratio`` **不等于**净值增长率。产品的核心交付物之一就是
        解释这两个数的差异——「规模涨了 40%，但净值只涨了 5%」。
    """
    a0 = _d(a_start)
    if a0 == 0:
        return {
            "delta_a_ratio": None,
            "sub_dilution": None,
            "nav_growth": None,
            "div_effect": None,
        }
    return {
        "delta_a_ratio": float(_d(net_sub) + _d(r_net) - _d(div_cash)) / float(a0),
        "sub_dilution": float(_d(net_sub)) / float(a0),
        "nav_growth": float(_d(r_net)) / float(a0),
        "div_effect": float(_d(div_cash)) / float(a0),
    }
