"""对账 8 步测试（附件A §8.2 / §8.3）。

重点不在「正常情况通过」，而在：
① **判级规则**（PASS / WARN / BLOCK 的边界）；
② **不得跳步**（步骤完整性校验）；
③ **SKIP 不等于 PASS**（无披露数据时不能算通过）；
④ **步 1–3 失败即 FAIL**（地基不牢，后续无意义）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from gwfund_core import reconcile as rc


# ==========================================================================
# 步 1 · 份额 × 净值
# ==========================================================================


class TestStep1:
    def test_pass_when_within_tolerance(self) -> None:
        s = rc.step1_share_nav_identity(1_000_000, 1.0000, 1_000_050)
        assert s.level == "PASS"

    def test_warn_between_1x_and_2x(self) -> None:
        # dev ≈ 0.8%（> 0.5%，< 1.0%）
        s = rc.step1_share_nav_identity(1_000_000, 1.0000, 992_000)
        assert s.level == "WARN"

    def test_block_beyond_2x(self) -> None:
        # dev ≈ 3%
        s = rc.step1_share_nav_identity(1_000_000, 1.0000, 970_000)
        assert s.level == "BLOCK"

    def test_missing_input_is_block_not_pass(self) -> None:
        s = rc.step1_share_nav_identity(None, 1.0, 1_000_000)
        assert s.level == "BLOCK"

    def test_nonpositive_rejected(self) -> None:
        assert rc.step1_share_nav_identity(0, 1.0, 1_000_000).level == "BLOCK"
        assert rc.step1_share_nav_identity(1_000_000, 1.0, 0).level == "BLOCK"


# ==========================================================================
# 步 2 · 份额连续性
# ==========================================================================


class TestStep2:
    def test_pass_when_residual_within_half_cent(self) -> None:
        # 100 万份 = 100 万份（单位：万份 → 容差 100 万份 = 100）
        s = rc.step2_share_continuity(304_853.86, 302_171.32, 2_682.54)
        assert s.level == "PASS"

    def test_reinvest_missing_is_caught(self) -> None:
        """T1：漏掉红利再投 → 残差 = 再投份额，必须报出。"""
        s = rc.step2_share_continuity(
            s_end_wan=1_100_000, s_prev_wan=1_000_000, net_sub_shares_wan=0,
            div_reinvest_wan=0,
        )
        assert s.level != "PASS"
        assert "100000" in s.detail

    def test_reinvest_included_passes(self) -> None:
        s = rc.step2_share_continuity(
            s_end_wan=1_100_000, s_prev_wan=1_000_000, net_sub_shares_wan=0,
            div_reinvest_wan=100_000,
        )
        assert s.level == "PASS"


# ==========================================================================
# 步 3 · 现金分红
# ==========================================================================


class TestStep3:
    def test_skip_when_no_disclosure(self) -> None:
        """**SKIP ≠ PASS**：没有公告数据不能算通过。"""
        s = rc.step3_dividend_amount(18_730, None)
        assert s.level == "SKIP"
        assert "不得视为通过" in s.detail

    def test_pass_when_matching(self) -> None:
        assert rc.step3_dividend_amount(18_730, 18_730, n_events=25).level == "PASS"

    def test_block_when_zero_but_computed_nonzero(self) -> None:
        assert rc.step3_dividend_amount(100, 0).level == "BLOCK"

    def test_warn_when_off_by_1pct(self) -> None:
        assert rc.step3_dividend_amount(1010, 1000).level == "WARN"


# ==========================================================================
# 步 4–8
# ==========================================================================


class TestStep4To8:
    def test_step4_pass_and_warn(self) -> None:
        assert rc.step4_net_sub_cross_check(1000, 1005).level == "PASS"
        assert rc.step4_net_sub_cross_check(1000, 1030).level == "BLOCK"
        assert rc.step4_net_sub_cross_check(None, 1000).level == "SKIP"

    def test_step5_tolerance_by_period(self) -> None:
        """T-4：年 ±2% / 半年 ±3% / 季 ±5%。"""
        # 2.5% 偏差：年在容差外（WARN），半年在容差内（PASS）
        assert rc.step5_mgmt_fee(1025, 1000, period="year").level == "WARN"
        assert rc.step5_mgmt_fee(1025, 1000, period="half").level == "PASS"
        # 4% 偏差：半年 WARN，季 PASS
        assert rc.step5_mgmt_fee(1040, 1000, period="half").level == "WARN"
        assert rc.step5_mgmt_fee(1040, 1000, period="quarter").level == "PASS"

    def test_step5_skip_without_disclosure(self) -> None:
        assert rc.step5_mgmt_fee(100, None).level == "SKIP"

    def test_step6_rate_mismatch(self) -> None:
        assert rc.step6_fee_rate(1.20, 0.20, 1.20, 0.20).level == "PASS"
        assert rc.step6_fee_rate(1.20, 0.20, 1.25, 0.20).level == "BLOCK"
        assert rc.step6_fee_rate(None, 0.20, 1.20, 0.20).level == "SKIP"

    def test_step7_period(self) -> None:
        assert rc.step7_period_alignment("2025-01-01", "2025-12-31", "2025-01-01", "2025-12-31").level == "PASS"
        assert rc.step7_period_alignment("2025-01-01", "2025-06-30", "2025-01-01", "2025-12-31").level == "WARN"

    def test_step8_other_residual(self) -> None:
        assert rc.step8_other_residual(40_000, 10_000_000, 10_000_000).level == "PASS"
        s = rc.step8_other_residual(60_000, 10_000_000, 10_000_000)
        assert s.level == "WARN"
        # 量级提示：ρ > 0.1% 时提示「优先排查科目归错」
        big = rc.step8_other_residual(200_000, 10_000_000, 10_000_000)
        assert "科目归错" in big.detail


# ==========================================================================
# 汇总与裁定（附件A §8.3）
# ==========================================================================


def _all_pass() -> list[rc.ReconcileStep]:
    return [
        rc.step1_share_nav_identity(1_000_000, 1.0, 1_000_000),
        rc.step2_share_continuity(1_000_000, 1_000_000, 0),
        rc.step3_dividend_amount(0, 0),
        rc.step4_net_sub_cross_check(100, 100),
        rc.step5_mgmt_fee(100, 100),
        rc.step6_fee_rate(1.2, 0.2, 1.2, 0.2),
        rc.step7_period_alignment("2025-01-01", "2025-12-31", "2025-01-01", "2025-12-31"),
        rc.step8_other_residual(0, 1_000_000, 1_000_000),
    ]


class TestReport:
    def test_all_pass_verdict(self) -> None:
        rep = rc.build_report("000001", "2025 年度", _all_pass())
        assert rep.verdict == "PASS"
        assert rep.n_warn == 0 and rep.n_block == 0

    def test_warn_only_is_conditional_pass(self) -> None:
        steps = _all_pass()
        steps[4] = rc.step5_mgmt_fee(1025, 1000, period="year")  # 步 5 WARN
        rep = rc.build_report("000001", "2025 年度", steps)
        assert rep.verdict == "CONDITIONAL_PASS"
        assert rep.n_warn == 1

    def test_block_is_fail(self) -> None:
        steps = _all_pass()
        steps[7] = rc.step8_other_residual(500_000, 1_000_000, 1_000_000)  # BLOCK
        rep = rc.build_report("000001", "2025 年度", steps)
        assert rep.verdict == "FAIL"

    def test_foundation_step_warn_is_fail(self) -> None:
        """步 1–3 是地基：即便只是 WARN，总体也必须 FAIL。"""
        steps = _all_pass()
        steps[2] = rc.step3_dividend_amount(1010, 1000)  # 步 3 WARN
        rep = rc.build_report("000001", "2025 年度", steps)
        assert rep.verdict == "FAIL"

    def test_missing_step_rejected(self) -> None:
        """不得跳步：缺任一步必须抛错。"""
        steps = _all_pass()[:7]
        with pytest.raises(ValueError, match="不得跳步"):
            rc.build_report("000001", "2025 年度", steps)

    def test_out_of_order_rejected(self) -> None:
        steps = _all_pass()
        steps[0], steps[1] = steps[1], steps[0]
        with pytest.raises(ValueError, match="按序"):
            rc.build_report("000001", "2025 年度", steps)

    def test_duplicate_step_rejected(self) -> None:
        steps = _all_pass()
        steps[1] = steps[0]
        with pytest.raises(ValueError):
            rc.build_report("000001", "2025 年度", steps)

    def test_render_matches_spec_format(self) -> None:
        """输出格式对齐附件A §8.2 的结构化样例。"""
        rep = rc.build_report("000001", "2025 年度", _all_pass())
        text = rep.render()
        assert text.startswith("[对账 000001 | 2025 年度]")
        for i in range(1, 9):
            assert f"  步{i} " in text
        assert "→ 总体: PASS" in text
        assert "0 WARN, 0 BLOCK" in text
