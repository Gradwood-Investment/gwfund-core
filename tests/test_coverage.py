"""指标覆盖矩阵测试：把 `COVERAGE.md` 的声明纳入 CI。

矩阵里每个标「✅ core 实现」的指标都必须真实存在对应函数——
否则矩阵会随时间腐烂成一张自说自话的表。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
CHECKER = CORE_ROOT / "scripts" / "check_coverage.py"


def test_coverage_checker_exists() -> None:
    assert CHECKER.exists(), f"覆盖校验脚本缺失：{CHECKER}"


def test_all_covered_indicators_have_functions() -> None:
    """跑校验脚本，要求全部映射有效。"""
    proc = subprocess.run(
        [sys.executable, str(CHECKER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        "指标覆盖校验失败：\n" + (proc.stdout or "") + (proc.stderr or "")
    )


def test_coverage_counts_are_stable() -> None:
    """把 47 / 37 这两个数钉死：变了说明指标增减，必须回来更新文档。"""
    sys.path.insert(0, str(CORE_ROOT / "src"))
    sys.path.insert(0, str(CHECKER.parent))
    import importlib.util

    spec = importlib.util.spec_from_file_location("cc", CHECKER)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    assert len(mod.COVERAGE) == 47, (
        f"指标总数变为 {len(mod.COVERAGE)}；若 SOW §5.3 有增减，请同步 COVERAGE.md 与本断言"
    )
    core_impl = sum(1 for v in mod.COVERAGE.values() if v is not None)
    assert core_impl == 37, f"core 实现数变为 {core_impl}（预期 37）"
    assert len(mod.NEEDS_DISCLOSURE) == 4, "需披露值的指标数变了"


def test_coverage_doc_exists() -> None:
    doc = CORE_ROOT / "COVERAGE.md"
    assert doc.exists(), "COVERAGE.md 缺失"
    text = doc.read_text(encoding="utf-8")
    for mid in ("M101", "M219", "M301", "M313"):
        assert mid in text, f"COVERAGE.md 缺 {mid}"
