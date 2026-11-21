"""契约对拍测试：把 `contracts/golden_vectors.json` 纳入 core 的 pytest。

这条测试与 `scripts/check_vectors.py` 是同一套逻辑的两个入口：
脚本给 CI/其他端用，测试给开发者在本地 `pytest` 时立刻发现漂移。

为何需要它：Tier-2 回归向量的期望值由引擎冻结，一旦有人改了实现导致数字变化，
普通单测可能仍然全绿（因为单测写的是同一套假设），只有对拍契约才会报警。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[1]
CHECKER = CORE_ROOT / "scripts" / "check_vectors.py"


def _vectors_path() -> Path:
    return CORE_ROOT / "contracts" / "golden_vectors.json"


@pytest.mark.golden
def test_contract_file_exists() -> None:
    """契约文件必须存在且可解析。"""
    path = _vectors_path()
    assert path.exists(), (
        f"契约缺失：{path}\n"
        f"生成方式：python contracts/build_golden_vectors.py"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["vectors"], "契约内无向量"
    assert data["tolerance"] > 0


@pytest.mark.golden
def test_all_vectors_pass() -> None:
    """跑对拍器，要求 100% 通过（四端一致性门）。"""
    if not CHECKER.exists():
        pytest.skip("对拍器脚本缺失")
    proc = subprocess.run(
        [sys.executable, str(CHECKER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        "四端一致性对拍失败：\n" + (proc.stdout or "") + (proc.stderr or "")
    )


@pytest.mark.golden
def test_tier1_vectors_are_hand_computed() -> None:
    """Tier-1 必须是手算基线，且至少 4 组（M0 门禁）。"""
    data = json.loads(_vectors_path().read_text(encoding="utf-8"))
    tier1 = [v for v in data["vectors"] if v["tier"] == 1]
    assert len(tier1) >= 4, f"Tier-1 手算基线不足 4 组，实为 {len(tier1)}"
    for v in tier1:
        assert v.get("source"), f"{v['id']} 缺少 source（必须能追溯到附件A 条款）"


@pytest.mark.golden
def test_core_version_matches_contract() -> None:
    """契约记录的 core_version 必须与包版本一致（版本锁）。"""
    import gwfund_core

    data = json.loads(_vectors_path().read_text(encoding="utf-8"))
    assert data["core_version"] == gwfund_core.__version__, (
        f"契约 core_version={data['core_version']} 与包版本 "
        f"{gwfund_core.__version__} 不一致——升版本后必须重新生成契约"
    )
