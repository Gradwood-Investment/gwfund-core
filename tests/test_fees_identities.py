"""费用模块与恒等式模块的完整测试（补齐覆盖率至 ≥80%，SOW §7.3 C10）。

覆盖：浮动费率（规模分档 / 业绩报酬）、交易费用估算与年报校准、
申购赎回费分档查询、累计费用、恒等式 B/C、质量门抛错、Other 科目自检。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from gwfund_core import fees, identities
from gwfund_core.errors import IdentityResidualError

YI = 100_000_000


# ==========================================================================
# 费用：累计与分档
# ==========================================================================


class TestCumulativeFee:
    def test_sums_across_days(self) -> None:
        accs = [
            fees.daily_accrual(YI, date(2026, 6, d), "1.20", "0.20", 0)
            for d in (1, 2, 3)
        ]
        total = fees.cumulative_fee(accs)
        one_day = float(fees.daily_accrual(YI, date(2026, 6, 1), "1.20", "0.20", 0).total)
        assert float(total["total"]) == pytest.approx(one_day * 3, abs=0.02)
        assert float(total["mgmt"]) > float(total["custody"])

    def test_empty_is_zero(self) -> None:
        total = fees.cumulative_fee([])
        assert total["total"] == Decimal(0)


class TestAumTieredFee:
    """规模分档型浮动管理费（附件A §3.2）。"""

    tier = [(Decimal("1e10"), "1.20"), (None, "0.60")]

    def test_below_threshold_uses_first_rate(self) -> None:
        """50 亿 < 100 亿 → 全部按 1.20%。"""
        f = fees.daily_fee_by_aum_tier(50 * YI, date(2026, 6, 1), self.tier)
        expected = 50 * YI * 0.0120 / 365
        assert float(f) == pytest.approx(expected, rel=1e-3)

    def test_above_threshold_splits(self) -> None:
        """150 亿 → 100 亿按 1.20% + 50 亿按 0.60%。"""
        f = fees.daily_fee_by_aum_tier(150 * YI, date(2026, 6, 1), self.tier)
        expected = (100 * YI * 0.0120 + 50 * YI * 0.0060) / 365
        assert float(f) == pytest.approx(expected, rel=1e-3)

    def test_tiered_lower_than_flat(self) -> None:
        """分档后的费用必须低于「全部按高档费率」。"""
        tiered = fees.daily_fee_by_aum_tier(150 * YI, date(2026, 6, 1), self.tier)
        flat = fees.daily_fee(150 * YI, "1.20", date(2026, 6, 1))
        assert tiered < flat

    def test_requires_open_ended_tier(self) -> None:
        with pytest.raises(ValueError, match="收尾"):
            fees.daily_fee_by_aum_tier(
                YI, date(2026, 6, 1), [(Decimal("1e10"), "1.20")]
            )

    def test_negative_aum_rejected(self) -> None:
        with pytest.raises(ValueError):
            fees.daily_fee_by_aum_tier(-YI, date(2026, 6, 1), self.tier)


class TestPerformanceFee:
    """业绩报酬型浮动费率（附件A §3.2，高水位路径依赖）。"""

    def test_no_fee_below_hurdle(self) -> None:
        f, hwm = fees.performance_fee_daily(
            YI, nav_prev=1.00, hwm_prev=1.00, hurdle=0.05, share_pct=20, on=date(2026, 6, 1)
        )
        assert float(f) == 0.0
        assert hwm == 1.00

    def test_fee_above_hurdle(self) -> None:
        """净值 1.20 / 高水位 1.00 / 门槛 5% → 超额 15%，提 20%。"""
        f, hwm = fees.performance_fee_daily(
            YI, nav_prev=1.20, hwm_prev=1.00, hurdle=0.05, share_pct=20, on=date(2026, 6, 1)
        )
        assert float(f) == pytest.approx(YI * 0.15 * 0.20, rel=1e-9)
        assert hwm == pytest.approx(1.20)

    def test_hwm_ratchets_up_only(self) -> None:
        """高水位只上不下——净值回落后仍按旧高水位计提，不重置。"""
        _, hwm_high = fees.performance_fee_daily(
            YI, 1.30, 1.00, 0.05, 20, date(2026, 6, 1)
        )
        _, hwm_low = fees.performance_fee_daily(
            YI, 1.10, 1.30, 0.05, 20, date(2026, 6, 2)
        )
        assert hwm_high == pytest.approx(1.30)
        assert hwm_low == pytest.approx(1.30)  # 未创新高，高水位不动

    def test_hwm_does_not_move_below_hurdle(self) -> None:
        """**DEP-02 / SPEC-GAP-01**：涨 3% 未达 5% 门槛 → HWM **不动**。

        原实现与 hurdle 解耦，涨 3% 也上移，导致该段涨幅永久免收业绩报酬，
        系统性低估管理费 0.1–0.5 pct/年。
        """
        fee, hwm = fees.performance_fee_daily(
            YI, nav_prev=1.03, hwm_prev=1.00, hurdle=0.05, share_pct=20,
            on=date(2026, 6, 1),
        )
        assert float(fee) == 0.0
        assert hwm == pytest.approx(1.00), "未达门槛时高水位不得上移"

    def test_hwm_moves_when_hurdle_exceeded(self) -> None:
        """涨 8% 达 5% 门槛 → 计提，且 HWM 上移至 N_t。"""
        fee, hwm = fees.performance_fee_daily(
            YI, nav_prev=1.08, hwm_prev=1.00, hurdle=0.05, share_pct=20,
            on=date(2026, 6, 1),
        )
        assert float(fee) == pytest.approx(YI * 0.03 * 0.20, abs=1.0)
        assert hwm == pytest.approx(1.08)

    def test_hwm_path_dependence_vs_follows_nav(self) -> None:
        """两种 HWM 口径的差异可量化：跟随净值的口径会少收费用。"""
        # 路径：1.00 → 1.04（未达门槛）→ 1.10
        f1, h1 = fees.performance_fee_daily(YI, 1.04, 1.00, 0.05, 20, date(2026, 6, 1))
        f2, h2 = fees.performance_fee_daily(YI, 1.10, h1, 0.05, 20, date(2026, 6, 2))
        g1, gh1 = fees.performance_fee_daily_hwm_follows_nav(
            YI, 1.04, 1.00, 0.05, 20, date(2026, 6, 1)
        )
        g2, gh2 = fees.performance_fee_daily_hwm_follows_nav(
            YI, 1.10, gh1, 0.05, 20, date(2026, 6, 2)
        )
        assert float(f1) == 0.0 and float(g1) == 0.0
        assert h1 == pytest.approx(1.00) and gh1 == pytest.approx(1.04)
        assert float(f2) > float(g2), "默认口径应收更多（HWM 未上移）"

    def test_zero_hwm_rejected(self) -> None:
        with pytest.raises(ValueError):
            fees.performance_fee_daily(YI, 1.2, 0.0, 0.05, 20, date(2026, 6, 1))


class TestTradeFee:
    """交易费用估算与年报校准（附件A §3.3 式 3.5/3.7）。"""

    def test_impact_tiers_by_aum(self) -> None:
        assert fees.impact_cost_pct(5 * YI) == Decimal("0.05")
        assert fees.impact_cost_pct(20 * YI) == Decimal("0.10")
        assert fees.impact_cost_pct(100 * YI) == Decimal("0.20")
        assert fees.impact_cost_pct(500 * YI) == Decimal("0.30")

    def test_cost_rate_increases_with_aum(self) -> None:
        small = fees.trade_cost_rate_pct(5 * YI, side="sell")
        large = fees.trade_cost_rate_pct(500 * YI, side="sell")
        assert large > small

    def test_stamp_duty_only_on_sell(self) -> None:
        """SPEC-GAP-03 / DEP-03：印花税只在卖出侧，``sell − buy = 0.05 pct``。

        原实现 ``include_stamp_duty`` 默认 `True`，买入侧会多计 0.05 pct，
        对高换手基金的费用估算偏差可达 10–20%。
        """
        both = fees.trade_cost_rate_both_sides(YI)
        assert both["sell"] - both["buy"] == Decimal("0.05")

    def test_side_is_required(self) -> None:
        """方向必须显式传入，禁止默认值（否则某条调用路径必错）。"""
        with pytest.raises(TypeError):
            fees.trade_cost_rate_pct(YI)  # type: ignore[call-arg]
        with pytest.raises(ValueError, match="side"):
            fees.trade_cost_rate_pct(YI, side="long")

    def test_trade_fee_scales_with_turnover(self) -> None:
        f1 = fees.trade_fee_from_turnover("1.0", YI, YI, side="buy")
        f2 = fees.trade_fee_from_turnover("2.0", YI, YI, side="buy")
        assert float(f2) == pytest.approx(float(f1) * 2, rel=1e-9)

    def test_trade_fee_sell_higher_than_buy(self) -> None:
        buy = fees.trade_fee_from_turnover("1.0", YI, YI, side="buy")
        sell = fees.trade_fee_from_turnover("1.0", YI, YI, side="sell")
        assert sell > buy

    def test_implied_rate_and_calibration(self) -> None:
        """式(3.7)：用年报费用反算隐含成本率。"""
        implied = fees.implied_cost_rate(
            annual_trade_fee=Decimal("3000000"), turnover_aum_sum=Decimal("2e9")
        )
        assert float(implied) == pytest.approx(0.0015)

    def test_needs_recalibration_threshold(self) -> None:
        """偏差 > 30% 触发校准。"""
        assert fees.needs_recalibration(Decimal("0.0015"), Decimal("0.0010")) is True
        assert fees.needs_recalibration(Decimal("0.0011"), Decimal("0.0010")) is False
        assert fees.needs_recalibration(Decimal("0.001"), Decimal("0")) is True

    def test_calibration_factor(self) -> None:
        assert fees.calibration_factor(Decimal("0.0015"), Decimal("0.0010")) == pytest.approx(1.5)

    def test_zero_denominator_rejected(self) -> None:
        with pytest.raises(ValueError):
            fees.implied_cost_rate(100, 0)


class TestRedemptionTier:
    """申购/赎回费分档查询（附件A §3.2，**闭区间**语义）。"""

    tier = [
        {"holding_days": [0, 6], "rate_pct": 1.50},
        {"holding_days": [7, 364], "rate_pct": 0.50},
        {"holding_days": [365, 729], "rate_pct": 0.25},
        {"holding_days": [730, None], "rate_pct": 0.00},
    ]

    @pytest.mark.parametrize(
        ("days", "expected"),
        [(0, 1.50), (6, 1.50), (7, 0.50), (364, 0.50), (365, 0.25), (729, 0.25), (730, 0.0), (5000, 0.0)],
    )
    def test_boundaries_are_closed_intervals(self, days: int, expected: float) -> None:
        """边界必须含端点：6 与 7 天分属不同档（实测 110022 四档）。"""
        assert float(fees.lookup_tier_rate(self.tier, days)) == pytest.approx(expected)

    def test_no_match_raises(self) -> None:
        """无匹配档位必须抛错，不得静默返回 0。"""
        with pytest.raises(ValueError, match="无匹配"):
            fees.lookup_tier_rate([{"holding_days": [0, 6], "rate_pct": 1.5}], 100)

    def test_negative_days_rejected(self) -> None:
        with pytest.raises(ValueError):
            fees.lookup_tier_rate(self.tier, -1)


# ==========================================================================
# 恒等式 B / C / 质量门
# ==========================================================================


class TestIdentityBandC:
    def test_identity_b_passes_when_consistent(self) -> None:
        """B 式：P_inv = ΔAUM + Div − NetSub。"""
        chk = identities.check_identity_b(
            a_start=1000.0, a_end=1200.0, div_cash=50.0,
            net_sub=100.0, p_inv_value=150.0,
        )
        assert chk.passed is True
        assert float(chk.residual) == 0.0

    def test_identity_b_detects_double_counting(self) -> None:
        """把赎回费归资产又记进 Other 会造成 AUM 虚增，B 式必然对不上。"""
        chk = identities.check_identity_b(
            a_start=1000.0, a_end=1200.0, div_cash=0.0,
            net_sub=100.0, p_inv_value=0.0,  # 少算 100
        )
        assert chk.passed is False

    def test_identity_c_uses_profit_statement(self) -> None:
        """C 式：R_gross = P_inv + Fee − Other。"""
        chk = identities.check_identity_c(
            r_gross=1000.0, p_inv_value=850.0, fee=150.0, other=0.0
        )
        assert chk.passed is True

    def test_identity_c_tolerates_other(self) -> None:
        """存在 Other 时用 C' 形式 ``R_gross = P_inv + Fee − Other``，不得误报失败。"""
        chk = identities.check_identity_c(
            r_gross=980.0, p_inv_value=850.0, fee=150.0, other=20.0
        )
        assert chk.passed is True
        assert float(chk.residual) == pytest.approx(0.0, abs=0.01)
        # 若忽略 Other，则残差恰为 Other 的金额 → 会被误判为失败
        without_other = identities.check_identity_c(
            r_gross=980.0, p_inv_value=850.0, fee=150.0, other=0.0
        )
        assert without_other.passed is False
        assert float(without_other.residual) == pytest.approx(-20.0, abs=0.01)

    def test_assert_identity_raises_on_failure(self) -> None:
        chk = identities.check_identity_a(
            a_start=1000.0, a_end=2000.0, r_net=0.0, net_sub=0.0, div_cash=0.0
        )
        with pytest.raises(IdentityResidualError) as ei:
            identities.assert_identity(chk)
        assert ei.value.identity == "A"

    def test_assert_identity_silent_on_success(self) -> None:
        chk = identities.check_identity_a(
            a_start=1000.0, a_end=1100.0, r_net=100.0, net_sub=0.0, div_cash=0.0
        )
        identities.assert_identity(chk)  # 不应抛错

    def test_same_freq_uses_abs_tolerance(self) -> None:
        """同频容差 0.01 元：差 0.005 元应通过、差 0.02 元应失败。"""
        ok = identities.check_identity_a(
            1000.0, 1100.0, 100.005, 0.0, 0.0, mixed_freq=False
        )
        assert ok.passed is True
        bad = identities.check_identity_a(
            1000.0, 1100.0, 100.02, 0.0, 0.0, mixed_freq=False
        )
        assert bad.passed is False

    def test_mixed_freq_uses_relative_tolerance(self) -> None:
        """混频容差 0.5%：100 万规模差 4000 元（0.4%）通过，差 8000 元（0.8%）失败。"""
        ok = identities.check_identity_a(
            1_000_000.0, 1_100_000.0, 99_600.0, 0.0, 0.0, mixed_freq=True
        )
        assert ok.passed is True
        bad = identities.check_identity_a(
            1_000_000.0, 1_100_000.0, 92_000.0, 0.0, 0.0, mixed_freq=True
        )
        assert bad.passed is False

    def test_zero_aum_mixed_freq_falls_back_to_abs(self) -> None:
        chk = identities.check_identity_a(0.0, 0.0, 0.0, 0.0, 0.0, mixed_freq=True)
        assert chk.passed is True
        assert chk.residual_pct is None


class TestOtherResidual:
    def test_unexplained_residual_pct(self) -> None:
        rho = identities.unexplained_residual_pct(50_000.0, 10_000_000.0, 12_000_000.0)
        assert rho == pytest.approx(50_000 / 12_000_000)

    def test_residual_gate_boundary(self) -> None:
        """ρ < 0.5% 才过门禁（式 4.5）。以 1,000 万 AUM 为分母。"""
        base = 10_000_000.0
        assert identities.unexplained_residual_pct(40_000, base, base) < 0.005
        assert identities.unexplained_residual_pct(60_000, base, base) > 0.005

    def test_other_suspect_flags_misclassification(self) -> None:
        """Other 达 AUM 的 0.1% 以上 → 疑似科目归错（附件A §4.5）。"""
        base = 1_000_000.0  # 0.1% = 1,000 元
        assert identities.is_other_suspect(500, base, base) is False
        assert identities.is_other_suspect(2_000, base, base) is True

    def test_solve_other_roundtrip(self) -> None:
        """由 A 式反解 Other 再代回 A 式，残差应为 0。"""
        a_start, a_end = 1_000_000.0, 1_200_000.0
        r_net, net_sub, div = 150_000.0, 40_000.0, 10_000.0
        other = identities.solve_other(a_start, a_end, r_net, net_sub, div)
        chk = identities.check_identity_a(
            a_start, a_end, r_net, net_sub, div, other=other
        )
        assert chk.passed is True

    def test_pearson_correlation_known_value(self) -> None:
        assert identities.pearson_correlation([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
        assert identities.pearson_correlation([1, 2, 3], [-1, -2, -3]) == pytest.approx(-1.0)

    def test_pearson_correlation_degenerate(self) -> None:
        assert identities.pearson_correlation([1], [1]) is None
        assert identities.pearson_correlation([1, 1, 1], [1, 2, 3]) is None


class TestFourEndConsistency:
    """四端一致性比较工具（SOW §7.3 C12 容差 1e-6）。"""

    def test_exact_match(self) -> None:
        assert identities.compare_scalar(0.1234567, 0.1234567) is True

    def test_within_tolerance(self) -> None:
        assert identities.compare_scalar(1.0, 1.0 + 1e-9) is True

    def test_outside_tolerance(self) -> None:
        assert identities.compare_scalar(1.0, 1.001) is False

    def test_none_handling(self) -> None:
        assert identities.compare_scalar(None, None) is True
        assert identities.compare_scalar(None, 1.0) is False


class TestPInvAndCostInv:
    def test_p_inv_negative(self) -> None:
        """D10 场景：期初 100 万 + 追加 900 万 = 1000 万投入，期末 840 万 → −160 万。"""
        p = identities.p_inv(
            a_end=8_400_000, div_cash=0, a_start=1_000_000, net_sub=9_000_000
        )
        assert float(p) == pytest.approx(-1_600_000)

    def test_cost_inv_includes_both_fee_sides(self) -> None:
        """Cost_inv = F_sub + F_red + Fee（投资者总成本口径，独立于三表）。"""
        c = identities.cost_inv(fee_sub=15_000, fee_red=6_000, fee_total=1_400_000)
        assert float(c) == pytest.approx(1_421_000)
