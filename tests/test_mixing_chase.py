"""混频与追高模块测试（附件A §6 / §7，判据 V6 / V10）。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from gwfund_core import chase, mixing
from gwfund_core.errors import InsufficientDataError
from gwfund_core.types import MixingMethod, PrecisionLevel, WeightScheme

YI = 100_000_000


# ==========================================================================
# 方案选择与精度等级（§6.3 / §6.4）
# ==========================================================================


class TestMethodSelection:
    def test_quarterly_detail_when_sub_red_present(self) -> None:
        m = mixing.choose_method(
            has_sub_red=True, has_start_end_share=True, has_end_share=True
        )
        assert m is MixingMethod.QUARTERLY_DETAIL

    def test_linear_when_only_start_end(self) -> None:
        m = mixing.choose_method(
            has_sub_red=False, has_start_end_share=True, has_end_share=True
        )
        assert m is MixingMethod.LINEAR

    def test_step_when_only_end(self) -> None:
        m = mixing.choose_method(
            has_sub_red=False, has_start_end_share=False, has_end_share=True
        )
        assert m is MixingMethod.STEP

    def test_refuse_when_no_share(self) -> None:
        with pytest.raises(InsufficientDataError) as ei:
            mixing.choose_method(
                has_sub_red=False, has_start_end_share=False, has_end_share=False
            )
        assert ei.value.reason_code == "SHARE_UNAVAILABLE"

    def test_daily_aum_never_grade_a(self) -> None:
        """§6.4 规则 1：任何日频 AUM 曲线一律最高标 B。"""
        for method in MixingMethod:
            level = mixing.precision_for(method, is_daily_aum=True)
            assert level is not PrecisionLevel.A

    def test_quarterly_detail_interval_can_be_grade_a(self) -> None:
        level = mixing.precision_for(
            MixingMethod.QUARTERLY_DETAIL, is_daily_aum=False, attribution_residual_ok=True
        )
        assert level is PrecisionLevel.A

    def test_quarterly_detail_downgraded_when_residual_bad(self) -> None:
        level = mixing.precision_for(
            MixingMethod.QUARTERLY_DETAIL, is_daily_aum=False, attribution_residual_ok=False
        )
        assert level is PrecisionLevel.B


# ==========================================================================
# 方案 1 / 2（§6.2 式 8.1–8.3）
# ==========================================================================


class TestStepAndLinear:
    qe = [date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30)]
    qs = [100.0, 110.0, 90.0]

    def test_step_no_lookahead(self) -> None:
        """阶梯法只能用**已披露**季末，不得提前用下一个季末。"""
        dates = [date(2025, 4, 15), date(2025, 5, 20)]
        out = mixing.step_shares(dates, self.qe, self.qs)
        assert out == [100.0, 100.0]

    def test_step_none_before_first_quarter(self) -> None:
        """首个季末之前不可得，必须为 None（禁止前向填充）。"""
        out = mixing.step_shares([date(2025, 1, 15)], self.qe, self.qs)
        assert out == [None]

    def test_step_takes_newest_disclosed(self) -> None:
        dates = [date(2025, 7, 15), date(2025, 10, 15)]
        assert mixing.step_shares(dates, self.qe, self.qs) == [110.0, 90.0]

    def test_linear_midpoint(self) -> None:
        """两季末之间中点应约为两份额的平均。"""
        out = mixing.linear_shares([date(2025, 5, 15)], self.qe, self.qs)
        assert out[0] == pytest.approx(105.0, abs=0.5)

    def test_linear_no_extrapolation(self) -> None:
        out = mixing.linear_shares([date(2025, 1, 15), date(2025, 12, 15)], self.qe, self.qs)
        assert out == [None, None]


# ==========================================================================
# 方案 3（§6.2 式 8.4/8.5）—— 默认方案
# ==========================================================================


class TestQuarterlyDetail:
    dates = [date(2026, 4, 1) + timedelta(days=i) for i in range(10)]
    navs = [1.0 + 0.01 * i for i in range(10)]

    def test_uniform_weights_sum_to_one(self) -> None:
        w = mixing.uniform_weights(10)
        assert sum(w) == pytest.approx(1.0)
        assert len(set(w)) == 1

    def test_weighted_avg_nav_uniform_is_mean(self) -> None:
        w = mixing.uniform_weights(len(self.navs))
        assert mixing.weighted_avg_nav(self.navs, w) == pytest.approx(
            sum(self.navs) / len(self.navs)
        )

    def test_net_sub_uses_disclosed_shares(self) -> None:
        """NetSub = Sub^sh·N̄_sub − Red^sh·N̄_red（费前，不扣申赎费）。"""
        sub, red = 1_000_000.0, 400_000.0
        val = mixing.quarterly_net_sub(sub, red, self.navs, self.dates)
        mean_nav = sum(self.navs) / len(self.navs)
        assert float(val) == pytest.approx((sub - red) * mean_nav, abs=0.01)

    def test_missing_shares_returns_none(self) -> None:
        assert mixing.quarterly_net_sub(None, 1.0, self.navs, self.dates) is None
        assert mixing.quarterly_net_sub(1.0, None, self.navs, self.dates) is None

    def test_v_shape_puts_more_weight_on_high_nav_for_sub(self) -> None:
        w = mixing.v_shape_weights(self.navs, side="sub")
        assert sum(w) == pytest.approx(1.0)
        # 最高净值日的权重必须高于最低净值日
        assert w[-1] > w[0]

    def test_v_shape_puts_more_weight_on_low_nav_for_red(self) -> None:
        w = mixing.v_shape_weights(self.navs, side="red")
        assert w[0] > w[-1]

    def test_month_end_weights_concentrate(self) -> None:
        dates = [date(2026, 3, 20) + timedelta(days=i) for i in range(12)]  # 跨月
        w = mixing.month_end_weights(dates, n_last=2)
        assert sum(w) == pytest.approx(1.0)
        assert w[0] == 0.0  # 月中第一天不分配


class TestErrorBound:
    def test_matches_attachment_a_6_5_example(self) -> None:
        """§6.5 实测：|−1.87 亿份| × (1.63674 − 1.02006) = 1.15 亿元。"""
        bound = mixing.aum_error_bound(-1.87 * YI, 1.63674, 1.02006)
        assert float(bound) == pytest.approx(1.15 * YI, rel=0.01)

    def test_progress_factor_bounds(self) -> None:
        tau, tau_next = date(2026, 4, 1), date(2026, 7, 1)
        assert mixing.daily_progress_factor(tau, tau, tau_next) == 0.0
        assert mixing.daily_progress_factor(tau_next, tau, tau_next) == 1.0
        mid = mixing.daily_progress_factor(date(2026, 5, 16), tau, tau_next)
        assert 0.0 < mid < 1.0


# ==========================================================================
# 追高资金（§7，判据 V10）
# ==========================================================================


class TestChase:
    def test_percentile_series_first_point_is_one(self) -> None:
        assert chase.percentile_series([1.0])[0] == pytest.approx(1.0)

    def test_percentile_series_bounds(self) -> None:
        pcts = chase.percentile_series([1.0, 1.1, 1.05, 1.2, 0.9])
        assert all(0.0 <= p <= 1.0 for p in pcts)

    def test_percentile_uses_full_history(self) -> None:
        """分位必须用自成立以来全部历史：新高点的分位应为 1.0。"""
        pcts = chase.percentile_series([1.0, 1.5, 1.2, 1.6])
        assert pcts[3] == pytest.approx(1.0)

    def test_v10_no_chasing_must_be_output(self) -> None:
        """V10：000001 观测期持续净赎回 → ``Chase = 0``，系统**不得强行拟合**。

        数据来自附件A §7.2（2025-09-30 → 2026-09-30 四个季度实测份额）。
        """
        net_subs = [-1.90 * YI, -0.91 * YI, -1.19 * YI, -1.87 * YI]
        navs_history = [0.9, 0.95, 1.05, 1.1123, 1.0823, 1.02006, 1.63674]
        summary = chase.chase_summary(net_subs, navs_history, theta=0.8)
        assert summary["chase_amount"] == 0.0
        assert summary["has_chasing"] is False
        assert summary["chase_ratio"] is None  # 无申购，分母为 0，不得返回 0

    def test_positive_chasing_case(self) -> None:
        """正向样例（§7.2 验收要求）：高位大额净申购应被识别为追高。"""
        navs_history = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]
        net_subs = [0.0, 0.0, 0.0, 0.0, 0.0, 5.0 * YI]  # 最高位一笔 5 亿
        summary = chase.chase_summary(net_subs, navs_history, theta=0.8)
        assert summary["has_chasing"] is True
        assert summary["chase_amount"] == pytest.approx(5.0 * YI)
        assert summary["chase_ratio"] == pytest.approx(1.0)

    def test_net_redemption_never_counts_even_at_high_percentile(self) -> None:
        """§7.3：净赎回期不算追高，即使在高位。"""
        navs_history = [1.0, 1.2, 1.4]
        net_subs = [0.0, 0.0, -3.0 * YI]
        assert chase.chase_amount(net_subs, [0.5, 0.9, 1.0], theta=0.8) == 0.0

    def test_chase_ratio_none_when_no_inflow(self) -> None:
        assert chase.chase_ratio(0.0, 0.0) is None

    def test_chase_loss_only_counts_losses(self) -> None:
        """式(9.5)：只累加亏损部分，盈利的追高资金不计入。"""
        net_subs = [1.0 * YI, 1.0 * YI]
        pcts = [0.9, 0.9]
        entry_navs = [1.0, 1.0]
        nav_now = 0.8  # 都亏 20%
        loss = chase.chase_loss(net_subs, pcts, entry_navs, nav_now)
        assert loss == pytest.approx(0.4 * YI, rel=1e-6)

        nav_now_gain = 1.2  # 都赚 20% → 亏损金额为 0
        assert chase.chase_loss(net_subs, pcts, entry_navs, nav_now_gain) == 0.0

    def test_chase_future_pnl_skips_missing_future(self) -> None:
        """视窗末无未来净值时应跳过该期，不得用当前值顶替。"""
        pnl = chase.chase_future_pnl(
            [1.0 * YI, 1.0 * YI], [0.9, 0.9], [1.0, 1.0], [1.1, None]
        )
        assert pnl == pytest.approx(0.1 * YI, rel=1e-6)

    def test_chase_summary_requires_both_numbers(self) -> None:
        """§7.3：必须同时给「占比」与「亏损金额」，只给一个视为不合格。"""
        summary = chase.chase_summary([1.0 * YI], [0.5, 1.0], theta=0.8)
        assert "chase_ratio" in summary
        assert "chase_amount" in summary
        assert "has_chasing" in summary


class TestCaliberCheck:
    """式(9.6)：监管人数口径 M312 与份额口径估算的方向校验。"""

    def test_pass_when_close(self) -> None:
        assert chase.caliber_check(0.70, 0.65) == "PASS"

    def test_warn_when_over_10pct(self) -> None:
        assert chase.caliber_check(0.70, 0.55) == "CALIBER_MISMATCH_WARN"

    def test_block_when_opposite_sign(self) -> None:
        """方向相反即 BLOCK——这是唯一能用外生数据发现现金流方向错误的机会。"""
        assert chase.caliber_check(0.75, 0.20) == "CALIBER_MISMATCH_BLOCK"

    def test_no_data(self) -> None:
        assert chase.caliber_check(0.70, None) == "CALIBER_CHECK_NO_DATA"

    def test_estimate_profit_investor_ratio(self) -> None:
        ratio = chase.estimate_profit_investor_ratio(
            sub_shares=[100.0, 100.0], sub_navs=[1.0, 2.0], nav_now=1.5
        )
        assert ratio == pytest.approx(0.5)
