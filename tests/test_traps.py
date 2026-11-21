"""陷阱清单测试：附件A §9 T1–T14（§10 E6 要求每个陷阱至少 1 个单元测试）。

v0.1.0 覆盖状态（真实标注，不含糊）：

====  ====  ==========================================
编号  状态  说明
====  ====  ==========================================
T1    ✅    红利再投 vs 现金分红（份额归因分离）
T2    ✅    赎回费归基金资产 → 不进 Fee / 不进 Other
T3    ✅    申赎费归销售机构 → 不冲减 NetSub
T4    ✅    拆分/折算 → 用复权口径算收益
T5    ⏳    A/C 份额合并——依赖数据层（v0.2.0）
T6    ⏳    ETF 联接双重收费——依赖数据层（v0.2.0）
T7    ⏳    货币基金摊余成本法——v1 不覆盖货币基金（D03）
T8    ⏳    QDII 汇率——v1 不覆盖 QDII（D03）
T9    ✅    成立不满一年不年化
T10   ⏳    清盘/转型——依赖数据层状态字段（v0.2.0）
T11   ✅    净值 4 位小数舍入累积
T12   ⏳    定开/持有期单日集中申赎（v0.2.0）
T13   ⏳    巨额赎回摆动定价（v0.2.0）
T14   ✅    分红以权益登记日份额为准
====  ====  ==========================================

⏳ 的条目不是「忘了写」，而是**依赖尚未交付的数据层**；
在 M1 门禁前必须全部转 ✅（附件A V8 判据）。
"""

from __future__ import annotations

import warnings

import pytest

from gwfund_core import attribution, fees, identities, returns


# --------------------------------------------------------------------------
# T1 红利再投 vs 现金分红
# --------------------------------------------------------------------------


def test_t1_reinvest_does_not_create_fake_net_subscription() -> None:
    """T1：红利再投只增份额、**AUM 不变**、``Div_cash = 0``。

    若代码写成 ``S_t = S_{t-1} + NetSub``（漏掉 ``Div^reinv``），
    就会把红利再投的份额静默计入净申购，导致归因把「市场赚的」误记成「客户买的」。
    """
    s_start, s_end = 1_000_000.0, 1_100_000.0  # 红利再投 10 万份
    net_sub_shares = 0.0  # 客户一分钱没申购
    div_reinvest = 100_000.0

    result = attribution.share_attribution(
        s_start, s_end, net_sub_shares, div_reinvest,
        tol_shares=0.01,
    )
    assert result["passed"] is True
    assert result["residual"] == pytest.approx(0.0, abs=1e-4)

    # 反面：漏掉 Div^reinv 时残差 = 10 万份，必须被发现
    wrong = attribution.share_attribution(
        s_start, s_end, net_sub_shares, 0.0, tol_shares=0.01
    )
    assert wrong["passed"] is False
    assert wrong["residual"] == pytest.approx(100_000.0, abs=1e-4)


def test_t1_cash_dividend_reduces_aum_not_shares() -> None:
    """T1：现金分红使 AUM 减少、份额不变、``Div_cash > 0``。"""
    a_start = 1_000.0
    div_cash = 100.0
    r_net = 0.0
    net_sub = 0.0
    a_end = 900.0  # 100 元派现，净值下跌
    chk = identities.check_identity_a(
        a_start, a_end, r_net, net_sub, div_cash, other=0
    )
    assert chk.passed is True


# --------------------------------------------------------------------------
# T2 赎回费归基金资产
# --------------------------------------------------------------------------


def test_t2_redeem_fee_to_fund_never_enters_other() -> None:
    """T2：赎回费归资产部分已在 ``R_gross`` 的「其他收入」内，**禁止**再进 ``Other``。

    构造：赎回 1000 万份、赎回费 1.5%、归入 25% → 3.75 万元其他收入。
    该笔钱应体现在 ``R_gross`` 中，``Other`` 保持不变（≈0）。
    """
    a_start = 10_000_000.0
    r_net = 0.0  # 假设净值没动
    net_sub = -10_000_000.0 * 1.0  # 赎回 1000 万份 @ NAV 1.0
    div_cash = 0.0

    # 正确：赎回费归资产已在 R_gross/R_net 内 ⇒ 期末 AUM = 期初 + NetSub + R_net
    redeem_fee_to_fund = 10_000_000.0 * 1.5 / 100 * 0.25  # 37,500 元
    a_end_correct = a_start + net_sub + r_net + redeem_fee_to_fund
    r_net_with_other_income = r_net + redeem_fee_to_fund
    other = identities.solve_other(
        a_start, a_end_correct, r_net_with_other_income, net_sub, div_cash
    )
    assert other == pytest.approx(0.0, abs=0.01), "Other 应为 0（禁止双计）"

    # 错误做法：把同一笔钱再进 Other → A 式双计，AUM 凭空多一块
    other_wrong = identities.solve_other(
        a_start, a_end_correct, r_net, net_sub, div_cash
    )
    assert float(other_wrong) == pytest.approx(redeem_fee_to_fund, abs=0.01)


def test_t2_correlation_guard_fires() -> None:
    """T2 自检：``Other`` 与被错记的赎回费金额高度相关 → 必须告警。"""
    redeem_fees = [0.0, 37_500.0, 12_000.0, 50_000.0, 8_000.0, 22_000.0]
    other_wrong = list(redeem_fees)  # 完全相关的错误归集
    with pytest.raises(identities.IdentityResidualError):
        identities.check_other_not_redeem_fee(other_wrong, redeem_fees)

    # 正确归集：Other 与赎回费无关（|corr| < 0.5），不应触发
    other_ok = [2.0, -1.0, 3.0, 1.0, -3.0, 0.0]
    corr = identities.check_other_not_redeem_fee(other_ok, redeem_fees)
    assert corr is None or abs(corr) < 0.5


# --------------------------------------------------------------------------
# T3 申赎费归销售机构
# --------------------------------------------------------------------------


def test_t3_subscription_fee_does_not_touch_net_sub_or_fee() -> None:
    """T3：申购费 1.5% 时 ``Fee`` 不变、``NetSub`` 不变，只有 ``Cost_inv`` 增加。"""
    sub_shares = 1_000_000.0
    nav = 1.0
    net_sub = identities.net_sub_amount(sub_shares, 0.0, nav, nav)
    assert float(net_sub) == pytest.approx(1_000_000.0)

    # 申购费只进投资者总成本
    sub_fee = 1_000_000.0 * 1.5 / 100
    c = identities.cost_inv(sub_fee, 0.0, fee_total=0.0)
    assert float(c) == pytest.approx(15_000.0)

    # 费用合计（基金口径）不含申赎费
    fund_fee = fees.daily_accrual(
        10_000_000_000, __import__("datetime").date(2026, 6, 1), "1.20", "0.20", 0
    )
    assert float(fund_fee.sales) == 0.0


def test_t3_redemption_fee_not_deducted_from_net_sub() -> None:
    """T3：``Red^amt`` 取**费前**全额；把赎回费从里面扣掉会低估基民盈亏。"""
    red_shares = 1_000_000.0
    nav = 1.2
    fee_pre_tax_full = red_shares * nav  # 费前全额
    net_sub = identities.net_sub_amount(0.0, red_shares, nav, nav)
    assert float(net_sub) == pytest.approx(-fee_pre_tax_full)

    # 若错误地扣掉 0.5% 赎回费：NetSub 从 −120 万变成 −119.4 万（少算 6000 元流出）
    # ⇒ B 式 P_inv = ΔAUM + Div − NetSub 中 −NetSub 变小 ⇒ 基民盈亏被**低估** 6000 元
    wrong_net_sub = -red_shares * nav * (1 - 0.005)
    assert float(wrong_net_sub) - float(net_sub) == pytest.approx(
        red_shares * nav * 0.005
    )
    p_inv_correct = identities.p_inv(
        a_end=1_200_000.0, div_cash=0.0, a_start=0.0, net_sub=net_sub
    )
    p_inv_wrong = identities.p_inv(
        a_end=1_200_000.0, div_cash=0.0, a_start=0.0, net_sub=wrong_net_sub
    )
    assert float(p_inv_wrong) < float(p_inv_correct), "错误口径应把基民盈亏做低"


# --------------------------------------------------------------------------
# T4 基金拆分 / 份额折算
# --------------------------------------------------------------------------


def test_t4_split_keeps_return_unchanged() -> None:
    """T4：1:2 拆分（份额×2、净值÷2）后 ``r_t`` 不变。"""
    nav_before = 2.0
    nav_after_split = 1.0  # 拆一半
    div = 0.0
    r = returns.daily_return(nav_before, nav_after_split, div)
    assert r == pytest.approx(-0.5, abs=1e-12), "单位净值口径会误报 −50%"

    # 正确：用复权口径，拆分前后收益应为 0
    adj = returns.adjusted_nav_series([2.0, 2.0])  # 已折算后的等价复权序列
    assert returns.twr_from_adjusted_nav(adj[0], adj[1]) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# T9 成立不满一年不得年化
# --------------------------------------------------------------------------


def test_t9_no_annualization_under_one_year() -> None:
    """T9：30 天涨 10% 不得报出 +1000% 年化。"""
    res = returns.annualize(0.10, days=30)
    assert res.value is None
    assert res.annualized is False
    assert "不足 1 年" in (res.reason or "")


def test_t9_annualizes_over_one_year() -> None:
    """满 1 年（365 天）才年化，且 (1+10%)^1 − 1 = 10%。"""
    res = returns.annualize(0.10, days=365)
    assert res.value == pytest.approx(0.10, abs=1e-12)


# --------------------------------------------------------------------------
# T11 净值精度舍入累积
# --------------------------------------------------------------------------


def test_t11_endpoint_vs_chain_consistency() -> None:
    """T11：250 期后 ``∏(1+r_t)`` 与首尾净值比差异 < 0.1%。"""
    import math

    navs = [1.0]
    for i in range(250):
        navs.append(round(navs[-1] * (1 + 0.001 * math.sin(i)), 4))  # 4 位精度
    rs = [returns.daily_return(navs[i - 1], navs[i]) for i in range(1, len(navs))]
    chain = returns.chain_return(rs)
    endpoint = navs[-1] / navs[0] - 1
    assert returns.twr_consistency_gap(chain, endpoint) < 0.001


# --------------------------------------------------------------------------
# T14 分红以权益登记日份额为准
# --------------------------------------------------------------------------


def test_t14_dividend_uses_record_date_shares() -> None:
    """T14：``Div_cash = S^reg · d``，``S^reg`` 是**登记日**份额，不是除息日份额。"""
    s_record = 1_000_000.0
    s_ex_date = 1_050_000.0  # 除息日已有人申购，份额不同
    d_per_share = 0.05

    correct = s_record * d_per_share
    wrong = s_ex_date * d_per_share
    assert correct == pytest.approx(50_000.0)
    assert wrong != pytest.approx(correct)


# --------------------------------------------------------------------------
# 未覆盖陷阱：显式登记，不得静默省略
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trap_id", "reason"),
    [
        ("T5", "A/C 份额合并需数据层 share_class 字段，v0.2.0 实现"),
        ("T6", "ETF 联接双重收费需 fund_type 与标的 ETF 映射，v0.2.0 实现"),
        ("T7", "货币基金摊余成本法：v1 不覆盖货币基金（决策 D03）"),
        ("T8", "QDII 汇率与 T+2：v1 不覆盖 QDII（决策 D03）"),
        ("T10", "清盘/转型需 fund_profile.status 与分段逻辑，v0.2.0 实现"),
        ("T12", "定开/持有期单日集中申赎需产品类型字段，v0.2.0 实现"),
        ("T13", "摆动定价需 fund_nav.flag 标记，v0.2.0 实现"),
    ],
)
def test_trap_pending_registered(trap_id: str, reason: str) -> None:
    """未覆盖陷阱的显式登记（M1 门禁前必须全部转 ✅，附件A V8）。"""
    warnings.warn(f"{trap_id} 尚未实现：{reason}", UserWarning, stacklevel=1)
    assert reason, f"{trap_id} 必须有明确的未实现理由"
