"""对账 8 步（附件A §8.2）：本系统的「黄金标准」校验流程。

**为什么需要它**：恒等式闭合只证明「账平」，不证明「账对」。
真正能发现问题的是**与披露值逐项对账**——管理费、托管费、投资收益、期末规模，
每一项都要和定期报告或独立源比。附件A §8.2 把它固化成 8 个必须按序执行的步骤，
并规定「不得跳步」。

**执行纪律**（附件A §8.2）：

* 必须按 1→8 顺序执行，**前一步 BLOCK 时后续步骤仍要跑完**（便于一次性定位问题）；
* 任一步 BLOCK（> 2 倍容差）或步 1–3 失败 ⇒ 总体 ``FAIL``，**阻断，不得进入交付**；
* 仅有 WARN（1–2 项超容差但 < 2 倍容差）⇒ ``CONDITIONAL_PASS``，放行但报告标注；
* 全部在容差内 ⇒ ``PASS``。

**容差档位**（附件C §5.0 容差九档）：

======  ==========================================  ==================
步      判据                                        档位
======  ==========================================  ==================
1       份额×净值恒等 |S·N/A − 1| ≤ 0.5%             T-3（混频）
2       份额连续性残差 ≤ 0.01 亿份                     附件A §8.2
3       现金分红与公告逐笔一致                          精确
4       净申购：方案3 vs 方案2 差异 < 1%               附件A §8.2
5       管理费 Σ vs 年报（年 ±2% / 半年 ±3%）          T-4
6       费率与合同一致                                精确
7       归属期间与自然年一致                            精确
8       ``Other`` 残差 ρ < 0.5%                        T-3
======  ==========================================  ==================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Level = Literal["PASS", "WARN", "BLOCK", "SKIP"]
Verdict = Literal["PASS", "CONDITIONAL_PASS", "FAIL"]

_CENT = Decimal("0.01")


def _d(v: Decimal | int | float | str) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


@dataclass(frozen=True)
class ReconcileStep:
    """单步对账结果。"""

    step: int
    name: str
    level: Level
    detail: str

    def as_line(self) -> str:
        return f"  步{self.step} {self.name:<12}: {self.level:<5}  ({self.detail})"


@dataclass(frozen=True)
class ReconcileReport:
    """8 步对账总报告（可直接打印成附件A §8.2 要求的结构化格式）。"""

    fund_code: str
    period_label: str
    steps: tuple[ReconcileStep, ...] = field(default_factory=tuple)

    @property
    def n_pass(self) -> int:
        return sum(1 for s in self.steps if s.level == "PASS")

    @property
    def n_warn(self) -> int:
        return sum(1 for s in self.steps if s.level == "WARN")

    @property
    def n_block(self) -> int:
        return sum(1 for s in self.steps if s.level == "BLOCK")

    @property
    def verdict(self) -> Verdict:
        """放行裁定（附件A §8.3）。

        * 任一步 BLOCK ⇒ ``FAIL``；
        * 步 1–3 失败（即便是 WARN）⇒ ``FAIL``——这三步是地基，超容差即不可信；
        * 仅 WARN ⇒ ``CONDITIONAL_PASS``；
        * 全 PASS ⇒ ``PASS``。
        """
        if self.n_block > 0:
            return "FAIL"
        foundation_bad = any(
            s.level in ("WARN", "BLOCK") and s.step in (1, 2, 3) for s in self.steps
        )
        if foundation_bad:
            return "FAIL"
        if self.n_warn > 0:
            return "CONDITIONAL_PASS"
        return "PASS"

    def render(self) -> str:
        """渲染附件A §8.2 规定的结构化输出格式。"""
        head = f"[对账 {self.fund_code} | {self.period_label}]"
        lines = [head] + [s.as_line() for s in self.steps]
        lines.append(
            f"  → 总体: {self.verdict} "
            f"({self.n_warn} WARN, {self.n_block} BLOCK)"
        )
        return "\n".join(lines)


def _grade(
    deviation: float,
    tolerance: float,
    *,
    block_multiple: float = 2.0,
) -> Level:
    """按「超容差倍数」判级：≤1 容差 PASS；≤2 倍 WARN；>2 倍 BLOCK。"""
    if deviation <= tolerance:
        return "PASS"
    if deviation <= tolerance * block_multiple:
        return "WARN"
    return "BLOCK"


# --------------------------------------------------------------------------
# 步 1 · 份额 × 净值 ≈ 期末净资产（式 1.1）
# --------------------------------------------------------------------------


def step1_share_nav_identity(
    shares_wan: Decimal | int | float | str | None,
    unit_nav: float | None,
    net_assets_wan: Decimal | int | float | str | None,
    *,
    tolerance: float = 0.005,
) -> ReconcileStep:
    """步 1：``|S·N/A − 1| ≤ 0.5%``（附件A §1.3 / T-3）。"""
    if shares_wan is None or unit_nav is None or net_assets_wan is None:
        return ReconcileStep(1, "份额×净值", "BLOCK", "输入缺失（份额/净值/净资产）")
    a = _d(net_assets_wan)
    if a <= 0 or _d(shares_wan) <= 0:
        return ReconcileStep(1, "份额×净值", "BLOCK", f"非正值：S={shares_wan}, A={a}")
    ratio = float(_d(shares_wan) * _d(unit_nav) / a)
    dev = abs(ratio - 1.0)
    lvl = _grade(dev, tolerance)
    return ReconcileStep(1, "份额×净值", lvl, f"ratio={ratio:.6f}, max dev {dev:.4%}")


# --------------------------------------------------------------------------
# 步 2 · 份额连续性（式 1.4）
# --------------------------------------------------------------------------


def step2_share_continuity(
    s_end_wan: Decimal | int | float | str,
    s_prev_wan: Decimal | int | float | str,
    net_sub_shares_wan: Decimal | int | float | str,
    *,
    div_reinvest_wan: Decimal | int | float | str = 0,
    split_adjust_wan: Decimal | int | float | str = 0,
    tolerance_wan: Decimal = Decimal("100"),
) -> ReconcileStep:
    """步 2：``S_t = S_{t−1} + NetSub + Div^reinv + ΔS^split``，残差 ≤ 0.01 亿份（=100 万份）。

    Note:
        容差取自 F10 披露精度（0.01 亿份）。若漏掉红利再投或份额折算，
        本步会直接暴露——这是附件A §1.2 说的「最致命的实现错误」。
    """
    lhs = _d(s_end_wan)
    rhs = _d(s_prev_wan) + _d(net_sub_shares_wan) + _d(div_reinvest_wan) + _d(split_adjust_wan)
    residual = (lhs - rhs).quantize(Decimal("0.0001"))
    dev = float(abs(residual) / _d(s_prev_wan)) if _d(s_prev_wan) else 1.0
    lvl: Level = "PASS" if abs(residual) <= tolerance_wan else "WARN"
    if abs(residual) > tolerance_wan * 10:
        lvl = "BLOCK"
    return ReconcileStep(
        2, "份额连续性", lvl, f"resid {residual} 万份（相对 {dev:.4%}）"
    )


# --------------------------------------------------------------------------
# 步 3 · 现金分红金额
# --------------------------------------------------------------------------


def step3_dividend_amount(
    computed_wan: Decimal | int | float | str,
    disclosed_wan: Decimal | int | float | str | None,
    *,
    n_events: int | None = None,
) -> ReconcileStep:
    """步 3：``Div_cash`` 与分红公告逐笔核对。

    ``disclosed_wan`` 为 ``None`` 时判 ``SKIP``——**不能因为「没有公告数据」就判 PASS**。
    """
    if disclosed_wan is None:
        return ReconcileStep(
            3, "现金分红", "SKIP",
            f"无公告数据可核（系统测算 {_d(computed_wan)} 万元）；不得视为通过",
        )
    c, d = _d(computed_wan), _d(disclosed_wan)
    if d == 0:
        lvl: Level = "PASS" if abs(c) < _CENT else "BLOCK"
        return ReconcileStep(3, "现金分红", lvl, f"公告为 0，系统 {c}")
    dev = float(abs(c - d) / abs(d))
    lvl = _grade(dev, 0.005)
    extra = f", {n_events} 笔" if n_events is not None else ""
    return ReconcileStep(3, "现金分红", lvl, f"系统 {c} vs 公告 {d}{extra}, 差 {dev:.4%}")


# --------------------------------------------------------------------------
# 步 4 · 净申购：方案 3 vs 方案 2
# --------------------------------------------------------------------------


def step4_net_sub_cross_check(
    net_sub_quarterly_detail: Decimal | int | float | str | None,
    net_sub_linear: Decimal | int | float | str | None,
    *,
    tolerance: float = 0.01,
) -> ReconcileStep:
    """步 4：方案 3（明细法）与方案 2（插值法）净申购差异 < 1%。

    两者都依赖「日内分配」假设；差异过大说明假设偏离现实，需换权重方案
    （附件A §6.2 / 决策 D20 的校准触发条件）。
    """
    if net_sub_quarterly_detail is None or net_sub_linear is None:
        return ReconcileStep(4, "净申购金额", "SKIP", "缺少方案 2 或方案 3 的 NetSub")
    a, b = _d(net_sub_quarterly_detail), _d(net_sub_linear)
    denom = max(abs(a), abs(b))
    if denom == 0:
        return ReconcileStep(4, "净申购金额", "PASS", "两者均为 0")
    dev = float(abs(a - b) / denom)
    lvl = _grade(dev, tolerance)
    return ReconcileStep(4, "净申购金额", lvl, f"方案3 {a} vs 方案2 {b}, 差 {dev:.4%}")


# --------------------------------------------------------------------------
# 步 5 · 管理费与年报对账
# --------------------------------------------------------------------------


def step5_mgmt_fee(
    computed_wan: Decimal | int | float | str,
    disclosed_wan: Decimal | int | float | str | None,
    *,
    period: Literal["year", "half", "quarter"] = "year",
) -> ReconcileStep:
    """步 5：``Σ F_mgmt`` vs 年报/半年报「当期发生的基金管理费」。

    容差按期间分档（附件C T-4）：年 ±2% / 半年 ±3% / 季 ±5%。
    """
    if disclosed_wan is None:
        return ReconcileStep(5, "管理费", "SKIP", f"无披露值可核（系统 {_d(computed_wan)} 万元）")
    tol = {"year": 0.02, "half": 0.03, "quarter": 0.05}[period]
    c, d = _d(computed_wan), _d(disclosed_wan)
    if d == 0:
        return ReconcileStep(5, "管理费", "SKIP", "披露值为 0，无法算相对偏差")
    dev = float(abs(c - d) / abs(d))
    lvl = _grade(dev, tol)
    return ReconcileStep(
        5, "管理费", lvl, f"引擎 {c} vs 披露 {d}（{period}，容差 {tol:.0%}），差 {dev:.2%}"
    )


# --------------------------------------------------------------------------
# 步 6 · 费率与合同一致
# --------------------------------------------------------------------------


def step6_fee_rate(
    mgmt_rate_pct: float | None,
    custody_rate_pct: float | None,
    contract_mgmt_pct: float | None,
    contract_custody_pct: float | None,
    *,
    tol_pct: float = 1e-4,
) -> ReconcileStep:
    """步 6：费率表与基金合同一致（绝对容差 1e-4 个百分点）。"""
    missing = [
        name
        for name, v in (
            ("管理费率", mgmt_rate_pct),
            ("托管费率", custody_rate_pct),
            ("合同管理费率", contract_mgmt_pct),
            ("合同托管费率", contract_custody_pct),
        )
        if v is None
    ]
    if missing:
        return ReconcileStep(6, "费率表", "SKIP", f"缺：{'、'.join(missing)}")
    d1 = abs(float(mgmt_rate_pct) - float(contract_mgmt_pct))  # type: ignore[arg-type]
    d2 = abs(float(custody_rate_pct) - float(contract_custody_pct))  # type: ignore[arg-type]
    dev = max(d1, d2)
    lvl: Level = "PASS" if dev <= tol_pct else ("WARN" if dev <= tol_pct * 10 else "BLOCK")
    return ReconcileStep(
        6, "费率表", lvl,
        f"mgmt {mgmt_rate_pct}%/cust {custody_rate_pct}%，与合同最大差 {dev:.6f} pct",
    )


# --------------------------------------------------------------------------
# 步 7 · 归属期间
# --------------------------------------------------------------------------


def step7_period_alignment(
    period_start: str,
    period_end: str,
    expected_start: str,
    expected_end: str,
) -> ReconcileStep:
    """步 7：计算期间与预期归属期间一致（基金成立/清盘期需按实际天数裁剪）。"""
    if period_start == expected_start and period_end == expected_end:
        return ReconcileStep(7, "归属期间", "PASS", f"{period_start}~{period_end}")
    return ReconcileStep(
        7, "归属期间", "WARN",
        f"实际 {period_start}~{period_end} vs 预期 {expected_start}~{expected_end}",
    )


# --------------------------------------------------------------------------
# 步 8 · Other 残差
# --------------------------------------------------------------------------


def step8_other_residual(
    other_unexplained_wan: Decimal | int | float | str,
    a_start_wan: Decimal | int | float | str,
    a_end_wan: Decimal | int | float | str,
    *,
    tolerance: float = 0.005,
) -> ReconcileStep:
    """步 8：``ρ = |Other_unexplained| / max(|A_0|,|A_T|) < 0.5%``（式 4.5）。

    量级预期：正常季度 ``Other`` 应 ≈ 0；达 0.1% 以上时**第一嫌疑是科目归错**
    （把利润表科目错记进权益变动），不是「数据有问题」。
    """
    denom = max(abs(_d(a_start_wan)), abs(_d(a_end_wan)))
    if denom == 0:
        return ReconcileStep(8, "Other 残差", "SKIP", "期初/期末 AUM 均为 0")
    rho = float(abs(_d(other_unexplained_wan)) / denom)
    lvl = _grade(rho, tolerance)
    hint = "；量级偏大，优先排查科目归错" if rho > 0.001 else ""
    return ReconcileStep(8, "Other 残差", lvl, f"ρ={rho:.6%}{hint}")


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def build_report(
    fund_code: str,
    period_label: str,
    steps: list[ReconcileStep],
) -> ReconcileReport:
    """组装 8 步报告；**校验步骤编号完整且有序**（不得跳步）。"""
    nums = [s.step for s in steps]
    if nums != sorted(nums):
        raise ValueError(f"对账步骤必须按序：{nums}")
    if len(set(nums)) != len(nums):
        raise ValueError(f"对账步骤重复：{nums}")
    missing = set(range(1, 9)) - set(nums)
    if missing:
        raise ValueError(f"对账步骤缺失 {sorted(missing)}——附件A §8.2 要求不得跳步")
    return ReconcileReport(fund_code=fund_code, period_label=period_label, steps=tuple(steps))
