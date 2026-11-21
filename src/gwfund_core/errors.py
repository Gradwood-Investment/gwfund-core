"""gwfund-core 异常与警告类型。

设计原则（附件A §10 E7）：
    归因残差与 ``Other`` 残差必须作为**质量门**，超阈值时**抛异常**，不得静默返回。

因此本模块把「可以被上层忽略的软问题」做成 ``Warning``，
把「必须阻断计算的硬问题」做成 ``Exception``。
"""

from __future__ import annotations


class GwFundError(Exception):
    """gwfund-core 所有异常的基类。"""


# --------------------------------------------------------------------------
# 硬错误：出现即不得继续计算（对应附件C 的 P0 阻断）
# --------------------------------------------------------------------------


class InsufficientDataError(GwFundError):
    """数据不足以计算该指标。

    附件A §2.2：日频 AUM 的三种状态之一是「不可得」——
    成立前 / 清盘后必须写 ``NULL``，**禁止写 0、禁止前向填充**。
    附件A §6.3：无任何份额数据时**拒绝计算**，报 ``SHARE_UNAVAILABLE``。
    """

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        msg = f"[{reason_code}] 数据不足，无法计算"
        if detail:
            msg += f"：{detail}"
        super().__init__(msg)


class IdentityResidualError(GwFundError):
    """三条恒等式残差超阈值（附件A §4.2 / §4.5，附件C CF-01）。

    SOW §5.2 与决策 D16 定的容差：
        同频（日频净值 + 日频份额）  绝对容差 0.01 元
        混频（日频净值 + 季频份额）  相对容差 <= 0.5%

    0.5%–5% 的残差**不得放行**——它足以把「净值涨、基民亏」的结论做反。
    """

    def __init__(
        self,
        identity: str,
        residual: object,
        tolerance: object,
        residual_pct: float | None = None,
    ) -> None:
        self.identity = identity
        self.residual = residual
        self.tolerance = tolerance
        self.residual_pct = residual_pct
        pct = "" if residual_pct is None else f"（相对残差 {residual_pct:.6%}）"
        super().__init__(
            f"恒等式 {identity} 残差超阈值：残差={residual}{pct}，容差={tolerance}"
        )


class AttributionResidualError(GwFundError):
    """规模归因残差超阈值（附件A §5.1 式(6.2)：< 0.5%）。"""


class NoIrrSolutionError(InsufficientDataError):
    """现金流在可行区间内无解（IRR 不存在）。

    典型成因：全部现金流同号（附件A §4.6「现金流符号」要求投资为负、收回为正）。
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__("IRR_NO_SOLUTION", detail)


class PrecisionLevelViolationError(GwFundError):
    """精度等级标注违规。

    附件A §6.4 强制规则：B/C 级数据不得当 A 级展示，这是本产品最容易被
    「技术性忽略」的合规缺陷（对应附件C P0）。
    """


# --------------------------------------------------------------------------
# 软问题：记警告，继续计算，但必须随结果一起输出
# --------------------------------------------------------------------------


class GwFundWarning(UserWarning):
    """gwfund-core 所有警告的基类。"""


class MwrMultiRootWarning(GwFundWarning):
    """IRR 多解（附件A §4.6）。

    现金流多次变号时 IRR 可能多解。必须报告全部根、取最接近 0 的，
    并标注 ``MWR_MULTI_ROOT``，不得只返回其中一个而不声明。
    """


class OtherResidualWarning(GwFundWarning):
    """``Other`` 未被解释的残差接近阈值（附件A §4.5 式(4.5)）。

    量级预期：无折算/无合并/无差错更正时 ``Other`` 应 ≈ 0。
    若达 AUM 的 0.1% 以上，**第一嫌疑是科目归错**（把利润表科目错记进权益变动），
    而不是「数据有问题」。
    """


class NetSubMethodWarning(GwFundWarning):
    """混频方法降级（附件A §6.3）。方案 3 → 2 → 1 的每次降级都必须留痕。"""
