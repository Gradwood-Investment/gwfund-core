"""B02 / W01 修复的负控测试：证明门禁真的拦得住。

**B02 背景**（《裁定书二》P0-2）：原实现 ``r_net_from_identity`` 由 A 式反解 ``R_net``，
``waterfall`` 再拿它算残差 ⇒ 残差恒等于 0。把期末 AUM 改大 27%，残差仍 ``0.00``、
``passed`` 仍 ``True``——门禁完全是摆设。

**W01/DEP-01 背景**（凌霄渡 DEP-01）：``return_decomposition.div_effect`` 符号与
式(6.4) 相反，三分量之和 − 总额 = ``2×Div/A_0``，有分红的基金在 UI 上四根柱加总
必然对不上期末 AUM。

**本文件的测试哲学**：不只测「正常情况通过」，更要测「异常情况**必须**失败」。
只测前半截的门禁 = 没有门禁。
"""

from __future__ import annotations

import warnings
from decimal import Decimal

import pytest

from gwfund_core import attribution
from gwfund_core.attribution import SelfProvisionWarning
from gwfund_core.errors import AttributionResidualError

YI = 100_000_000
REL_TOL = Decimal("0.005")


# ==========================================================================
# B02-1：双路 R_net 对账
# ==========================================================================


class TestRNetTwoPaths:
    def test_three_paths_agree_on_consistent_data(self) -> None:
        """数据一致时，A 式反解 / 汇率路径 / 披露路径三者应接近。"""
        a_start, a_end = 1_000.0, 1_200.0
        net_sub, div = 100.0, 20.0
        rn_identity = attribution.r_net_from_identity(a_start, a_end, net_sub, div)
        # 独立路径：期初 AUM × 区间收益率（收益率由净值链推出，此处假设 12.0%）
        rn_indep = attribution.r_net_from_return(a_start, 0.12)
        rec = attribution.reconcile_r_net(rn_identity, rn_indep, label="unit")
        assert rec.passed is True
        assert str(rec.as_line()).startswith("[R_net 双路 unit] PASS")

    def test_reconcile_fails_when_aum_perturbed_by_1pct(self) -> None:
        """**负控（B02）**：期末 AUM 人为偏 1%，双路对账必须失败。

        这是《裁定书二》要求的判据：「AUM 人为偏 1% 后残差门**必须报错**」。
        """
        a_start, a_end = 1_000.0, 1_200.0
        net_sub, div = 100.0, 0.0
        # 自洽基准：A_end = A_0 + NetSub + R_net ⇒ R_net = 100，收益率 = 10%
        period_return = 0.10

        rn_identity = attribution.r_net_from_identity(a_start, a_end, net_sub, div)
        rn_indep = attribution.r_net_from_return(a_start, period_return)

        # 一致性基准
        assert attribution.reconcile_r_net(rn_identity, rn_indep).passed is True

        # 把期末 AUM 改大 1%
        a_end_wrong = a_end * 1.01
        rn_identity_wrong = attribution.r_net_from_identity(
            a_start, a_end_wrong, net_sub, div
        )
        rec = attribution.reconcile_r_net(rn_identity_wrong, rn_indep)
        assert rec.passed is False, "AUM 偏 1% 却未报错——残差门失效"
        assert rec.diff_pct > float(REL_TOL)

    def test_reconcile_raises_on_fail(self) -> None:
        rec_raise = attribution.reconcile_r_net(
            1_000.0, 900.0, label="raise", raise_on_fail=False
        )
        assert rec_raise.passed is False
        with pytest.raises(AttributionResidualError, match="R_net 双路对账失败"):
            attribution.reconcile_r_net(1_000.0, 900.0, label="raise", raise_on_fail=True)

    def test_disclosure_path(self) -> None:
        """独立路径 ②：```R_gross − Fee```。"""
        assert float(attribution.r_net_from_disclosure(1_000.0, 150.0)) == 850.0

    def test_average_aum_preferred_over_start(self) -> None:
        """规模大幅变动时，用平均 AUM 更准（期初 AUM 会高估/低估）。"""
        rn_start = attribution.r_net_from_return(1_000.0, 0.10)
        rn_avg = attribution.r_net_from_return(1_000.0, 0.10, average_aum=2_000.0)
        assert float(rn_avg) == pytest.approx(200.0)
        assert float(rn_start) == pytest.approx(100.0)


# ==========================================================================
# B02-2：自证守卫（运行时喊出「残差恰好为 0」）
# ==========================================================================


class TestSelfProvisionGuard:
    def test_warns_when_residual_is_exactly_zero(self) -> None:
        """用 A 式反解的 R_net 跑瀑布 → 残差恰为 0 → 必须发警告。"""
        a0, at, ns, dv = 1_000.0, 1_200.0, 100.0, 0.0
        rn = attribution.r_net_from_identity(a0, at, ns, dv)
        with pytest.warns(SelfProvisionWarning, match="残差恰为 0"):
            wf = attribution.waterfall(a0, ns, rn, dv, at)
        assert wf.residual == 0
        assert wf.passed is True  # 数学上确实平，所以只能靠警告提示失效

    def test_independent_path_can_fail_loudly(self) -> None:
        """独立路径在数据不一致时必须抛错（这才是有效的门禁）。"""
        a0, at, ns, dv = 1_000.0, 1_200.0, 100.0, 0.0
        with pytest.raises(AttributionResidualError):
            attribution.waterfall_independent(
                a0, ns, dv, at, period_return=0.05, raise_on_fail=True
            )

    def test_independent_path_passes_on_consistent_data(self) -> None:
        a0, at, ns, dv = 1_000.0, 1_200.0, 100.0, 0.0
        wf = attribution.waterfall_independent(
            a0, ns, dv, at, period_return=0.10, raise_on_fail=True
        )
        assert wf.passed is True
        assert float(wf.segments[2].amount) == pytest.approx(100.0)


# ==========================================================================
# W01 / DEP-01：div_effect 符号 + 闭合性
# ==========================================================================


class TestReturnDecompositionSign:
    def test_div_effect_is_negative(self) -> None:
        """SPEC-GAP-02：``div_effect = −Div_cash/A_0``。"""
        d = attribution.return_decomposition(a_start=1_000.0, net_sub=100.0,
                                             r_net=200.0, div_cash=50.0)
        assert d["div_effect"] == pytest.approx(-0.05), "分红效应必须取负号"

    def test_components_sum_to_total(self) -> None:
        """闭合性：三分量之和 **严格等于** ``delta_a_ratio``（容差 1e-9）。"""
        d = attribution.return_decomposition(a_start=1_000.0, net_sub=100.0,
                                             r_net=200.0, div_cash=50.0)
        total = d["sub_dilution"] + d["nav_growth"] + d["div_effect"]
        assert total == pytest.approx(d["delta_a_ratio"], abs=1e-9)
        assert d["closure_passed"] is True

    def test_old_bug_would_break_by_2x_div(self) -> None:
        """回归：若误用正号，分量和 − 总额恰为 ``2×Div/A_0``（原缺陷指纹）。"""
        a0, ns, rn, dv = 1_000.0, 100.0, 200.0, 50.0
        d = attribution.return_decomposition(a0, ns, rn, dv)
        wrong_div = +dv / a0  # 原实现的符号
        wrong_total = d["sub_dilution"] + d["nav_growth"] + wrong_div
        assert (wrong_total - d["delta_a_ratio"]) == pytest.approx(2 * dv / a0, abs=1e-12)

    def test_closed_against_a_end(self) -> None:
        """用 ``A_T − A_0`` 独立核对总额，一致时通过。"""
        a0, ns, rn, dv = 1_000.0, 100.0, 200.0, 50.0
        a_end = a0 + ns + rn - dv
        d = attribution.return_decomposition(a0, ns, rn, dv, a_end=a_end)
        assert d["closure_passed"] is True
        assert d["closure_residual"] == pytest.approx(0.0, abs=1e-9)

    def test_raises_when_a_end_inconsistent(self) -> None:
        """**负控（W01）**：``A_T`` 与分解不符时必须抛错，不得静默。"""
        a0, ns, rn, dv = 1_000.0, 100.0, 200.0, 50.0
        with pytest.raises(AttributionResidualError, match="不闭合"):
            attribution.return_decomposition(
                a0, ns, rn, dv, a_end=a0 + ns + rn - dv + 33.0
            )

    def test_zero_a0_returns_none_not_zero(self) -> None:
        """分母为 0 → 返回 None，不得返回 0（附件A §5.8）。"""
        d = attribution.return_decomposition(0.0, 100.0, 200.0, 50.0)
        assert all(v is None for v in d.values())


# ==========================================================================
# 回归：000001 2026Q2 样例仍须闭合（不能因改符号而破坏既有口径）
# ==========================================================================


def test_attachment_a_5_3_still_closes_after_fix() -> None:
    """修复后，附件A §5.3 的 000001 2026Q2 归因仍必须 0.00% 残差。"""
    a_start, a_end = 26.44 * YI, 39.38 * YI
    net_sub, div = -2.4841 * YI, 0.0
    rn = attribution.r_net_from_identity(a_start, a_end, net_sub, div)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SelfProvisionWarning)
        wf = attribution.waterfall(a_start, net_sub, rn, div, a_end)
    assert wf.passed is True
    assert wf.residual_pct == pytest.approx(0.0, abs=1e-12)
