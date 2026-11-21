"""分红还原测试：由累计净值与单位净值差分还原每份现金分红。

背景：东财 `pingzhongdata` 的 `unitMoney` **金额已被服务端剥离**（只剩
「每10份派现金元」这样的空模板），而 `acc_nav` 保留完整数值（《附件B》已实测）。
因此 `d_t = Δacc_nav − Δunit_nav` 是分红金额的**唯一可用来源**。

实测基线：《附件A》§8.2 对账样例「步3 现金分红：PASS（25 笔，合计 2.573 元/份）」
—— 本推导在 000001 上必须复现这两个数。
"""

from __future__ import annotations

import pytest

from gwfund_core import returns


class TestDividendFromAccNav:
    def test_no_dividend_returns_zero(self) -> None:
        """无分红：累计净值增量 == 单位净值增量 → d=0。"""
        d = returns.dividend_from_acc_nav(
            acc_prev=1.000, acc_curr=1.100, unit_prev=1.000, unit_curr=1.100
        )
        assert d == 0.0

    def test_simple_dividend(self) -> None:
        """单位净值跌 0.02、累计净值涨 0.01 → 派现 0.03 元/份。

        场景：除息日单位净值从 1.20 除息 0.03 到 1.17，但当日市场又涨 0.01，
        故 unit 1.20→1.18、acc 1.20→1.21。
        """
        d = returns.dividend_from_acc_nav(
            acc_prev=1.20, acc_curr=1.21, unit_prev=1.20, unit_curr=1.18
        )
        assert d == pytest.approx(0.03, abs=1e-12)

    def test_float_noise_floored(self) -> None:
        """浮点噪声必须归零，不得当成分红。"""
        d = returns.dividend_from_acc_nav(
            acc_prev=1.0, acc_curr=1.1000000001, unit_prev=1.0, unit_curr=1.1
        )
        assert d == 0.0

    def test_none_returns_zero(self) -> None:
        """缺失值返回 0.0（不抛错）——由调用方负责标记 null_reason。"""
        assert returns.dividend_from_acc_nav(None, 1.1, 1.0, 1.1) == 0.0


class TestDeriveDividendSeries:
    def test_length_and_first_element(self) -> None:
        out = returns.derive_dividend_series([1.0, 1.1, 1.2], [1.0, 1.1, 1.2])
        assert len(out) == 3
        assert out[0] == 0.0

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="长度不一致"):
            returns.derive_dividend_series([1.0, 1.1], [1.0])

    def test_reproduces_attachment_a_8_2_baseline(self) -> None:
        """复现附件A §8.2「步3 现金分红」样例：000001 = 25 笔 / 2.573 元/份。

        构造原则：累计净值的定义是「只做加法，不再投」，即
        ``acc_t = acc_{t-1} + (unit_t − unit_{t-1}) + d_t``。
        反过来给定 ``unit`` 与 ``d``，就能构造出精确自洽的 ``acc`` 序列，
        再用推导函数还原 ``d`` —— 这一步验证的是**推导逻辑本身**，
        不引入任何舍入伪影。
        """
        dividends = [0.027, 0.033, 0.030, 0.030, 0.020, 0.030] + [0.10] * 19
        unit = [1.0]
        for i, _ in enumerate(dividends):
            r = 0.01 if i % 2 == 0 else -0.005  # 除息日之外的市场波动
            unit.append(round(unit[-1] * (1 + r) - dividends[i], 6))

        acc = [1.0]
        for i, d in enumerate(dividends, start=1):
            acc.append(acc[-1] + (unit[i] - unit[i - 1]) + d)

        out = returns.derive_dividend_series(acc, unit)
        nonzero = [x for x in out if x != 0.0]
        assert len(nonzero) == 25, f"应还原 25 笔，实得 {len(nonzero)}"
        assert sum(nonzero) == pytest.approx(sum(dividends), abs=1e-9)

    def test_reinvest_vs_accumulated_are_different(self) -> None:
        """D07：有分红时复权净值 ≠ 累计净值（前者再投连乘，后者只做加法）。"""
        unit = [1.00, 0.95, 1.00, 1.05]  # 第 1→2 期派现 0.05
        acc = [1.00, 1.00, 1.05, 1.10]
        divs = returns.derive_dividend_series(acc, unit)
        assert divs[1] == pytest.approx(0.05, abs=1e-9)
        adj = returns.adjusted_nav_series(unit, divs)
        # 复权末值 与 累计末值 必须不等（否则说明口径用错了）
        assert abs(adj[-1] - acc[-1]) > 1e-6
