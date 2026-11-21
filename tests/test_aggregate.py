"""聚合与排名指标测试（M209 / M303 / M308 / M309 / M311）。

这些指标的共同风险是**口径悄悄错**：
- 分位并列处理不当 → 同样收益排名不同；
- ΔAUM≈0 时算占比 → 几百倍的无意义数字；
- A/C 份额合并开关失效 → 同一只基金被算两次；
- 经理聚合剔除离任 → 幸存者偏差；
- 持有人缺档按 0 补齐 → 勾稽「通过」但结论是假的。

每个用例都针对上述一种失效模式。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from gwfund_core import aggregate as ag


# ==========================================================================
# M209 同类排名分位
# ==========================================================================


class TestPercentileRank:
    def test_best_is_100(self) -> None:
        assert ag.percentile_rank(0.50, [0.1, 0.2, 0.3, 0.4, 0.5]) == pytest.approx(100.0)

    def test_worst_is_0(self) -> None:
        assert ag.percentile_rank(0.10, [0.1, 0.2, 0.3, 0.4, 0.5]) == pytest.approx(0.0)

    def test_median_is_50(self) -> None:
        assert ag.percentile_rank(0.30, [0.1, 0.2, 0.3, 0.4, 0.5]) == pytest.approx(50.0)

    def test_ties_use_average_rank(self) -> None:
        """并列样本共享同一分位——**同样收益不能因排序不同而排名不同**。"""
        a = ag.percentile_rank(0.3, [0.1, 0.3, 0.3, 0.5])
        b = ag.percentile_rank(0.3, [0.1, 0.3, 0.3, 0.5])
        assert a == pytest.approx(b)

    def test_all_equal_gives_50(self) -> None:
        assert ag.percentile_rank(0.3, [0.3, 0.3, 0.3]) == pytest.approx(50.0)

    def test_lower_is_better_inverts(self) -> None:
        """回撤/费率类指标：越小越好，分位要反过来。"""
        best = ag.percentile_rank(0.05, [0.05, 0.10, 0.20], higher_is_better=False)
        worst = ag.percentile_rank(0.20, [0.05, 0.10, 0.20], higher_is_better=False)
        assert best == pytest.approx(100.0)
        assert worst == pytest.approx(0.0)

    def test_empty_pool_returns_none(self) -> None:
        """空池返回 None，**不得返回 0**（0 表示最差，语义完全不同）。"""
        assert ag.percentile_rank(0.3, []) is None

    def test_single_peer_is_50(self) -> None:
        assert ag.percentile_rank(0.3, [0.3]) == pytest.approx(50.0)


# ==========================================================================
# M303 规模增长来源占比
# ==========================================================================


class TestNetSubShareOfGrowth:
    def test_normal_case(self) -> None:
        assert ag.net_sub_share_of_growth(100, 60) == pytest.approx(0.6)

    def test_over_100pct_allowed(self) -> None:
        """净申购超过规模增长 ⇒ 投资收益为负。**不得截断到 [0,1]**。"""
        assert ag.net_sub_share_of_growth(100, 150) == pytest.approx(1.5)

    def test_negative_allowed(self) -> None:
        """规模在缩但客户仍净申购 ⇒ 负占比，有解读意义。"""
        assert ag.net_sub_share_of_growth(-100, 20) == pytest.approx(-0.2)

    def test_tiny_growth_returns_none(self) -> None:
        """ΔAUM 接近 0 时占比会爆到几百倍，无解释意义 → None。"""
        assert ag.net_sub_share_of_growth(0.2, 60) is None
        assert ag.net_sub_share_of_growth(0, 60) is None

    def test_custom_threshold(self) -> None:
        assert ag.net_sub_share_of_growth(5, 3, min_abs_growth_wan=Decimal("10")) is None
        assert ag.net_sub_share_of_growth(20, 3, min_abs_growth_wan=Decimal("10")) == pytest.approx(0.15)


# ==========================================================================
# M308 公司层面聚合
# ==========================================================================


def _f(code: str, p_inv: float, fee: float, aum: float = 0, **kw) -> ag.FundFigure:
    return ag.FundFigure(code, p_inv, fee, aum, **kw)


class TestAggregateCompany:
    def test_simple_sum(self) -> None:
        r = ag.aggregate_company([_f("A", 100, 10), _f("B", -40, 5)])
        assert r["n_funds"] == 2
        assert r["p_inv_wan"] == Decimal("60.00")
        assert r["fee_wan"] == Decimal("15.00")
        assert r["fee_per_yuan_pnl"] == pytest.approx(4.0)

    def test_merge_share_classes_keeps_largest(self) -> None:
        """A/C 合并：同一只基金只保留 AUM 最大的一档。"""
        rows = [_f("000001A", 100, 10, 500), _f("000001C", 50, 5, 100)]
        r = ag.aggregate_company(rows, merge_share_classes=True)
        assert r["n_funds"] == 1
        assert r["merged"] is True
        assert r["p_inv_wan"] == Decimal("100.00")  # 保留 A 类

    def test_no_merge_by_default(self) -> None:
        rows = [_f("000001A", 100, 10, 500), _f("000001C", 50, 5, 100)]
        r = ag.aggregate_company(rows)
        assert r["n_funds"] == 2
        assert r["merged"] is False

    def test_zero_fee_returns_none(self) -> None:
        r = ag.aggregate_company([_f("A", 100, 0)])
        assert r["fee_per_yuan_pnl"] is None

    def test_empty(self) -> None:
        r = ag.aggregate_company([])
        assert r["n_funds"] == 0
        assert r["fee_per_yuan_pnl"] is None


# ==========================================================================
# M309 经理层面聚合
# ==========================================================================


class TestAggregateManager:
    def test_includes_departed_by_default(self) -> None:
        """**默认必须含离任产品**——否则是幸存者偏差。"""
        rows = [_f("A", 100, 10, is_active=True), _f("B", -500, 5, is_active=False)]
        r = ag.aggregate_manager(rows)
        assert r["n_funds"] == 2
        assert r["n_departed"] == 1
        assert r["p_inv_wan"] == Decimal("-400.00")
        assert r["n_excluded_due_to_survivorship"] == 0

    def test_excluding_departed_is_explicit_and_counted(self) -> None:
        rows = [_f("A", 100, 10, is_active=True), _f("B", -500, 5, is_active=False)]
        r = ag.aggregate_manager(rows, include_departed=False)
        assert r["n_funds"] == 1
        assert r["p_inv_wan"] == Decimal("100.00")
        # 必须报出「被幸存者偏差剔掉了几只」，让调用方看见代价
        assert r["n_excluded_due_to_survivorship"] == 1


# ==========================================================================
# M311 持有人结构
# ==========================================================================


class TestHolderStructure:
    def test_balanced_passes(self) -> None:
        r = ag.holder_structure(60.0, 39.5, 0.5)
        assert r["passed"] is True
        assert r["total_pct"] == pytest.approx(100.0)

    def test_imbalance_fails_with_reason(self) -> None:
        r = ag.holder_structure(60.0, 30.0, 0.0)
        assert r["passed"] is False
        assert "偏离" in (r["reason"] or "")

    def test_missing_tier_is_not_zero_filled(self) -> None:
        """缺档时 **total_pct 必须是 None**，不得按 0 补齐后判「勾稽通过」。"""
        r = ag.holder_structure(60.0, None, 0.5)
        assert r["total_pct"] is None
        assert r["passed"] is None
        assert "不得按 0 补齐" in (r["reason"] or "")

    def test_all_missing(self) -> None:
        r = ag.holder_structure(None, None, None)
        assert r["total_pct"] is None
        assert r["reason"] == "三档全部缺失"

    def test_custom_tolerance(self) -> None:
        r = ag.holder_structure(60.0, 39.0, 0.0, tolerance_pct=2.0)
        assert r["passed"] is True
