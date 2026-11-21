"""裁定书三 §二 C1–C4 的验收测试。

《裁定书三》原话：「实现 §二 C1–C4（诊断可见性、默认不返回、对外拦截、告警留痕）
——**四条各一个测试**」。本文件严格按此组织，每条条件一组。

同时覆盖阶梯门（M1 诊断 → M2 ≤5% → M3 ≤1%）的进度判定。
"""

from __future__ import annotations

import logging

import pytest

from gwfund_core import diagnostics as dg


SEGMENTS = [
    {"key": "a_start", "label": "期初 AUM", "amount_wan": 1000.0},
    {"key": "net_sub", "label": "净申购贡献", "amount_wan": -100.0},
    {"key": "r_net", "label": "投资收益贡献", "amount_wan": 50.0},
]


# ==========================================================================
# C1 · 每次归因输出必须携带 reconcile_gap + estimate_grade
# ==========================================================================


class TestC1FieldsAlwaysPresent:
    def test_envelope_carries_both_fields_when_c_grade(self) -> None:
        """C 级 + 未请求诊断 → 分量 None，但两个字段仍在。"""
        env = dg.build_attribution(
            "000001",
            segments=SEGMENTS,
            reconcile_gap=1.084,
            estimate_grade="C",
        )
        assert env.reconcile_gap == 1.084
        assert env.estimate_grade == "C"
        assert env.components is None

    def test_envelope_carries_both_fields_when_a_grade(self) -> None:
        env = dg.build_attribution(
            "000001", segments=SEGMENTS, reconcile_gap=0.001, estimate_grade="A"
        )
        assert env.reconcile_gap == 0.001
        assert env.estimate_grade == "A"
        assert env.components is not None

    def test_gap_none_still_present(self) -> None:
        """无法计算双路差时，字段必须是 ``None`` 而不是消失。"""
        env = dg.build_attribution(
            "000001", segments=SEGMENTS, reconcile_gap=None, estimate_grade="C"
        )
        assert env.reconcile_gap is None
        assert env.estimate_grade == "C"
        assert env.reconcile_passed is False


# ==========================================================================
# C2 · C 级归因默认不出现在 API/导出，需显式 include_diagnostics
# ==========================================================================


class TestC2DefaultHidden:
    def test_c_grade_default_hides_components(self) -> None:
        env = dg.build_attribution(
            "000001", segments=SEGMENTS, reconcile_gap=1.08, estimate_grade="C"
        )
        assert env.components is None
        assert env.diagnostics_included is False
        assert any("C2" in n for n in env.notes)

    def test_c_grade_explicit_request_returns_components(self) -> None:
        env = dg.build_attribution(
            "000001",
            segments=SEGMENTS,
            reconcile_gap=1.08,
            estimate_grade="C",
            include_diagnostics=True,
        )
        assert env.components is not None
        assert env.diagnostics_included is True
        assert len(env.components) == len(SEGMENTS)

    @pytest.mark.parametrize("grade", ["A", "B"])
    def test_ab_grade_returns_by_default(self, grade: str) -> None:
        env = dg.build_attribution(
            "000001", segments=SEGMENTS, reconcile_gap=0.001, estimate_grade=grade
        )
        assert env.components is not None

    def test_helper_matches_envelope_behaviour(self) -> None:
        assert dg.should_include_components("C") is False
        assert dg.should_include_components("C", include_diagnostics=True) is True
        assert dg.should_include_components("A") is True
        assert dg.should_include_components("B") is True


# ==========================================================================
# C3 · 对外文章、分享卡片、报告禁止引用 C 级分量
# ==========================================================================


class TestC3ExportBlocked:
    @pytest.mark.parametrize("target", ["article", "share_card", "report"])
    def test_c_grade_blocks_external_targets(self, target: str) -> None:
        with pytest.raises(dg.DiagnosticNotAllowedError, match="C3"):
            dg.assert_export_allowed("C", target=target)  # type: ignore[arg-type]

    @pytest.mark.parametrize("grade", ["A", "B"])
    @pytest.mark.parametrize("target", ["article", "share_card", "report"])
    def test_ab_grade_allowed(self, grade: str, target: str) -> None:
        dg.assert_export_allowed(grade, target=target)  # type: ignore[arg-type]

    def test_c_grade_allowed_for_api_with_explicit_request(self) -> None:
        """api 走 C2 的显式请求机制，不在 C3 拦截范围。"""
        dg.assert_export_allowed("C", target="api")

    def test_error_message_explains_why(self) -> None:
        with pytest.raises(dg.DiagnosticNotAllowedError) as ei:
            dg.assert_export_allowed("C", target="article")
        msg = str(ei.value)
        assert "不可信" in msg or "未达" in msg


# ==========================================================================
# C4 · 双路失败必须 WARN 日志 + 页面角标，不得静默降级
# ==========================================================================


class TestC4LoudFailure:
    def test_alert_triggered_on_failure(self) -> None:
        triggered, code, msg = dg.reconcile_alert(1.084)
        assert triggered is True
        assert code == "RNET_RECONCILE_FAIL"
        assert "108.40%" in msg or "gap=" in msg

    def test_no_alert_when_passed(self) -> None:
        triggered, code, _ = dg.reconcile_alert(0.001)
        assert triggered is False
        assert code == "RNET_RECONCILE_OK"

    def test_unavailable_also_alerts(self) -> None:
        """无法计算也要告警——静默当通过是 C4 明确禁止的。"""
        triggered, code, msg = dg.reconcile_alert(None)
        assert triggered is True
        assert code == "RNET_RECONCILE_UNAVAILABLE"
        assert "不得静默" in msg

    def test_badge_non_empty_when_c_grade(self) -> None:
        badge = dg.badge_text("C", False)
        assert badge
        assert "C" in badge or "仅供参考" in badge

    def test_badge_non_empty_when_failed_but_not_c(self) -> None:
        badge = dg.badge_text("B", False)
        assert badge

    def test_badge_empty_when_all_good(self) -> None:
        assert dg.badge_text("A", True) == ""

    def test_envelope_carries_badge_and_alert_code(self) -> None:
        env = dg.build_attribution(
            "000001", segments=SEGMENTS, reconcile_gap=1.08, estimate_grade="C"
        )
        assert env.badge
        assert env.alert_code == "RNET_RECONCILE_FAIL"
        assert env.alert_message

    def test_core_stays_pure_no_logging(self) -> None:
        """C4 的留痕由 API 写日志；core 只判定——架构门禁止 core 引 logging。"""
        import ast
        import pathlib

        src = pathlib.Path(dg.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "logging" not in imported, "core 不得 import logging（零依赖纯函数）"


# ==========================================================================
# 阶梯门（M1 仅诊断 → M2 ≤5% → M3 ≤1%）
# ==========================================================================


class TestLadder:
    def test_m1_has_no_target(self) -> None:
        st = dg.ladder_status("M1", 1.08)
        assert st["target"] is None
        assert st["reached"] is None

    def test_m2_target_is_5pct(self) -> None:
        assert dg.LADDER_TARGETS["M2"] == 0.05
        assert dg.ladder_status("M2", 0.04)["reached"] is True
        assert dg.ladder_status("M2", 0.06)["reached"] is False

    def test_m3_target_is_1pct(self) -> None:
        assert dg.LADDER_TARGETS["M3"] == 0.01
        assert dg.ladder_status("M3", 0.009)["reached"] is True
        assert dg.ladder_status("M3", 0.02)["reached"] is False

    def test_miss_note_mentions_permanent_c(self) -> None:
        """未达标的后果必须写明：永久标 C、只展示区间说明。"""
        st = dg.ladder_status("M2", 0.30)
        assert "永久标 C" in st["note"]

    def test_current_state_fails_m2(self) -> None:
        """当前实测（000001 双路差 108.4%）连 M2 的 5% 都达不到。"""
        assert dg.ladder_status("M2", 1.084)["reached"] is False
