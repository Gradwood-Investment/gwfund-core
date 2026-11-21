"""gwfund-core 公共数据类型。

精度策略（SOW §5.8 / 附件A §1.1 的落地约定）：

* **金额**（元）一律 ``decimal.Decimal`` —— 禁止用 float 累加金额；
  这就是 SOW §5.8「金额内部一律用 Decimal 或整数分存储」的实现。
* **比率/收益率**（无量纲）用 ``float`` —— IRR 需要迭代求根，
  Decimal 在这里既慢又无收益；容差按 V3 判据取 1e-6。
* **百分比入参**统一用「百分数」而非小数（``1.20`` 表示 1.20%），
  与附件A §3.2 的 ``rate_pct`` 字段命名保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Sequence


class PrecisionLevel(str, Enum):
    """精度等级 κ（附件A §6.4）。

    UI 呈现要求：A 绿色「精确」/ B 黄色「估算·季末阶梯」/ C 橙色「插值估算」/
    UNAVAILABLE 灰色「数据缺失」+ 不显示金额。
    """

    A = "A"
    B = "B"
    C = "C"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def label_zh(self) -> str:
        return {
            PrecisionLevel.A: "精确",
            PrecisionLevel.B: "估算·季末阶梯",
            PrecisionLevel.C: "插值估算",
            PrecisionLevel.UNAVAILABLE: "数据缺失",
        }[self]


class MixingMethod(str, Enum):
    """混频方案（附件A §6.2）。"""

    QUARTERLY_DETAIL = "quarterly_detail"  # 方案 3，默认，精度 A
    LINEAR = "linear"  # 方案 2，精度 C
    STEP = "step"  # 方案 1，精度 B


class WeightScheme(str, Enum):
    """季度申赎的日内分配权重方案（附件A §6.2 / 决策 D20）。"""

    UNIFORM = "uniform"  # 默认：最小方差无偏
    V_SHAPE = "v_shape"  # V 型（追涨杀跌）
    MONTH_END = "month_end"  # 月末集中


@dataclass(frozen=True)
class MwrResult:
    """MWR（资金加权收益率 = IRR）的求解结果（附件A §4.6）。"""

    value: float
    """区间 MWR，小数形式（-0.2731 表示 -27.31%）。"""

    annualized: float | None
    """年化 MWR；区间不足 1 年时为 ``None``（附件A §4.6「短期间」）。"""

    multi_root: bool
    """是否检出多解（现金流多次变号）。"""

    roots: tuple[float, ...] = ()
    """检出到的全部根，升序；``multi_root`` 为 False 时通常只有 1 个。"""

    iterations: int = 0
    """牛顿/二分迭代次数，便于复现与排障。"""

    method: str = "newton"
    """最终采用的求解器：``newton`` / ``bisection``。"""


@dataclass(frozen=True)
class IdentityCheck:
    """一条恒等式的校验结果（附件A §4.2）。"""

    identity: str
    """``A`` / ``B`` / ``C``。"""

    lhs: Decimal
    rhs: Decimal
    residual: Decimal
    """``lhs - rhs``。"""

    residual_pct: float | None
    """相对残差；分母不可得时为 ``None``。"""

    tolerance: Decimal
    passed: bool

    def as_line(self) -> str:
        pct = "n/a" if self.residual_pct is None else f"{self.residual_pct:.6%}"
        flag = "PASS" if self.passed else "FAIL"
        return f"[恒等式 {self.identity}] {flag} residual={self.residual} ({pct})"


@dataclass(frozen=True)
class WaterfallSegment:
    """规模归因瀑布的一段（附件A §5.1 式(6.1)）。"""

    key: str
    label_zh: str
    amount: Decimal
    pct_of_start: float | None


@dataclass(frozen=True)
class WaterfallResult:
    """规模归因瀑布结果。"""

    segments: tuple[WaterfallSegment, ...]
    a_start: Decimal
    a_end: Decimal
    residual: Decimal
    residual_pct: float
    passed: bool


@dataclass(frozen=True)
class NavPoint:
    """一个交易日的净值观测。"""

    on: date
    unit_nav: float
    """单位净值（费后），元/份（附件A §1.1 ``N_t``）。"""

    acc_nav: float | None = None
    """累计净值，元/份（``N_acc_t``）。**算业绩不得用它**（决策 D07）。"""

    div_per_share: float = 0.0
    """该日每份现金分红（除息日计），元/份（``d_t``）。"""


@dataclass(frozen=True)
class SharePeriod:
    """一个披露期（通常为一季）的份额与申赎明细。

    对应附件A §1.1 的 ``S_t`` / ``Sub^sh`` / ``Red^sh``，来源为东财 F10 ``gmbd``。
    字段缺失一律 ``None``，**不得写 0**（缺失 ≠ 0，附件A §2.2）。
    """

    start: date
    end: date
    end_shares: Decimal | None = None
    """期末总份额 ``S_t``，份。"""

    end_net_assets: Decimal | None = None
    """期末净资产 ``A_t``，元。用于式(1.1) 质量门。"""

    sub_shares: Decimal | None = None
    """期间申购份额 ``Sub^sh``，份。"""

    red_shares: Decimal | None = None
    """期间赎回份额 ``Red^sh``，份。"""

    div_reinvest_shares: Decimal | None = None
    """红利再投份额 ``Div^reinv``，份（附件A 式(1.4)、陷阱 T1）。"""

    split_adjust_shares: Decimal | None = None
    """份额折算调整 ``ΔS^split``，份（陷阱 T4）。"""

    @property
    def net_sub_shares(self) -> Decimal | None:
        """净申购份额 ``NetSub^sh = Sub^sh - Red^sh``（式(1.3)）。"""
        if self.sub_shares is None or self.red_shares is None:
            return None
        return self.sub_shares - self.red_shares


@dataclass(frozen=True)
class FeeRate:
    """一条费率记录（附件A §3.2 的 ``fund_fee`` 表结构）。"""

    fee_type: str
    """``mgmt`` | ``custody`` | ``sales`` | ``sub`` | ``red`` | ``trade``。"""

    rate_pct: Decimal
    """年化或一次性费率，百分数（``1.20`` = 1.20%）。"""

    effective_from: date
    effective_to: date | None = None
    """``None`` 表示至今。"""

    rate_basis: str = "annual"
    """``annual`` | ``once`` | ``tiered``。"""

    tier: Sequence[dict] = field(default_factory=tuple)
    """``rate_basis='tiered'`` 时的分档定义，边界为**闭区间**（附件A §3.2）。"""


@dataclass(frozen=True)
class FeeAccrual:
    """单日费用计提结果（附件A §3.1）。"""

    on: date
    a_prev: Decimal
    mgmt: Decimal
    custody: Decimal
    sales: Decimal

    @property
    def total(self) -> Decimal:
        return self.mgmt + self.custody + self.sales
