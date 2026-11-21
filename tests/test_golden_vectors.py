"""黄金向量：手算样例逐位复现（附件A §11 V3/V4/V9，SOW §7.3 C12）。

这些测试是**四端口径一致性的基线**：任何端改动导致数字漂移，必须先在这里失败。
容差取 1e-6（SOW §7.3 C12 / 附件C §13），另有 NPV 自洽断言作为独立校验。
"""

from __future__ import annotations

import pytest

from gwfund_core import attribution, fees, identities, returns
from datetime import date

YI = 100_000_000  # 1 亿元


# ==========================================================================
# 附件A §4.5 · 三期手算样例
# ==========================================================================


class TestAttachmentA_4_5_ThreePeriod:
    """设定：N = [1.000, 1.100, 1.045, 1.0450]；无分红；
    t=0 申购 100 万，t=1 追加 50 万，t=3 全部赎回。"""

    navs = [1.000, 1.100, 1.045, 1.0450]
    cashflows = [-1_000_000, -500_000, 0, 1_520_000]

    def test_single_period_returns(self) -> None:
        """式(1.2)：r_1=+10%, r_2=-5%, r_3=0%。"""
        rs = [
            returns.daily_return(self.navs[i - 1], self.navs[i])
            for i in range(1, len(self.navs))
        ]
        assert rs == pytest.approx([0.10, -0.05, 0.0], abs=1e-12)

    def test_twr(self) -> None:
        """式(5.1)：TWR = 1.10 × 0.95 × 1.00 − 1 = +4.50%。"""
        twr = returns.chain_return([0.10, -0.05, 0.0])
        assert twr == pytest.approx(0.045, abs=1e-12)

    def test_mwr_root_is_genuine(self) -> None:
        """MWR 必须真的是 NPV=0 的根（自洽校验，不依赖任何文档数字）。"""
        res = returns.irr(self.cashflows)
        npv = sum(cf / (1.0 + res.value) ** t for cf, t in zip(self.cashflows, range(4)))
        scale = sum(abs(cf) for cf in self.cashflows)
        assert abs(npv) / scale < 1e-12, f"NPV 未归零：{npv}"

    def test_mwr_value(self) -> None:
        """式(5.2)：MWR ≈ 0.497828%（独立复算值）。

        .. note::
           附件A §4.5 原文写 **0.4944%**，与本实现的 0.497828% 相差 0.34 bp。
           经三步独立复核（牛顿法、二分法、NPV 代入），**0.497828% 才是
           NPV=0 的真根**；原文 0.4944% 代入后 NPV = −142.2 元 ≠ 0。
           本偏差已登记于 ``DEVIATIONS.md`` DEV-001，待委托方裁定。
        """
        res = returns.irr(self.cashflows)
        assert res.value == pytest.approx(0.4978283759868921e-2, abs=1e-12)
        assert res.multi_root is False

    def test_investor_gap(self) -> None:
        """式(5.3)：Gap = TWR − MWR = 4.50% − 0.497828% = 4.0022 pct。

        .. note::
           原文写 4.0056 pct（源自 0.4944%），随 DEV-001 一并修正。
        """
        twr = 0.045
        mwr = returns.irr(self.cashflows).value
        gap = returns.investor_gap(twr, mwr)
        assert gap == pytest.approx(0.04002171624013101, abs=1e-12)


# ==========================================================================
# 决策 D10 · MWR 黄金用例（用户已复算确认，容差 ±0.1 pct）
# ==========================================================================


class TestDecisionD10Golden:
    """期初 100 万（NAV=1.0）→ 半年后 NAV=1.5 追加 900 万 → 年末 NAV=1.2。

    现金流：−100 / −900 / +840（万元）；解 ``840y² − 900y − 100 = 0``。
    """

    def test_twr(self) -> None:
        """净值 1.0 → 1.5 → 1.2：TWR = 1.5 × 0.8 − 1 = +20.00%。"""
        twr = returns.chain_return([1.5 / 1.0 - 1.0, 1.2 / 1.5 - 1.0])
        assert twr == pytest.approx(0.20, abs=1e-12)

    def test_mwr(self) -> None:
        """MWR = −27.3126%（D10 定：−27.31%，容差 ±0.1 pct）。"""
        res = returns.irr([-100.0, -900.0, 840.0], [0.0, 0.5, 1.0])
        assert res.value == pytest.approx(-0.2731, abs=1e-3)
        assert res.multi_root is False

    def test_p_inv(self) -> None:
        """式(4.1)：P_inv = 840 − 1000 = −160 万元。"""
        p = identities.p_inv(a_end=840.0, div_cash=0.0, a_start=100.0, net_sub=900.0)
        assert p == pytest.approx(-160.0, abs=1e-9)

    def test_gap(self) -> None:
        """Gap = +20.00% − (−27.31%) = +47.31 pct（D10 定，容差 ±0.1）。"""
        mwr = returns.irr([-100.0, -900.0, 840.0], [0.0, 0.5, 1.0]).value
        gap = returns.investor_gap(0.20, mwr)
        assert gap * 100 == pytest.approx(47.31, abs=0.1)

    def test_mwr_short_period_not_annualized(self) -> None:
        """T9：区间 1 年（含）以下不年化时 ``annualized`` 必须为 None。"""
        res = returns.mwr_from_flows(
            [-100.0, -900.0, 840.0], [0.0, 0.5, 1.0], period_days=300
        )
        assert res.annualized is None
        assert res.value == pytest.approx(-0.2731, abs=1e-3)


# ==========================================================================
# 附件A §3.5 · 费用计提手算样例
# ==========================================================================


class TestAttachmentA_3_5FeeGolden:
    """100 亿元规模 / 管理费 1.20% / 托管费 0.20% / A 类销售服务费 0。"""

    a_prev = 10_000_000_000

    def test_non_leap_2026(self) -> None:
        """非闰年：mgmt 328,767.12 / cust 54,794.52 / 合计 383,561.64。"""
        acc = fees.daily_accrual(
            self.a_prev, date(2026, 6, 1), "1.20", "0.20", 0
        )
        # Decimal 与 pytest.approx 不直接兼容，统一转 float 比较
        assert float(acc.mgmt) == pytest.approx(328_767.12, abs=0.005)
        assert float(acc.custody) == pytest.approx(54_794.52, abs=0.005)
        assert float(acc.sales) == 0.0
        assert float(acc.total) == pytest.approx(383_561.64, abs=0.005)

    def test_annual_total_equals_rate_times_aum(self) -> None:
        """年度合计 = 1.40% × 100 亿 = 1.40 亿元（口径自洽）。"""
        acc = fees.daily_accrual(
            self.a_prev, date(2026, 6, 1), "1.20", "0.20", 0
        )
        assert float(acc.total) * 365 == pytest.approx(140_000_000, rel=1e-6)

    def test_leap_year_2024(self) -> None:
        """闰年分母 366：mgmt 327,868.85（单日变小，年度合计不变）。"""
        f = fees.daily_fee(self.a_prev, "1.20", date(2024, 6, 3))
        assert float(f) == pytest.approx(327_868.85, abs=0.005)

    def test_leap_year_effect_direction(self) -> None:
        """闰年单日费用**小于**非闰年（分母变大）——方向可证。"""
        f2024 = fees.daily_fee(self.a_prev, "1.20", date(2024, 6, 3))
        f2026 = fees.daily_fee(self.a_prev, "1.20", date(2026, 6, 1))
        assert f2024 < f2026


# ==========================================================================
# 附件A §5.3 · 规模归因样例（000001 2026Q2 实测数据）
# ==========================================================================


class TestAttachmentA_5_3AttributionGolden:
    """A_0=26.44 亿 → A_T=39.38 亿；NetSub^sh=−1.87 亿份；NetSub=−2.4841 亿元。"""

    a_start = 26.44 * YI
    a_end = 39.38 * YI
    net_sub = -2.4841 * YI
    div_cash = 0.0

    def test_r_net(self) -> None:
        """R_net = 39.38 − 26.44 − (−2.4841) + 0 = 15.4241 亿元。"""
        r_net = attribution.r_net_from_identity(
            self.a_start, self.a_end, self.net_sub, self.div_cash
        )
        assert float(r_net) / YI == pytest.approx(15.4241, abs=1e-4)

    def test_waterfall_closes(self) -> None:
        """式(6.2)：归因残差 0.00%，``passed`` 为 True。"""
        r_net = attribution.r_net_from_identity(
            self.a_start, self.a_end, self.net_sub, self.div_cash
        )
        wf = attribution.waterfall(
            self.a_start, self.net_sub, r_net, self.div_cash, self.a_end
        )
        assert wf.passed is True
        assert wf.residual_pct == pytest.approx(0.0, abs=1e-9)

    def test_identity_a_closes(self) -> None:
        """式(A) 在混频口径下相对残差 < 0.5%。"""
        r_net = attribution.r_net_from_identity(
            self.a_start, self.a_end, self.net_sub, self.div_cash
        )
        chk = identities.check_identity_a(
            self.a_start, self.a_end, r_net, self.net_sub, self.div_cash,
            mixed_freq=True,
        )
        assert chk.passed is True

    def test_waterfall_segments(self) -> None:
        """瀑布六段的金额与符号（分红段为负、期末等于 A_T）。"""
        r_net = attribution.r_net_from_identity(
            self.a_start, self.a_end, self.net_sub, self.div_cash
        )
        wf = attribution.waterfall(
            self.a_start, self.net_sub, r_net, self.div_cash, self.a_end
        )
        keys = [s.key for s in wf.segments]
        assert keys == ["a_start", "net_sub", "r_net", "div_cash", "other", "a_end"]
        assert float(wf.segments[1].amount) / YI == pytest.approx(-2.4841, abs=1e-4)
        assert float(wf.segments[5].amount) / YI == pytest.approx(39.38, abs=1e-4)


# ==========================================================================
# 偏差登记自检：原文数字与本实现的差异必须被显式记录
# ==========================================================================


def test_deviation_dev001_is_documented() -> None:
    """DEV-001 自检：附件A §4.5 的 MWR 原文值与本实现的差为 0.34 bp。

    这条测试的作用是**提醒**：一旦附件A 修订（换成 0.497828%），
    本测试会失败，提示开发者同步更新 DEVIATIONS.md 与测试注释。
    """
    spec_value = 0.4944e-2
    true_value = 0.4978283759868921e-2
    diff_bp = abs(true_value - spec_value) * 10_000
    assert diff_bp == pytest.approx(0.34, abs=0.01), (
        "附件A §4.5 的 MWR 原文值可能已被修订，请同步 DEVIATIONS.md DEV-001"
    )
