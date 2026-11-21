"""绩效与体验指标测试（MISS-01~09，补齐《裁定书二》§3.1 的 9 项缺失指标）。

覆盖：M204 年化波动率 / M205 最大回撤 / M206 夏普 / M207 索提诺 / M208 卡玛 /
M306 每元管理费盈亏 / M307 持有体验分布 / M310 费用率 / M313 换手率（估算 + 披露）。
"""

from __future__ import annotations

import math

import pytest

from gwfund_core import metrics


# ==========================================================================
# M204 年化波动率
# ==========================================================================


class TestAnnualizedVolatility:
    def test_known_value(self) -> None:
        """常数收益序列 → 波动率 0（不是 None，因为样本足够）。"""
        assert metrics.annualized_volatility([0.01] * 30) == pytest.approx(0.0)

    def test_scales_with_sqrt_periods(self) -> None:
        rs = [0.01, -0.01, 0.02, -0.02, 0.015]
        a = metrics.annualized_volatility(rs, periods_per_year=252)
        b = metrics.annualized_volatility(rs, periods_per_year=52)
        assert a / b == pytest.approx(math.sqrt(252 / 52), rel=1e-9)

    def test_insufficient_sample_returns_none(self) -> None:
        """样本不足 2 个 → None，**不得返回 0**（附件A §5.8）。"""
        assert metrics.annualized_volatility([]) is None
        assert metrics.annualized_volatility([0.01]) is None


# ==========================================================================
# M205 最大回撤
# ==========================================================================


class TestMaxDrawdown:
    def test_known_drawdown(self) -> None:
        """净值 1.0 → 1.2 → 0.9 → 1.1：最大回撤 = 1 − 0.9/1.2 = 25%。"""
        r = metrics.max_drawdown([1.0, 1.2, 0.9, 1.1])
        assert r is not None
        assert r.mdd == pytest.approx(0.25)
        assert r.peak_index == 1
        assert r.trough_index == 2

    def test_monotonic_increase_has_zero_drawdown(self) -> None:
        r = metrics.max_drawdown([1.0, 1.1, 1.2, 1.3])
        assert r is not None
        assert r.mdd == pytest.approx(0.0)

    def test_peak_before_trough_required(self) -> None:
        """回撤必须「先峰后谷」：先跌后涨不算回撤。"""
        r = metrics.max_drawdown([1.0, 0.8, 1.5])
        assert r is not None
        assert r.mdd == pytest.approx(0.20)  # 只有 1.0→0.8 那一段
        assert r.trough_index == 1

    def test_invalid_input_returns_none(self) -> None:
        assert metrics.max_drawdown([]) is None
        assert metrics.max_drawdown([1.0]) is None
        assert metrics.max_drawdown([1.0, 0.0, 1.2]) is None
        assert metrics.max_drawdown([1.0, -0.5]) is None


# ==========================================================================
# M206 夏普 / M207 索提诺 / M208 卡玛
# ==========================================================================


class TestRiskAdjustedRatios:
    def test_sharpe_positive_for_upward_series(self) -> None:
        rs = [0.01, 0.012, 0.008, 0.011, 0.009, 0.013, 0.007, 0.010]
        s = metrics.sharpe_ratio(rs, risk_free_annual=0.03)
        assert s is not None and s > 0

    def test_sharpe_none_when_zero_vol(self) -> None:
        """零波动 → 分母为 0 → None（不得返回 inf/0）。"""
        assert metrics.sharpe_ratio([0.01] * 20) is None

    def test_sharpe_penalised_by_risk_free(self) -> None:
        rs = [0.01, 0.012, 0.008, 0.011, 0.009]
        low_rf = metrics.sharpe_ratio(rs, risk_free_annual=0.0)
        high_rf = metrics.sharpe_ratio(rs, risk_free_annual=0.10)
        assert low_rf is not None and high_rf is not None
        assert high_rf < low_rf

    def test_downside_deviation_uses_all_samples(self) -> None:
        """分母用**全部样本数**（只数负样本会低估下行风险）。"""
        rs = [0.01, 0.01, -0.02, -0.02]
        dd = metrics.downside_deviation(rs, periods_per_year=1)
        # sqrt((0 + 0 + 0.0004 + 0.0004)/4) = sqrt(0.0002)
        assert dd == pytest.approx(math.sqrt(0.0002), rel=1e-9)

    def test_sortino_higher_than_sharpe_when_downside_small(self) -> None:
        """下行波动小于总波动时，索提诺应大于夏普。"""
        rs = [0.05, 0.04, 0.06, 0.045, 0.055, -0.005, 0.05, 0.06]
        s = metrics.sharpe_ratio(rs, risk_free_annual=0.0)
        so = metrics.sortino_ratio(rs, risk_free_annual=0.0)
        assert s is not None and so is not None
        assert so > s

    def test_sortino_none_when_no_downside(self) -> None:
        assert metrics.sortino_ratio([0.01, 0.02, 0.03]) is None

    def test_calmar(self) -> None:
        assert metrics.calmar_ratio(0.20, 0.10) == pytest.approx(2.0)
        assert metrics.calmar_ratio(0.20, 0.0) is None
        assert metrics.calmar_ratio(None, 0.1) is None


# ==========================================================================
# M306 每元管理费对应基民盈亏
# ==========================================================================


class TestFeePerYuanPnl:
    def test_negative_when_investors_lose(self) -> None:
        """基民亏 1.6 亿、管理费收 1842 万 → 每元管理费对应 −8.7 元。"""
        v = metrics.fee_per_yuan_pnl(-160_000_000, 18_420_000)
        assert v is not None
        assert v == pytest.approx(-8.686, rel=1e-3)

    def test_zero_fee_returns_none(self) -> None:
        """管理费为 0 → 分母为 0 → None（不得返回 0）。"""
        assert metrics.fee_per_yuan_pnl(1_000_000, 0) is None


# ==========================================================================
# M307 持有体验分布
# ==========================================================================


class TestHoldingExperience:
    def test_basic_distribution(self) -> None:
        navs = [1.0 + 0.001 * i for i in range(300)]  # 单调上涨
        r = metrics.holding_experience(navs, window=250)
        assert r["samples"] == 300 - 250
        assert r["win_rate"] == pytest.approx(1.0)
        assert r["min"] is not None and r["min"] > 0

    def test_insufficient_sample_returns_none_fields(self) -> None:
        r = metrics.holding_experience([1.0, 1.1, 1.2], window=250)
        assert r["samples"] == 0
        assert r["win_rate"] is None
        assert r["median"] is None

    def test_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="window"):
            metrics.holding_experience([1.0, 1.1], window=0)

    def test_downside_series_has_low_win_rate(self) -> None:
        navs = [2.0 - 0.002 * i for i in range(300)]
        r = metrics.holding_experience(navs, window=250)
        assert r["win_rate"] == pytest.approx(0.0)


# ==========================================================================
# M310 费用率
# ==========================================================================


class TestFeeRatio:
    def test_annualizes_by_calendar_days(self) -> None:
        """半年费用 700 万 / 平均 AUM 10 亿 → 年化费用率 1.40%。"""
        v = metrics.fee_ratio(7_000_000, 1_000_000_000, days=182.5)
        assert v == pytest.approx(0.0140, rel=1e-3)

    def test_full_year_is_direct_ratio(self) -> None:
        v = metrics.fee_ratio(14_000_000, 1_000_000_000, days=365)
        assert v == pytest.approx(0.014)

    def test_zero_aum_returns_none(self) -> None:
        assert metrics.fee_ratio(100, 0, days=365) is None
        assert metrics.fee_ratio(100, 1_000, days=0) is None


# ==========================================================================
# M313 换手率（式 3.6 估算 + 披露口径）
# ==========================================================================


class TestTurnover:
    def test_no_trade_means_zero_turnover(self) -> None:
        """权重不变且收益一致 → 无交易 → 换手 0。"""
        w = {"A": 0.5, "B": 0.5}
        r = {"A": 0.10, "B": 0.10}
        assert metrics.turnover_from_weights(w, w, r, 0.10) == pytest.approx(0.0)

    def test_passive_drift_is_not_turnover(self) -> None:
        """持仓不动、A 涨 B 不涨 → 权重自然漂移，但**不应算作换手**。"""
        w_prev = {"A": 0.5, "B": 0.5}
        r = {"A": 0.20, "B": 0.00}
        port_r = 0.10
        w_curr = {"A": 0.5 * 1.20 / 1.10, "B": 0.5 * 1.00 / 1.10}
        assert metrics.turnover_from_weights(w_prev, w_curr, r, port_r) == pytest.approx(0.0, abs=1e-9)

    def test_full_switch_is_one(self) -> None:
        """从 A 全部换到 B → 单边换手 1.0。"""
        assert metrics.turnover_from_weights(
            {"A": 1.0, "B": 0.0}, {"A": 0.0, "B": 1.0}, {"A": 0.0, "B": 0.0}, 0.0
        ) == pytest.approx(1.0)

    def test_empty_weights_returns_none(self) -> None:
        assert metrics.turnover_from_weights({}, {}, {}, 0.0) is None

    def test_disclosed_uses_min_of_buy_sell(self) -> None:
        """披露口径取买卖金额**较低者**（抑制双边放大）。"""
        v = metrics.turnover_disclosed(buy_cost=120, sell_proceeds=80, avg_holding_value=200)
        assert v == pytest.approx(0.4)  # min(120, 80)/200

    def test_disclosed_zero_denominator_returns_none(self) -> None:
        assert metrics.turnover_disclosed(100, 100, 0) is None

    def test_caliber_gap_threshold(self) -> None:
        """估算/披露偏离 > 50% ⇒ 估算模型失效（附件A §3.3）。"""
        assert metrics.turnover_caliber_gap(1.2, 1.0) == pytest.approx(0.2)
        assert metrics.turnover_caliber_gap(1.6, 1.0) == pytest.approx(0.6)
        assert metrics.turnover_caliber_gap(1.6, 1.0) > 0.5
        assert metrics.turnover_caliber_gap(1.0, 0.0) is None
