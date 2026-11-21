"""三条恒等式与基民净盈亏——全系统结论的基石。

对应《附件A 算法引擎计算规范》§4：

* §4.1 式(4.1)  基民净盈亏 ``P_inv = A_T + Div_cash − A_0 − NetSub``
* §4.2 式(A)(B)(C)(C')  三条恒等式及其地位区分
* §4.2 式(4.2a) ``NetSub`` 为**费前**金额（申赎费不得冲减）
* §4.3 式(4.3)  投资者总成本 ``Cost_inv = F_sub + F_red + Fee``
* §4.4 式(4.4)(4.5)  ``Other`` 科目表与归一化残差
* §5.2  SOW  容差：同频 0.01 元 / 混频 ≤0.5%（决策 D16 从严）

三条式子的**地位必须区分**，否则引擎会自证正确：

===========  ==========================================  ==================
式           地位                                        用途
===========  ==========================================  ==================
**(A)**      定义式（``Other`` 为残差）                    计算 ``Other``
**(B)**      独立构造式（``P_inv`` 由市值/现金流构造）       交叉校验
**(C)**      利润表校验式（``R_gross``/``Fee`` 取年报）     年度对账
===========  ==========================================  ==================

🔴 **一票否决项**（附件A §4.4）：赎回费归入基金资产的部分属利润表「其他收入」，
已含在 ``R_gross`` → ``R_net`` 内，**绝不能再单列进 ``Other``**，否则 A 式双计、
AUM 凭空多一块。``Other`` 的正常量级应 ≈ 0。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from .errors import IdentityResidualError
from .types import IdentityCheck

# 恒等式容差（SOW §5.2 + 决策 D16）
ABS_TOL_YUAN = Decimal("0.01")
"""同频（日频净值 + 日频份额）绝对容差：到分。"""

REL_TOL_PCT = Decimal("0.005")
"""混频（日频净值 + 季频份额）相对容差：0.5%。0.5%–5% 不得放行。"""

OTHER_PCT_LIMIT = Decimal("0.005")
"""``Other`` 归一化残差阈值 ρ < 0.5%（式 4.5）。"""

OTHER_SUSPECT_PCT = Decimal("0.001")
"""``Other`` 达 AUM 的 0.1% 以上时，**第一嫌疑是科目归错**（附件A §4.5）。"""

_CENT = Decimal("0.01")


def _d(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    """量化到分。"""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _rel_residual(residual: Decimal, *scales: Decimal) -> float | None:
    """相对残差 ``|residual| / max(|scales|, ε)``；分母全为 0 时返回 ``None``。"""
    denom = max((abs(s) for s in scales), default=Decimal(0))
    if denom == 0:
        return None
    return float(abs(residual) / denom)


# --------------------------------------------------------------------------
# 式(4.1) 基民净盈亏
# --------------------------------------------------------------------------


def p_inv(
    a_end: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    a_start: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
) -> Decimal:
    """基民净盈亏 ``P_inv``，附件A §4.1 式(4.1)。

    ``P_inv = A_T + Div_cash − A_0 − NetSub``

    这是「全体持有人作为一个整体」的净盈亏，与单个人盈亏无关。

    Args:
        a_end: 期末基金资产净值 ``A_T``，元。
        div_cash: 期间**现金**分红总额 ``Div_cash``，元（红利再投不计入此项）。
        a_start: 期初基金资产净值 ``A_0``，元。
        net_sub: 期间净申购金额 ``NetSub``，元，**必须为费前金额**。
            若把付给销售渠道的申赎费从赎回额里扣掉，B 式会把「付给渠道的费」
            误算成「投资亏损」，基民盈亏被系统性低估（附件A §4.3）。

    Returns:
        ``P_inv``，Decimal 元（负值表示基民整体亏钱）。
    """
    return _q(_d(a_end) + _d(div_cash) - _d(a_start) - _d(net_sub))


def cost_inv(
    fee_sub: Decimal | int | float | str,
    fee_red: Decimal | int | float | str,
    fee_total: Decimal | int | float | str,
) -> Decimal:
    """投资者总成本 ``Cost_inv``，附件A §4.3 式(4.3)。

    ``Cost_inv = F_sub + F_red + Fee``

    Note:
        这是**完全独立口径**，不参与利润表、不参与恒等式 A/B/C。
        申赎费（全额，含归销售机构与归基金资产两部分）只在这里出现一次。
    """
    return _q(_d(fee_sub) + _d(fee_red) + _d(fee_total))


# --------------------------------------------------------------------------
# 式(4.2a) 净申购金额（费前）
# --------------------------------------------------------------------------


def net_sub_amount(
    sub_shares: Decimal | int | float | str | None,
    red_shares: Decimal | int | float | str | None,
    avg_nav_sub: float,
    avg_nav_red: float,
) -> Decimal | None:
    """净申购金额（费前），附件A §4.2a 式(4.2a) / §6.2 式(8.5)。

    ``NetSub = Sub^sh × N̄_sub − Red^sh × N̄_red``

    Args:
        sub_shares: 期间申购份额；``None`` 表示未披露。
        red_shares: 期间赎回份额；``None`` 表示未披露。
        avg_nav_sub: 申购侧加权平均净值 ``N̄_sub``。
        avg_nav_red: 赎回侧加权平均净值 ``N̄_red``。

    Returns:
        ``NetSub``，Decimal 元；任一份额缺失时返回 ``None``——
        **缺失不等于 0**（附件A §2.2）。

    Warning:
        🔴 申赎费一律不得冲减 ``NetSub``（附件A §4.2a、陷阱 T3）。
    """
    if sub_shares is None or red_shares is None:
        return None
    sub_amt = _d(sub_shares) * _d(avg_nav_sub)
    red_amt = _d(red_shares) * _d(avg_nav_red)
    return _q(sub_amt - red_amt)


# --------------------------------------------------------------------------
# 式(A)(B)(C) 三条恒等式
# --------------------------------------------------------------------------


def solve_other(
    a_start: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    r_net: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
) -> Decimal:
    """由式(A) 解出 ``Other``（其他权益变动净额），附件A §4.2。

    ``ΔAUM = R_net + NetSub − Div_cash + Other``
    ⇒ ``Other = (A_T − A_0) − R_net − NetSub + Div_cash``

    Returns:
        ``Other``，Decimal 元。正常量级应 ≈ 0。
    """
    return _q(
        (_d(a_end) - _d(a_start)) - _d(r_net) - _d(net_sub) + _d(div_cash)
    )


def _tolerance_pass(
    residual: Decimal,
    residual_pct: float | None,
    *,
    mixed_freq: bool,
    tol: Decimal,
) -> bool:
    """容差判定（同频用绝对、混频用相对，分母为 0 时退化为绝对）。

    独立成函数是为了让三条恒等式共用同一套判定，避免各自实现出现口径漂移。
    """
    if mixed_freq and residual_pct is not None:
        return Decimal(str(residual_pct)) <= tol
    return abs(residual) <= ABS_TOL_YUAN


def check_identity_a(
    a_start: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    r_net: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    other: Decimal | int | float | str = 0,
    *,
    mixed_freq: bool = False,
) -> IdentityCheck:
    """校验式(A) 规模恒等式，附件A §4.2。

    ``ΔAUM = R_net + NetSub − Div_cash + Other``

    Args:
        mixed_freq: ``True`` 时用相对容差 0.5%，否则用绝对容差 0.01 元。

    Returns:
        :class:`IdentityCheck`；``passed`` 为 ``False`` 时调用方**必须**处理
        （可调 :func:`assert_identity` 直接抛异常）。
    """
    lhs = _d(a_end) - _d(a_start)
    rhs = _d(r_net) + _d(net_sub) - _d(div_cash) + _d(other)
    residual = _q(lhs - rhs)
    pct = _rel_residual(residual, _d(a_start), _d(a_end))
    tol = REL_TOL_PCT if mixed_freq else ABS_TOL_YUAN
    passed = _tolerance_pass(residual, pct, mixed_freq=mixed_freq, tol=tol)
    return IdentityCheck(
        identity="A",
        lhs=lhs,
        rhs=rhs,
        residual=residual,
        residual_pct=pct,
        tolerance=tol,
        passed=passed,
    )


def check_identity_b(
    a_start: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    div_cash: Decimal | int | float | str,
    net_sub: Decimal | int | float | str,
    p_inv_value: Decimal | int | float | str,
    *,
    mixed_freq: bool = False,
) -> IdentityCheck:
    """校验式(B) 盈亏恒等式（**独立构造式**），附件A §4.2。

    ``P_inv = ΔAUM + Div_cash − NetSub``

    Note:
        ``P_inv`` 必须由「市值 + 现金流」直接构造，**不得**用 A 式反算，
        否则这一步交叉校验没有意义。
    """
    lhs = _d(p_inv_value)
    rhs = (_d(a_end) - _d(a_start)) + _d(div_cash) - _d(net_sub)
    residual = _q(lhs - rhs)
    pct = _rel_residual(residual, _d(a_start), _d(a_end))
    tol = REL_TOL_PCT if mixed_freq else ABS_TOL_YUAN
    passed = _tolerance_pass(residual, pct, mixed_freq=mixed_freq, tol=tol)
    return IdentityCheck(
        identity="B",
        lhs=lhs,
        rhs=rhs,
        residual=residual,
        residual_pct=pct,
        tolerance=tol,
        passed=passed,
    )


def check_identity_c(
    r_gross: Decimal | int | float | str,
    p_inv_value: Decimal | int | float | str,
    fee: Decimal | int | float | str,
    other: Decimal | int | float | str = 0,
    *,
    mixed_freq: bool = False,
) -> IdentityCheck:
    """校验式(C)/(C') 分配恒等式（**利润表校验式**），附件A §4.2。

    ``R_gross = P_inv + Fee − Other``

    Note:
        只在 ``Other = 0`` 时退化为 ``R_gross = P_inv + Fee``。
        若把三条式都当成「必须同时精确成立」的硬断言，
        会在发生份额折算/差错更正的期间误报对账失败。
    """
    lhs = _d(r_gross)
    rhs = _d(p_inv_value) + _d(fee) - _d(other)
    residual = _q(lhs - rhs)
    pct = _rel_residual(residual, lhs, rhs)
    tol = REL_TOL_PCT if mixed_freq else ABS_TOL_YUAN
    passed = _tolerance_pass(residual, pct, mixed_freq=mixed_freq, tol=tol)
    return IdentityCheck(
        identity="C",
        lhs=lhs,
        rhs=rhs,
        residual=residual,
        residual_pct=pct,
        tolerance=tol,
        passed=passed,
    )


def assert_identity(check: IdentityCheck) -> None:
    """质量门：恒等式不闭合即抛异常（附件A §10 E7：超阈值抛异常，不静默）。

    Raises:
        IdentityResidualError: ``check.passed`` 为 ``False``。
    """
    if not check.passed:
        raise IdentityResidualError(
            identity=check.identity,
            residual=check.residual,
            tolerance=check.tolerance,
            residual_pct=check.residual_pct,
        )


# --------------------------------------------------------------------------
# 式(4.5) ``Other`` 归一化残差与科目归错自检
# --------------------------------------------------------------------------


def unexplained_residual_pct(
    other_unexplained: Decimal | int | float | str,
    a_start: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
    *,
    eps: Decimal = Decimal("1e-9"),
) -> float:
    """归一化残差 ``ρ``，附件A §4.5 式(4.5)。

    ``ρ = |Other_unexplained| / max(|A_0|, |A_T|, ε)``，门禁要求 ``ρ < 0.5%``。
    """
    denom = max(abs(_d(a_start)), abs(_d(a_end)), eps)
    return float(abs(_d(other_unexplained)) / denom)


def is_other_suspect(
    other: Decimal | int | float | str,
    a_start: Decimal | int | float | str,
    a_end: Decimal | int | float | str,
) -> bool:
    """``Other`` 是否达到「疑似科目归错」量级（> 0.1% AUM）。

    附件A §4.5：无折算/无合并/无差错更正的正常季度 ``Other`` 应 ≈ 0；
    达 AUM 的 0.1% 以上时，**第一嫌疑是科目归错**（把利润表科目错记进权益变动），
    而不是「数据有问题」。
    """
    return unexplained_residual_pct(other, a_start, a_end) >= float(OTHER_SUSPECT_PCT)


def pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """皮尔逊相关系数（纯标准库实现，避免 core 引入 numpy 依赖）。"""
    if len(xs) != len(ys):
        raise ValueError(f"序列长度不一致：{len(xs)} vs {len(ys)}")
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def check_other_not_redeem_fee(
    other_series: Sequence[float],
    redeem_fee_to_fund_series: Sequence[float],
    *,
    threshold: float = 0.5,
) -> float | None:
    """``Other`` 科目归错自检，附件A §4.4 / 陷阱 T2。

    ```python
    assert abs(np.corrcoef(other_series, red_fee_to_fund_series)[0, 1]) < 0.5
    ```

    若 ``Other`` 与「赎回费归资产金额」高度相关（|corr| > 0.5），
    说明科目归错了——那笔钱应进 ``R_gross`` 的「其他收入」子科目。

    Returns:
        相关系数；样本不足或某一序列方差为 0 时返回 ``None``（无法判定）。

    Raises:
        IdentityResidualError: ``|corr| > threshold``，科目归错。
    """
    corr = pearson_correlation(other_series, redeem_fee_to_fund_series)
    if corr is not None and abs(corr) > threshold:
        raise IdentityResidualError(
            identity="Other-科目归错自检",
            residual=round(corr, 6),
            tolerance=threshold,
            residual_pct=None,
        )
    return corr


# --------------------------------------------------------------------------
# 四端口径一致性（附件C MC-01：算法只有一份）
# --------------------------------------------------------------------------

GOLDEN_TOLERANCE = 1e-6
"""四端对拍容差（SOW §7.3 C12 / 附件C §13：差异 ≤ 1e-6）。"""


def compare_scalar(a: float, b: float, *, tol: float = GOLDEN_TOLERANCE) -> bool:
    """比较两个标量是否在四端一致性容差内（绝对 + 相对混合）。"""
    if a is None or b is None:
        return a is b
    diff = abs(a - b)
    if diff <= tol:
        return True
    scale = max(abs(a), abs(b), 1.0)
    return diff / scale <= tol
