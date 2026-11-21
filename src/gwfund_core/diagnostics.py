"""归因诊断的可见性与拦截（裁定书三 §二 C1–C4）。

《裁定书三》对「R_net 双路对账仅作诊断」追加了 4 条硬条件——**诊断 ≠ 可以静默**：

===  ============================================================  ============================
#    条件                                                          本模块对应实现
===  ============================================================  ============================
C1   每次归因输出必须携带 ``reconcile_gap`` + ``estimate_grade``    :func:`build_attribution`
C2   C 级归因**默认不返回**，需 ``include_diagnostics=True``         :func:`should_include_components`
C3   对外文章/分享卡/报告**禁止引用 C 级分量**                       :func:`assert_export_allowed`
C4   双路失败必须 **WARN 日志 + 页面角标**，不得静默降级             :func:`log_reconcile_warning`
===  ============================================================  ============================

**阶梯门（写死）**：M1 仅诊断 → M2 ≤5% → M3 ≤1%。
若 M2 末仍达不到 ≤5%：归因分量**永久标 C**，产品只展示区间说明、不展示分量数值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: 归因分量的精度等级（A/B 可对外，C 不可）
GRADE_A, GRADE_B, GRADE_C = "A", "B", "C"

#: 阶梯门目标（《裁定书三》§二，写死不得再议）
LADDER_TARGETS: dict[str, float | None] = {
    "M1": None,  # 仅诊断，无量化目标
    "M2": 0.05,  # ≤5%
    "M3": 0.01,  # ≤1%
}


class DiagnosticNotAllowedError(PermissionError):
    """C3：C 级归因分量禁止出现在对外产物中。"""


@dataclass(frozen=True)
class AttributionEnvelope:
    """归因输出的标准信封——**C1 要求这两个字段永远在场**。"""

    fund_code: str
    reconcile_gap: float | None
    """R_net 双路对账相对差（小数）。``None`` 表示无法计算。"""
    estimate_grade: str
    """``A`` / ``B`` / ``C``。C 级分量默认不返回（C2）。"""
    components: dict[str, Any] | None
    """瀑布分量。C 级且未显式请求诊断时为 ``None``。"""
    reconcile_passed: bool
    diagnostics_included: bool
    badge: str
    """页面角标文案（C4 要求 UI 留痕）。"""
    alert_code: str = "RNET_RECONCILE_OK"
    """C4 日志码，供 API 层直接 `logger.warning(alert_code, ...)`。"""
    alert_message: str = ""
    notes: list[str] = field(default_factory=list)


def should_include_components(
    estimate_grade: str,
    *,
    include_diagnostics: bool = False,
) -> bool:
    """C2：C 级分量是否允许返回。

    Args:
        estimate_grade: 当前精度等级。
        include_diagnostics: 调用方是否显式请求诊断。

    Returns:
        ``True`` 表示允许返回分量数值。

    Note:
        **A/B 级默认返回**；**C 级必须显式请求**才返回。
        这条的意义：让「拿不到可信分量」成为默认行为，
        而不是靠调用方自觉不展示。
    """
    if estimate_grade in (GRADE_A, GRADE_B):
        return True
    return bool(include_diagnostics)


def assert_export_allowed(
    estimate_grade: str,
    *,
    target: Literal["article", "share_card", "report", "api"] = "report",
) -> None:
    """C3：对外产物引用 C 级分量时**硬拦截**。

    Args:
        estimate_grade: 被引用分量的精度等级。
        target: 产物类型。

    Raises:
        DiagnosticNotAllowedError: 等级为 C 且目标是对外产物。

    Note:
        对外产物 = 文章 / 分享卡 / 报告。``api`` 走 C2 的显式请求机制，不在此拦截。
    """
    if estimate_grade != GRADE_C:
        return
    if target in ("article", "share_card", "report"):
        raise DiagnosticNotAllowedError(
            f"C3：{target} 禁止引用 C 级归因分量——"
            f"归因双路对账未达 0.5%（M1 仅诊断），分量不可信。"
            f"请改用「区间说明」表述，或等 M2 双路 ≤5% 后再引用。"
        )


def badge_text(estimate_grade: str, reconcile_passed: bool) -> str:
    """C4：页面角标文案（UI 必须显示，不得静默）。"""
    if estimate_grade == GRADE_C:
        return "⚠ 归因仅供参考（C 级）：R_net 双路未对齐，不展示分量数值"
    if not reconcile_passed:
        return "⚠ R_net 双路对账未通过，归因精度已降级"
    return ""


def reconcile_alert(
    reconcile_gap: float | None,
    *,
    threshold: float = 0.005,
) -> tuple[bool, str, str]:
    """C4：判定是否需要告警，返回 (是否告警, 日志码, 文案)。

    **本函数不写日志**——core 是零依赖纯函数库（架构门禁止 `logging` 等 I/O）。
    由 API 层拿返回值去 `logger.warning(...)`，这样职责清晰：
    core 判定「该不该叫」，API 决定「叫到哪」。

    Args:
        reconcile_gap: 双路相对差（小数）。``None`` = 无法计算。
        threshold: 容差，默认 0.5%。

    Returns:
        ``(triggered, code, message)``。``triggered=False`` 时 ``code`` 为 ``RNET_RECONCILE_OK``。
    """
    if reconcile_gap is None:
        return (
            True,
            "RNET_RECONCILE_UNAVAILABLE",
            "双路对账无法计算（缺独立路径）——不得静默当通过",
        )
    if abs(reconcile_gap) > threshold:
        return (
            True,
            "RNET_RECONCILE_FAIL",
            f"gap={reconcile_gap:.2%} threshold={threshold:.2%} "
            "→ 归因降级为 C，分量默认不返回（裁定书三 C2/C4）",
        )
    return (False, "RNET_RECONCILE_OK", f"gap={reconcile_gap:.4%}")


def build_attribution(
    fund_code: str,
    *,
    segments: list[dict[str, Any]] | None,
    reconcile_gap: float | None,
    estimate_grade: str,
    include_diagnostics: bool = False,
    residual_pct: float | None = None,
    rnet_reconcile_passed: bool | None = None,
) -> AttributionEnvelope:
    """组装归因信封，统一执行 C1–C4。

    Args:
        segments: 瀑布分量（内部计算用）。
        reconcile_gap: 双路相对差。
        estimate_grade: 精度等级。
        include_diagnostics: 是否显式请求诊断分量。
        residual_pct: 归因残差（按定义为 0，仅留痕）。
        rnet_reconcile_passed: 若外部已判定，直接采用。

    Returns:
        :class:`AttributionEnvelope`——**``reconcile_gap`` 与 ``estimate_grade`` 永远在场**（C1）。
    """
    passed = (
        rnet_reconcile_passed
        if rnet_reconcile_passed is not None
        else (reconcile_gap is not None and abs(reconcile_gap) <= 0.005)
    )
    # C4：失败必须留痕——本函数只判定，日志由调用方（API）写
    _triggered, alert_code, alert_msg = reconcile_alert(reconcile_gap)

    # C2：C 级默认不返回分量
    include = should_include_components(
        estimate_grade, include_diagnostics=include_diagnostics
    )
    notes: list[str] = []
    if not include:
        notes.append(
            "C2：归因分量默认不返回（C 级）。如需排查，请带 include_diagnostics=true。"
        )
    if residual_pct is not None:
        notes.append(
            f"归因残差 {residual_pct:.6%}（按定义为 0，不是门禁；门禁是 R_net 双路对账）"
        )

    return AttributionEnvelope(
        fund_code=fund_code,
        reconcile_gap=reconcile_gap,
        estimate_grade=estimate_grade,
        components=segments if include else None,
        reconcile_passed=passed,
        diagnostics_included=include,
        badge=badge_text(estimate_grade, passed),
        alert_code=alert_code,
        alert_message=alert_msg,
        notes=notes,
    )


def ladder_status(stage: str, reconcile_gap: float | None) -> dict[str, Any]:
    """阶梯门进度（M1 诊断 → M2 ≤5% → M3 ≤1%）。"""
    target = LADDER_TARGETS.get(stage)
    if target is None:
        return {
            "stage": stage,
            "target": None,
            "gap": reconcile_gap,
            "reached": None,
            "note": "M1 仅诊断，无量化目标（裁定书三 §二）",
        }
    reached = reconcile_gap is not None and abs(reconcile_gap) <= target
    return {
        "stage": stage,
        "target": target,
        "gap": reconcile_gap,
        "reached": reached,
        "note": (
            "已达标" if reached
            else f"未达 {target:.0%}；若 M2 末仍不达，归因分量永久标 C（只展示区间说明）"
        ),
    }
