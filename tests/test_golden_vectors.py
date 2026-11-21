"""黄金向量：手算样例逐位复现（附件A §11 V3/V4/V9，SOW §7.3 C12）。

这些测试是**四端口径一致性的基线**：任何端改动导致数字漂移，必须先在这里失败。
容差取 1e-6（SOW §7.3 C12 / 附件C §13），另有 NPV 自洽断言作为独立校验。
"""

from __future__ import annotations

import warnings

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
        """式(6.2)：归因残差 0.00%；但 R_net 来自 A 式反解 → **门不可信**。

        《裁定书四》P0-1：残差为 0 是自证指纹，``passed`` 必须为 False。
        """
        r_net = attribution.r_net_from_identity(
            self.a_start, self.a_end, self.net_sub, self.div_cash
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", attribution.SelfProvisionWarning)
            wf = attribution.waterfall(
                self.a_start, self.net_sub, r_net, self.div_cash, self.a_end
            )
        assert wf.residual_pct == pytest.approx(0.0, abs=1e-9)
        assert wf.passed is False, "自证指纹下 passed 必须为 False"
        assert wf.gate_status == "self_provision"

    def test_identity_a_residual_is_actually_checked(self) -> None:
        """式(A) 的残差门**真的会失败**——不能是恒真断言（《裁定书四》P0-2）。

        原实现用 ``r_net_from_identity`` 反解 R_net 再喂回 ``check_identity_a``，
        残差按定义恒为 0，断言 ``passed is True`` **无论如何都通过**，等于没有测试。

        现在改为两条真断言：
        ① 用**独立路径**（``r_net_from_return``，不由 A 式反解）构造 R_net，
           残差应真实为 0 —— 证明一致数据下门能通过；
        ② 把期末 AUM 人为 +1%，同一门必须**变红** —— 证明它不是恒真。
        """
        # 附件A §5.3 数据：A_0=26.44亿、A_T=39.38亿、NetSub=-2.4841亿、Div=0
        # 隐含区间收益率 = R_net / A_0 = 15.4241 / 26.44 ≈ 58.3362%
        implied_return = 15.4241 / 26.44
        r_net = attribution.r_net_from_return(self.a_start, implied_return)

        # ① 一致数据 → 门通过
        chk = identities.check_identity_a(
            self.a_start,
            self.a_end,
            r_net,
            self.net_sub,
            self.div_cash,
            mixed_freq=True,
        )
        assert chk.passed is True, f"一致数据下应通过，实际残差 {chk.residual_pct:.6%}"
        assert chk.residual_pct < 0.005

        # ② 期末 AUM +1% → 门必须变红（证明断言不是恒真）
        bad_end = self.a_end * 1.01
        chk_bad = identities.check_identity_a(
            self.a_start,
            bad_end,
            r_net,
            self.net_sub,
            self.div_cash,
            mixed_freq=True,
        )
        assert chk_bad.passed is False, (
            f"期末 AUM +1% 后门必须失败，否则是恒真断言（实际 residual_pct="
            f"{chk_bad.residual_pct:.6%}）"
        )

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


def test_gv01_matches_revised_spec() -> None:
    """GV-01 与**已修订**的《附件A》§4.5 一致（DEV-001 收口后）。

    《裁定书一》R1：附件A 已按真值修订为 ``MWR = 0.497828%``、
    ``Gap = 4.002172 pct``、``年化 = 2.0062%``。

    原测试断言「引擎值与旧文档值 0.4944% 相差恰 0.34 bp」——文档修订后该断言失效。
    按裁定要求改为：断言**引擎值 == 文档值**，并保留「NPV(root)≈0」这条
    不依赖任何文档数字的自洽校验。
    """
    doc_mwr_pct = 0.497828      # 《附件A》§4.5 修订后值（百分数）
    doc_gap_pct = 4.002172      # 修订后缺口（百分点）
    engine_mwr = returns.irr([-1_000_000, -500_000, 0, 1_520_000]).value
    engine_mwr_pct = engine_mwr * 100
    engine_gap_pct = returns.investor_gap(0.045, engine_mwr) * 100

    # T-1 容差（文档示例逐位复现）：1e-6
    assert engine_mwr_pct == pytest.approx(doc_mwr_pct, abs=1e-6), (
        f"引擎 MWR {engine_mwr_pct:.9f}% 与修订后附件A {doc_mwr_pct}% 不符"
    )
    assert engine_gap_pct == pytest.approx(doc_gap_pct, abs=1e-6)
    # 自洽校验：不依赖任何文档数字
    cfs = [-1_000_000, -500_000, 0, 1_520_000]
    npv = sum(cf / (1.0 + engine_mwr) ** t for cf, t in zip(cfs, range(4)))
    assert abs(npv) < 1e-3, f"MWR 不是 NPV=0 的根：NPV={npv}"


def test_gv01_annualized_gap_is_172456() -> None:
    """DEV-001b：年化缺口 = **17.2456 pp**（不是 17.2438）。

    凌霄渡复核指出：原值的错因是「用截断值相减」。
    纪律：**中间值必须保留 6 位小数再相减**。
    """
    mwr = returns.irr([-1_000_000, -500_000, 0, 1_520_000]).value
    twr = 0.045
    # 3 期 = 1 季 → 每年 4 期（仅用于等间隔算例，SPEC-GAP-04）
    twr_ann = returns.annualize_by_periods(twr, 4) * 100
    mwr_ann = returns.annualize_by_periods(mwr, 4) * 100
    gap_ann = twr_ann - mwr_ann
    assert gap_ann == pytest.approx(17.2456, abs=1e-4), (
        f"年化缺口应为 17.2456 pp，实得 {gap_ann:.6f}；"
        f"（TWR年化 {twr_ann:.6f} / MWR年化 {mwr_ann:.6f}）"
    )
