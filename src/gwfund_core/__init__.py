"""gwfund-core —— 渐木公募基金研究系统 · 核心算法库。

**本包是四端（桌面 / 网站 / 小程序 / API）的唯一算法真相源**（SOW §7.1、附件C MC-01）。
任何端不得复制本包内的公式；四端一致性靠「版本锁 + 黄金向量 + 每日比对」保证。

设计约束（SOW §7.3 C3/C10、附件A §10）：

* **纯函数**：无 I/O、无网络、无文件读写、无全局可变状态；
* **零运行时依赖**：只用 Python 标准库（``decimal`` / ``math`` / ``dataclasses``）；
* **缺失即 None**：缺失值一律 ``None``，禁止写 ``0``、禁止前向填充（附件A §2.2）；
* **超阈值抛异常**：恒等式与归因残差超阈值时抛异常，不静默（附件A §10 E7）。

典型用法::

    from gwfund_core import returns, fees, identities, attribution, metrics

    # TWR：日频 r_t 链式连乘
    rs = [returns.daily_return(a, b) for a, b in zip(navs[:-1], navs[1:])]
    twr = returns.chain_return(rs)

    # MWR：现金流求 IRR（投资为负、收回为正）
    res = returns.irr([-1_000_000, -500_000, 0, 1_520_000])

    # 基民收益缺口
    gap = returns.investor_gap(twr, res.value)

    # 三条恒等式
    check = identities.check_identity_a(a_start=..., a_end=..., r_net=...,
                                        net_sub=..., div_cash=...)

    # 归因（B02：必须用独立构造的 R_net）
    wf = attribution.waterfall_independent(A0, net_sub, div, AT, period_return=twr)

免责声明：本库为研究工具的计算内核，所有测算基于公开披露数据与历史区间。
**历史测算结果不等于未来表现，不构成投资建议。**
"""

from __future__ import annotations

from . import attribution, chase, fees, identities, metrics, mixing, reconcile, returns
from .attribution import RNetReconciliation, SelfProvisionWarning
from .errors import (
    AttributionResidualError,
    GwFundError,
    GwFundWarning,
    IdentityResidualError,
    InsufficientDataError,
    MwrMultiRootWarning,
    NetSubMethodWarning,
    NoIrrSolutionError,
    OtherResidualWarning,
    PrecisionLevelViolationError,
)
from .metrics import DrawdownResult
from .reconcile import ReconcileReport, ReconcileStep
from .types import (
    FeeAccrual,
    FeeRate,
    IdentityCheck,
    MixingMethod,
    MwrResult,
    NavPoint,
    PrecisionLevel,
    SharePeriod,
    WaterfallResult,
    WaterfallSegment,
    WeightScheme,
)

__version__ = "0.4.0"

__all__ = [
    "__version__",
    # —— 子模块 ——
    "attribution",
    "chase",
    "fees",
    "identities",
    "metrics",
    "reconcile",
    "mixing",
    "returns",
    # —— 类型 ——
    "DrawdownResult",
    "FeeAccrual",
    "FeeRate",
    "IdentityCheck",
    "MixingMethod",
    "MwrResult",
    "NavPoint",
    "PrecisionLevel",
    "RNetReconciliation",
    "SharePeriod",
    "WaterfallResult",
    "WaterfallSegment",
    "WeightScheme",
    # —— 异常与警告 ——
    "AttributionResidualError",
    "GwFundError",
    "GwFundWarning",
    "IdentityResidualError",
    "InsufficientDataError",
    "MwrMultiRootWarning",
    "NetSubMethodWarning",
    "NoIrrSolutionError",
    "OtherResidualWarning",
    "PrecisionLevelViolationError",
    "ReconcileReport",
    "ReconcileStep",
    "SelfProvisionWarning",
]

#: 算法口径所依据的规范版本（写进 API 响应 meta，便于复现）
SPEC_VERSION = "附件A v1.1（含 SPEC-GAP-01~05）/ SOW v1.1"
