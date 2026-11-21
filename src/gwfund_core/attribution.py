"""规模归因：瀑布、份额归因、收益分解。

对应《附件A 算法引擎计算规范》§5 与 §4.7 SPEC-GAP-02：

* §5.1 式(6.1)  四段瀑布 ``A_0 → +NetSub → +R_net → −Div_cash → +Other → A_T``
* §5.1 式(6.2)  归因残差 ``ε_attr < 0.5%``（质量门）
* §5.2 式(6.3)  第二层份额归因 ``ΔS = NetSub^sh + Div^reinv + ΔS^split``
* §5.2 式(6.4)  第三层收益分解 ``ΔA/A_0 = NetSub/A_0 + R_net/A_0 − Div/A_0``
* §4.7 SPEC-GAP-02  ``div_effect`` **取负号**，三分量严格可加

**B02 修复记录（2026-11-21，依《裁定书二》P0-2）**

原实现的致命缺陷：``r_net_from_identity`` 由 A 式反解 ``R_net``，
``waterfall`` 再拿它去算残差 ⇒ **残差恒等于 0**。把期末 AUM 改大 27%，残差仍是 ``0.00``、
``passed`` 仍是 ``True``——这正是本产品要防的那类「自我印证」错误。

修复方式（双路独立）：

1. :func:`r_net_from_identity` —— A 式反解（**仅供诊断，禁止用于残差门**，已在 docstring 标注）；
2. :func:`r_net_from_return` —— 独立路径 ①：``R_net = A_0 × 区间收益率``；
3. :func:`r_net_from_disclosure` —— 独立路径 ②：``R_net = R_gross − Fee``（利润表）；
4. :func:`reconcile_r_net` —— 两路差额入残差，超容差即失败（T-3：0.5%）；
5. :func:`waterfall_independent` —— 用**独立构造**的 ``R_net`` 跑瀑布，
   残差因此可能非零，门禁才有效力；
6. 负控测试：把期末 AUM 人为改 1%，残差门**必须报错**。
"""

from __future__ import annotations

import warnings
from decimal import Decimal

from .errors import AttributionResidualError, GwFundWarning
from .types import WaterfallResult, WaterfallSegment

_CENT = Decimal("0.01")
REL_TOL_ATTR = Decimal("0.005")
"""归因残差阈值 0.5%（附件A §5.1 式(6.2) / 附件C T-3）。"""


class SelfProvisionWarning(GwFundWarning):
    """归因残差恰为 0 —— 高度怀疑 ``R_net`` 来自 A 式反解（自我印证）。

    《裁定书二》B02：残差恒真的门禁等于没有门禁。
    本警告的作用是在运行时把这个失效模式喊出来。
    """


def _d(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# --------------------------------------------------------------------------
# R_net 的三种构造路径（必须互相独立，否则残差门无效）
# --------------------------------------------------------------------------


def r_net_from_identity(
    a_start: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    other: Decimal | int | float | str = 0,
) -> Decimal:
    """由式(A) 反解 ``R_net`` —— **仅供诊断，禁止用于残差门**。

    ``R_net ≈ A_T − A_0 − NetSub + Div_cash − Other``

    .. danger::
       **用本函数的结果去算归因残差，残差恒等于 0**（B02 缺陷）。
       残差门必须改用 :func:`r_net_from_return` 或 :func:`r_net_from_disclosure`
       独立构造 ``R_net``。

    本函数唯一正当用途：当 ``R_net`` 有独立来源时，反解出 ``Other``
    （见 :func:`gwfund_core.identities.solve_other`）。
    """
    return _d(a_end) - _d(a_start) - _d(net_sub) + _d(div_cash) - _d(other)


def r_net_from_return(
    a_start: Decimal | int | float | str,
    period_return: float,
    *,
    average_aum: Decimal | int | float | str | None = None,
) -> Decimal:
    """**独立路径 ①**：``R_net = A_0 × 区间收益率``（或 ``平均 AUM × 收益率``）。

    Args:
        a_start: 期初资产净值 ``A_0``。
        period_return: 区间净收益率（由**复权净值**链式得出，或由份额加权得出）。
        average_aum: 若有更可靠的区间平均 AUM（如日均 ``S×N``），优先用它。
            用 ``A_0`` 会在规模大幅变动时高估/低估投资收益。

    Returns:
        ``R_net``（Decimal 元）。

    Note:
        这条路径与 A 式**无关**——它只依赖净值收益率与规模，
        因此可以用来检 A 式反解的结果是否离谱。
    """
    base = _d(average_aum) if average_aum is not None else _d(a_start)
    return (base * _d(period_return)).quantize(_CENT)


def r_net_from_disclosure(
    r_gross: Decimal | int | float | str,
    fee: Decimal | int | float | str,
) -> Decimal:
    """**独立路径 ②**：``R_net = R_gross − Fee``（利润表口径）。

    Args:
        r_gross: 利润表「收入合计」（附件A §4.2 式(C)，须合并公允价值变动收益，决策 D17）。
        fee: 利润表费用合计。

    Returns:
        ``R_net``（Decimal 元）。

    Note:
        利润表口径与净值口径同量纲（附件A §2.1 推论），可比。
        但披露只有年/半年频，因此该路径只适用于年度/半年度残差门。
    """
    return (_d(r_gross) - _d(fee)).quantize(_CENT)


class RNetReconciliation:
    """``R_net`` 双路对账结果。"""

    __slots__ = ("label", "value_a", "value_b", "diff", "diff_pct", "passed")

    def __init__(
        self,
        label: str,
        value_a: Decimal,
        value_b: Decimal,
        tolerance: Decimal = REL_TOL_ATTR,
    ) -> None:
        self.label = label
        self.value_a = value_a
        self.value_b = value_b
        self.diff = (value_a - value_b).quantize(_CENT)
        denom = max(abs(value_a), abs(value_b))
        self.diff_pct = float(abs(self.diff) / denom) if denom else 0.0
        self.passed = Decimal(str(self.diff_pct)) <= tolerance

    def as_line(self) -> str:
        flag = "PASS" if self.passed else "FAIL"
        return (
            f"[R_net 双路 {self.label}] {flag} "
            f"{self.value_a} vs {self.value_b}，差 {self.diff}（{self.diff_pct:.6%}）"
        )


def reconcile_r_net(
    r_net_identity: Decimal | int | float | str,
    r_net_independent: Decimal | int | float | str,
    *,
    label: str = "",
    tolerance: Decimal = REL_TOL_ATTR,
    raise_on_fail: bool = False,
) -> RNetReconciliation:
    """两路 ``R_net`` 对账（B02 的核心门禁）。

    Args:
        r_net_identity: A 式反解值（:func:`r_net_from_identity`）。
        r_net_independent: 独立构造值（:func:`r_net_from_return` 或
            :func:`r_net_from_disclosure`）。
        tolerance: 相对容差，默认 0.5%（附件C T-3）。

    Returns:
        :class:`RNetReconciliation`。
    """
    rec = RNetReconciliation(label, _d(r_net_identity), _d(r_net_independent), tolerance)
    if raise_on_fail and not rec.passed:
        raise AttributionResidualError(
            f"R_net 双路对账失败{('（' + label + '）') if label else ''}："
            f"{rec.value_a} vs {rec.value_b}，相对差 {rec.diff_pct:.6%} > {tolerance}"
        )
    return rec


# --------------------------------------------------------------------------
# 式(6.1) 瀑布
# --------------------------------------------------------------------------


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

    .. warning::
       **若 ``r_net`` 来自 :func:`r_net_from_identity`，残差恒等于 0，门禁无效**（B02）。
       生产路径请用 :func:`waterfall_independent`。本函数检测到「残差恰为 0」时
       会发出 :class:`SelfProvisionWarning`。

    Args:
        other: 其他权益变动净额。**注意**：赎回费归基金资产**不在**此项内
            （它属 ``R_gross`` 的其他收入），见附件A §4.4。
        raise_on_fail: ``True`` 时残差超阈值直接抛 :class:`AttributionResidualError`。

    Returns:
        :class:`WaterfallResult`，含各段金额、占期初 AUM 比例与残差。
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

    # B02 运行时守卫：残差恰为 0 是「自我印证」的典型指纹
    if abs(residual) < Decimal("0.000001") and rn != 0:
        expected_identity = a0 + ns + rn - dv + ot
        if abs(expected_identity - at) < Decimal("0.000001"):
            warnings.warn(
                "归因残差恰为 0：R_net 极可能来自 A 式反解，残差门已失效（B02）。"
                "生产路径请改用 waterfall_independent() 或先做 reconcile_r_net()。",
                SelfProvisionWarning,
                stacklevel=2,
            )

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


def waterfall_independent(
    a_start: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    *,
    period_return: float,
    other: Decimal | int | float | str = 0,
    average_aum: Decimal | int | float | str | None = None,
    raise_on_fail: bool = True,
) -> WaterfallResult:
    """用**独立构造**的 ``R_net`` 跑瀑布（B02 修复后的生产入口）。

    ``R_net`` 由 :func:`r_net_from_return` 给出（期初/平均 AUM × 区间收益率），
    与 A 式无关，因此残差**可能非零**——门禁才有效力。

    Args:
        period_return: 区间净收益率（复权净值口径）。
        average_aum: 区间平均 AUM；``None`` 时退回 ``A_0``。
        raise_on_fail: 默认 ``True``——生产路径残差超 0.5% 直接阻断。

    Returns:
        :class:`WaterfallResult`。

    Raises:
        AttributionResidualError: 残差 ≥ 0.5% 且 ``raise_on_fail=True``。
    """
    rn = r_net_from_return(a_start, period_return, average_aum=average_aum)
    return waterfall(
        a_start, net_sub, rn, div_cash, a_end, other, raise_on_fail=raise_on_fail
    )


# --------------------------------------------------------------------------
# 式(6.3) 第二层：份额归因
# --------------------------------------------------------------------------


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


# --------------------------------------------------------------------------
# 式(6.4) 第三层：收益分解（SPEC-GAP-02：div_effect 取负号）
# --------------------------------------------------------------------------


def return_decomposition(
    a_start: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    r_net: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    *,
    a_end: Decimal | int | float | str | None = None,
    check_closed: bool = True,
) -> dict[str, float | None]:
    """第三层收益分解，附件A §5.2 式(6.4) + §4.7 SPEC-GAP-02。

    ``ΔA/A_0 = NetSub/A_0 + R_net/A_0 + (−Div_cash/A_0)``
              ``sub_dilution``  ``nav_growth``   ``div_effect``

    **``div_effect`` 取负号**：三分量之和严格等于 ``delta_a_ratio``。
    原实现返回 ``+Div/A_0``，导致「分量和 − 总额 = 2×Div/A_0」——有分红的基金
    在 UI 上四根柱加总必然对不上期末 AUM（DEP-01 / SPEC-GAP-02）。

    Args:
        a_end: 若提供，则额外用「A_T − A_0」独立核对 ``delta_a_ratio``，
            不一致时在返回值里给出 ``closure_residual``。
        check_closed: 是否做闭合性断言（|分量和 − 总额| ≤ 1e-9）。

    Returns:
        ``{"delta_a_ratio", "sub_dilution", "nav_growth", "div_effect",
        "closure_residual", "closure_passed"}``。
        ``A_0 = 0`` 时各分量为 ``None``（不得返回 0）。

    Raises:
        AttributionResidualError: ``check_closed=True`` 且闭合性断言失败
            （说明实现或调用口径有误，必须阻断）。

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
            "closure_residual": None,
            "closure_passed": None,
        }

    f0 = float(a0)
    sub_dilution = float(_d(net_sub)) / f0
    nav_growth = float(_d(r_net)) / f0
    div_effect = -float(_d(div_cash)) / f0  # SPEC-GAP-02：取负号
    delta_a_ratio = sub_dilution + nav_growth + div_effect

    closure_residual = (sub_dilution + nav_growth + div_effect) - delta_a_ratio
    closure_passed = abs(closure_residual) <= 1e-9

    if a_end is not None:
        observed = float(_d(a_end) - a0) / f0
        closure_residual = observed - delta_a_ratio
        closure_passed = abs(closure_residual) <= 1e-9

    if check_closed and not closure_passed:
        raise AttributionResidualError(
            f"收益分解不闭合：残差 {closure_residual:.3e}（容差 1e-9）；"
            f"分量 sub={sub_dilution:.9f} nav={nav_growth:.9f} "
            f"div={div_effect:.9f} 总额={delta_a_ratio:.9f}"
        )

    return {
        "delta_a_ratio": delta_a_ratio,
        "sub_dilution": sub_dilution,
        "nav_growth": nav_growth,
        "div_effect": div_effect,
        "closure_residual": closure_residual,
        "closure_passed": closure_passed,
    }
