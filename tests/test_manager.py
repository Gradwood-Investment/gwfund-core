"""基金经理口径算法测试（M401–M420）。

重点覆盖三个**容易搞错但不会报错**的口径：

1. **空窗期跳过，不记 0 收益** —— 记 0 会把空窗当「持币不动」
2. **年化分母是 D_eff**，不是首末任职跨度
3. **共管等分是假设** —— `is_estimate` 必须为 True 且带说明
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from gwfund_core.manager import (
    AttributedPnl,
    Regime,
    active_funds_on,
    annualized_career,
    attributed_pnl,
    career_twr,
    co_management_coefficient,
    daily_manager_return,
    effective_days,
)

D = date


# --------------------------------------------------------------------------- #
# Regime：左闭右开
# --------------------------------------------------------------------------- #


def test_regime_covers_left_closed_right_open() -> None:
    r = Regime("m1", "000001", D(2024, 1, 1), D(2024, 1, 4))
    assert r.covers(D(2024, 1, 1)), "起始日应包含（左闭）"
    assert r.covers(D(2024, 1, 3))
    assert not r.covers(D(2024, 1, 4)), "结束日应排除（右开）"
    assert not r.covers(D(2023, 12, 31))


def test_regime_open_ended_covers_future() -> None:
    r = Regime("m1", "000001", D(2024, 1, 1), None)
    assert r.covers(D(2030, 1, 1))


def test_regime_rejects_inverted_interval() -> None:
    with pytest.raises(ValueError, match="左闭右开"):
        Regime("m1", "000001", D(2024, 1, 5), D(2024, 1, 5))
    with pytest.raises(ValueError):
        Regime("m1", "000001", D(2024, 1, 5), D(2024, 1, 1))


def test_regime_n_days_is_right_open() -> None:
    """[1-01, 1-04) 是 3 天，不是 4 天。"""
    assert Regime("m1", "f", D(2024, 1, 1), D(2024, 1, 4)).n_days == 3
    assert Regime("m1", "f", D(2024, 1, 1), None).n_days is None


# --------------------------------------------------------------------------- #
# D_eff
# --------------------------------------------------------------------------- #


def test_effective_days_single_regime() -> None:
    assert effective_days([Regime("m1", "f", D(2024, 1, 1), D(2024, 1, 11))]) == 10


def test_effective_days_dedups_overlapping_funds() -> None:
    """同一天管两只基金，仍只算 1 天。"""
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), D(2024, 1, 6)),
        Regime("m1", "f2", D(2024, 1, 1), D(2024, 1, 6)),
    ]
    assert effective_days(regs) == 5, "不应按基金重复计数"


def test_effective_days_excludes_gap() -> None:
    """**核心**：空窗期不计入 D_eff。"""
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), D(2024, 1, 6)),    # 5 天
        Regime("m1", "f2", D(2024, 2, 1), D(2024, 2, 11)),   # 10 天
    ]
    assert effective_days(regs) == 15, "1 月空窗到 2 月之间的 26 天不应计入"


def test_effective_days_open_ended_uses_as_of() -> None:
    regs = [Regime("m1", "f1", D(2024, 1, 1), None)]
    assert effective_days(regs, as_of=D(2024, 1, 11)) == 10


# --------------------------------------------------------------------------- #
# F(m,t) 与共管系数
# --------------------------------------------------------------------------- #


def test_active_funds_on() -> None:
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), D(2024, 1, 6)),
        Regime("m1", "f2", D(2024, 2, 1), None),
    ]
    assert active_funds_on(regs, "m1", D(2024, 1, 3)) == {"f1"}
    assert active_funds_on(regs, "m1", D(2024, 3, 1)) == {"f2"}
    assert active_funds_on(regs, "m1", D(2024, 1, 20)) == set(), "空窗期应为空集"


def test_co_management_single() -> None:
    regs = [Regime("m1", "f1", D(2024, 1, 1), None)]
    assert co_management_coefficient(regs, "f1", D(2024, 1, 3)) == Decimal(1)


def test_co_management_two_managers() -> None:
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), None),
        Regime("m2", "f1", D(2024, 1, 1), None),
    ]
    assert co_management_coefficient(regs, "f1", D(2024, 1, 3)) == Decimal("0.5")


def test_co_management_sum_equals_one() -> None:
    """共管拆分后各经理之和 = 1（不重不漏）。"""
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), None),
        Regime("m2", "f1", D(2024, 1, 1), None),
        Regime("m3", "f1", D(2024, 1, 1), None),
    ]
    total = sum(co_management_coefficient(regs, "f1", D(2024, 1, 3)) for _ in range(3))
    # 1/3 在 Decimal 下也不是精确值（28 位有效数字），量化到分后才是 1.00
    assert total.quantize(Decimal("0.01")) == Decimal("1.00"), (
        f"实际 {total} —— Decimal(1)/3 不是精确值，这是已知的浮点/定点限制"
    )


def test_co_management_no_manager_raises() -> None:
    with pytest.raises(ValueError, match="无在任经理"):
        co_management_coefficient([], "f1", D(2024, 1, 3))


# --------------------------------------------------------------------------- #
# M4.1 日收益
# --------------------------------------------------------------------------- #


def test_daily_return_aum_weighted() -> None:
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), None),
        Regime("m1", "f2", D(2024, 1, 1), None),
    ]
    r = daily_manager_return(
        regs, "m1", D(2024, 1, 3),
        fund_returns={"f1": 0.10, "f2": 0.00},
        fund_aum={"f1": Decimal(300), "f2": Decimal(100)},
    )
    # (300*0.10 + 100*0.0) / 400 = 0.075
    assert r == pytest.approx(0.075)


def test_daily_return_missing_fund_excluded_not_zero() -> None:
    """**核心**：无收益数据的基金不进分子**也不进分母**（不是按 0 算）。"""
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), None),
        Regime("m1", "f2", D(2024, 1, 1), None),
    ]
    r = daily_manager_return(
        regs, "m1", D(2024, 1, 3),
        fund_returns={"f1": 0.10},          # f2 缺失
        fund_aum={"f1": Decimal(100), "f2": Decimal(900)},
    )
    assert r == pytest.approx(0.10), "若把 f2 当 0 收益，结果会变成 0.01（错）"


def test_daily_return_all_missing_is_none() -> None:
    regs = [Regime("m1", "f1", D(2024, 1, 1), None)]
    assert daily_manager_return(regs, "m1", D(2024, 1, 3), fund_returns={}) is None


def test_daily_return_no_funds_is_none() -> None:
    """空窗期返回 None，不是 0。"""
    regs = [Regime("m1", "f1", D(2024, 1, 1), D(2024, 1, 6))]
    assert daily_manager_return(regs, "m1", D(2024, 2, 1), {"f1": 0.05}) is None


# --------------------------------------------------------------------------- #
# M4.2 生涯 TWR
# --------------------------------------------------------------------------- #


def test_career_twr_chains() -> None:
    rets = {D(2024, 1, 1): 0.10, D(2024, 1, 2): 0.10}
    assert career_twr(rets) == pytest.approx(1.10 * 1.10 - 1)


def test_career_twr_skips_gap_not_zero() -> None:
    """**核心**：空窗期跳过，不记 0 收益。

    若记 0，乘积会多乘几次 1.0 —— 数值上看似无变化，
    但一旦空窗期有「缺失数据」被当 0，就会把真实收益稀释。
    这里通过 active_days 只给两天来验证。
    """
    rets = {
        D(2024, 1, 1): 0.10,
        D(2024, 1, 2): 0.10,
        D(2024, 6, 1): 0.50,   # 空窗期内，不应计入
    }
    active = [D(2024, 1, 1), D(2024, 1, 2)]
    assert career_twr(rets, active_days=active) == pytest.approx(0.21)
    # 不给 active_days 时会全算进来
    assert career_twr(rets) == pytest.approx(1.1 * 1.1 * 1.5 - 1)


def test_career_twr_missing_day_skipped() -> None:
    """active_days 里有但 rets 里没有 → 跳过，不记 0。"""
    rets = {D(2024, 1, 1): 0.10}
    active = [D(2024, 1, 1), D(2024, 1, 2)]   # 1-02 无数据
    assert career_twr(rets, active_days=active) == pytest.approx(0.10)


def test_career_twr_empty() -> None:
    assert career_twr({}) == 0.0


# --------------------------------------------------------------------------- #
# 年化
# --------------------------------------------------------------------------- #


def test_annualized_uses_deff() -> None:
    """**核心**：分母是 D_eff。365 天 +35% → 年化 35%。"""
    assert annualized_career(0.35, 365) == pytest.approx(0.35)


def test_annualized_short_tenure_returns_none() -> None:
    """短任期年化无统计意义，返回 None 而不是荒谬数字。"""
    assert annualized_career(0.10, 30) is None
    assert annualized_career(0.10, 89) is None
    assert annualized_career(0.10, 90) is not None


def test_annualized_not_using_span() -> None:
    """反证：若误用「首末跨度」而非 D_eff，年化会明显偏大。"""
    twr = 0.21          # 两个交易日各 +10%
    d_eff = 2           # 真实有效天数
    span = 365          # 首末跨度（含空窗）
    # D_eff 太短 → None（正确行为）
    assert annualized_career(twr, d_eff) is None
    # 若错用 span，会给出 21% 这种"看起来正常"的数字 —— 这正是危险的
    assert annualized_career(twr, span) == pytest.approx(0.21, abs=1e-6)


# --------------------------------------------------------------------------- #
# M5.1 归属盈亏
# --------------------------------------------------------------------------- #


def test_attributed_pnl_single_manager_full() -> None:
    regs = [Regime("m1", "f1", D(2024, 1, 1), None)]
    pnl = {("f1", D(2024, 1, 2)): Decimal(100)}
    out = attributed_pnl(regs, "m1", pnl)
    assert out.pnl == Decimal(100), "独管应拿全部"
    assert out.n_funds == 1


def test_attributed_pnl_split_equal() -> None:
    """共管两人各得一半。"""
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), None),
        Regime("m2", "f1", D(2024, 1, 1), None),
    ]
    pnl = {("f1", D(2024, 1, 2)): Decimal(100)}
    a = attributed_pnl(regs, "m1", pnl)
    b = attributed_pnl(regs, "m2", pnl)
    assert a.pnl == Decimal(50)
    assert b.pnl == Decimal(50)
    assert a.pnl + b.pnl == Decimal(100), "两经理之和必须等于基金盈亏（验收判据 2）"


def test_attributed_pnl_is_estimate_flagged() -> None:
    """**必须**标记为估计值并带说明 —— 附件A §13 铁律。"""
    regs = [Regime("m1", "f1", D(2024, 1, 1), None)]
    out = attributed_pnl(regs, "m1", {("f1", D(2024, 1, 2)): Decimal(1)})
    assert out.is_estimate is True
    assert "等分" in out.assumption
    assert "估计" in out.assumption


def test_attributed_pnl_excludes_out_of_regime() -> None:
    """不在任期内的盈亏不得计入。"""
    regs = [Regime("m1", "f1", D(2024, 1, 1), D(2024, 1, 6))]
    pnl = {
        ("f1", D(2024, 1, 3)): Decimal(100),   # 在任
        ("f1", D(2024, 2, 1)): Decimal(999),   # 已离任，不应计入
    }
    out = attributed_pnl(regs, "m1", pnl)
    assert out.pnl == Decimal(100)


def test_attributed_pnl_three_way_split() -> None:
    regs = [Regime(f"m{i}", "f1", D(2024, 1, 1), None) for i in range(3)]
    pnl = {("f1", D(2024, 1, 2)): Decimal(90)}
    parts = [attributed_pnl(regs, f"m{i}", pnl).pnl for i in range(3)]
    assert sum(parts) == Decimal(90), f"和 = {sum(parts)}，应为 90"
    assert parts[0] == Decimal(30) and parts == [Decimal(30)] * 3


def test_split_equally_sums_exactly() -> None:
    """**验收判据 2 的核心**：等分后求和必须精确等于原额。"""
    from gwfund_core.manager import split_equally

    for amount, n in [
        (Decimal("100"), 3), (Decimal("100"), 7), (Decimal("0.01"), 3),
        (Decimal("1234.56"), 6), (Decimal("100"), 2),
    ]:
        parts = split_equally(amount, n)
        assert len(parts) == n
        assert sum(parts) == amount, f"{amount} 分 {n} 份，和 = {sum(parts)}，应为 {amount}"


def test_split_equally_remainder_to_first() -> None:
    from gwfund_core.manager import split_equally

    assert split_equally(Decimal("100"), 3) == [
        Decimal("33.34"), Decimal("33.33"), Decimal("33.33")
    ]


def test_split_equally_n_equals_1() -> None:
    from gwfund_core.manager import split_equally

    assert split_equally(Decimal("99.99"), 1) == [Decimal("99.99")]


def test_split_equally_rejects_zero() -> None:
    from gwfund_core.manager import split_equally

    with pytest.raises(ValueError):
        split_equally(Decimal("1"), 0)


def test_attributed_pnl_three_way_sums_exactly() -> None:
    """三位共管时，三人归属之和必须**精确**等于基金盈亏。"""
    regs = [Regime(f"m{i}", "f1", D(2024, 1, 1), None) for i in range(3)]
    pnl = {("f1", D(2024, 1, 2)): Decimal("100.00")}
    parts = [attributed_pnl(regs, f"m{i}", pnl).pnl for i in range(3)]
    assert sum(parts) == Decimal("100.00"), f"和 = {sum(parts)}，应为 100.00"
    assert parts == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]


def test_attributed_pnl_multi_day_multi_fund() -> None:
    regs = [
        Regime("m1", "f1", D(2024, 1, 1), None),
        Regime("m1", "f2", D(2024, 1, 1), None),
        Regime("m2", "f2", D(2024, 1, 1), None),
    ]
    pnl = {
        ("f1", D(2024, 1, 2)): Decimal(100),   # m1 独管，全得
        ("f2", D(2024, 1, 2)): Decimal(100),   # m1/m2 共管，各半
    }
    out = attributed_pnl(regs, "m1", pnl)
    assert out.pnl == Decimal(150)             # 100 + 50
    assert out.n_funds == 2
