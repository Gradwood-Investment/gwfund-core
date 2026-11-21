"""SPEC-GAP-01~05 的落地测试（《附件A》§4.7，2026-11-21 回写）。

这 5 条是**规范原有缺口**（代码有、规范无），由文档作者回写定义后，
实现方必须按新定义对齐并留测试。

=====  ================================  ==================================
GAP    内容                              本文件对应测试
=====  ================================  ==================================
01     HWM 上移条件                      test_gap01_*
02     div_effect 符号                   见 test_attribution_gates.py
03     交易成本买卖方向                   test_gap03_*
04     按「期数」年化的适用边界           test_gap04_*
05     PrecisionLevel.UNAVAILABLE 触发    test_gap05_*
=====  ================================  ==================================
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from gwfund_core import fees, returns
from gwfund_core.errors import InsufficientDataError
from gwfund_core.types import PrecisionLevel

YI = 100_000_000


# ==========================================================================
# SPEC-GAP-01 · HWM 上移条件
# ==========================================================================


def test_gap01_hwm_frozen_when_gain_below_hurdle() -> None:
    """涨 3% < 门槛 5% → 高水位不动（原实现会错误上移）。"""
    fee, hwm = fees.performance_fee_daily(
        YI, nav_prev=1.03, hwm_prev=1.00, hurdle=0.05, share_pct=20, on=date(2026, 6, 1)
    )
    assert float(fee) == 0.0
    assert hwm == pytest.approx(1.00)


def test_gap01_hwm_moves_when_hurdle_exceeded() -> None:
    """涨 8% > 门槛 5% → 计提，且高水位上移至 N_t。"""
    fee, hwm = fees.performance_fee_daily(
        YI, nav_prev=1.08, hwm_prev=1.00, hurdle=0.05, share_pct=20, on=date(2026, 6, 1)
    )
    assert float(fee) == pytest.approx(YI * 0.03 * 0.20, abs=1.0)
    assert hwm == pytest.approx(1.08)


def test_gap01_explicit_contract_variant_is_opt_in() -> None:
    """合同约定「HWM 无条件跟随」时走显式变体，**不得默认**。"""
    _, hwm_default = fees.performance_fee_daily(
        YI, 1.03, 1.00, 0.05, 20, date(2026, 6, 1)
    )
    _, hwm_follows = fees.performance_fee_daily_hwm_follows_nav(
        YI, 1.03, 1.00, 0.05, 20, date(2026, 6, 1)
    )
    assert hwm_default == pytest.approx(1.00)
    assert hwm_follows == pytest.approx(1.03)


# ==========================================================================
# SPEC-GAP-03 · 交易成本买卖方向
# ==========================================================================


def test_gap03_side_required_and_stamp_only_on_sell() -> None:
    both = fees.trade_cost_rate_both_sides(YI)
    # Decimal 与 pytest.approx 不直接兼容，转 float 比较
    assert float(both["sell"] - both["buy"]) == pytest.approx(0.05)
    with pytest.raises(TypeError):
        fees.trade_cost_rate_pct(YI)  # type: ignore[call-arg]


# ==========================================================================
# SPEC-GAP-04 · 按「期数」年化的适用边界
# ==========================================================================


def test_gap04_periods_method_marked_as_example_only() -> None:
    """``annualize_by_periods`` 的 docstring 必须写明「生产路径禁用」。"""
    doc = returns.annualize_by_periods.__doc__ or ""
    assert "生产" in doc and "禁用" in doc, "SPEC-GAP-04 要求 docstring 显式警示生产禁用"
    assert "等间隔" in doc


def test_gap04_periods_power_math() -> None:
    """季度 4 期：(1+0.045)^4 − 1 = 19.2519%。"""
    assert returns.annualize_by_periods(0.045, 4) == pytest.approx(0.1925186, abs=1e-7)


def test_gap04_calendar_method_is_production_default() -> None:
    """生产口径用日历天数：730 天 21% → (1.21)^(365/730) − 1 = 10%。"""
    r = returns.annualize(0.21, 730)
    assert r.annualized is True
    assert r.value == pytest.approx(0.10, abs=1e-12)


def test_gap04_both_methods_agree_on_one_year() -> None:
    """满 1 年时两法应一致（这是唯一的交叉校验点）。"""
    a = returns.annualize(0.10, 365)
    b = returns.annualize_by_periods(0.10, 1)
    assert a.value == pytest.approx(b, abs=1e-12)


# ==========================================================================
# SPEC-GAP-05 · PrecisionLevel 四值 + JSON 往返
# ==========================================================================


def test_gap05_four_fixed_values() -> None:
    """枚举值固定为 A/B/C/UNAVAILABLE 四值，**不写「—」、不写空串、不写 null**。"""
    assert {p.value for p in PrecisionLevel} == {"A", "B", "C", "UNAVAILABLE"}


def test_gap05_labels_match_spec() -> None:
    """中文标签固定（附件A §6.4 / SPEC-GAP-05）。"""
    assert PrecisionLevel.A.label_zh == "精确"
    assert PrecisionLevel.B.label_zh == "估算·季末阶梯"
    assert PrecisionLevel.C.label_zh == "插值估算"
    assert PrecisionLevel.UNAVAILABLE.label_zh == "数据缺失"


def test_gap05_json_roundtrip() -> None:
    """四端序列化必须能往返：``json`` 编解码后仍是同一值。"""
    for p in PrecisionLevel:
        encoded = json.dumps({"level": p.value}, ensure_ascii=False)
        decoded = json.loads(encoded)["level"]
        assert decoded == p.value
        assert PrecisionLevel(decoded) is p


def test_gap05_unavailable_never_serialized_as_empty() -> None:
    """``UNAVAILABLE`` 不得被序列化成空串或 null（四端对拍会失败）。"""
    payload = json.dumps({"level": PrecisionLevel.UNAVAILABLE.value})
    assert '"UNAVAILABLE"' in payload
    assert 'null' not in payload
    assert '""' not in payload


def test_gap05_str_enum_behaves_like_str() -> None:
    """``str`` 枚举：可与字符串直接比较（便于 API 出参）。"""
    assert PrecisionLevel.A == "A"
    assert f"{PrecisionLevel.B.value}" == "B"


def test_gap04_total_loss_raises() -> None:
    """区间亏损 100% 无法年化 → 抛 InsufficientDataError，不得返回 0。"""
    with pytest.raises(InsufficientDataError):
        returns.annualize_by_periods(-1.0, 4)
