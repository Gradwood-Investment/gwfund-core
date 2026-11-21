"""架构测试：core 必须无 I/O、零第三方依赖（SOW §7.3 C10、附件A §10 E1/E3）。

**B01 修复记录（2026-11-21，依《裁定书二》P0-1）**

原实现有两个缺陷，导致守卫形同虚设：

1. ``test_only_stdlib_imports`` 只遍历 ``ast.ImportFrom``，**不看 ``ast.Import``**——
   而 ``import os`` 正是 ``Import`` 形式，于是「注入 ``import os; os.environ.get()``
   后仍 5 passed」；
2. 禁用名单缺 ``os / pathlib / sys``。

修复方式（三条，缺一不可）：

* 扫描逻辑抽成**纯函数** :func:`scan_violations`，统一遍历 ``Import`` + ``ImportFrom``；
* 禁用名单补 ``os / pathlib / sys / subprocess / requests / httpx / duckdb / sqlalchemy / tqdm``；
* **新增负控测试** :func:`test_negative_control_guard_catches_import_os`——
  在临时目录里造一个含 ``import os`` 的包，断言守卫**必须报违规**。
  负控是守卫的「元测试」：守卫失效时，负控先红。

> 这条纪律来自《裁定书二》：「守卫失效比功能缺失更严重——
> 它会让后面所有验收都变成走过场。」
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "gwfund_core"

# 禁止导入的模块（B01 修复：补入 os / pathlib / sys）
FORBIDDEN_MODULES = {
    # —— 文件系统与环境（本次补入）——
    "os",
    "pathlib",
    "sys",
    # —— 网络 ——
    "requests",
    "httpx",
    "urllib",
    "urllib3",
    "aiohttp",
    "socket",
    "http",
    "ftplib",
    "smtplib",
    "telnetlib",
    # —— 进程与并发 ——
    "subprocess",
    "multiprocessing",
    "threading",
    "asyncio",
    # —— 存储 ——
    "sqlite3",
    "duckdb",
    "sqlalchemy",
    "psycopg2",
    "pymysql",
    # —— 数值/数据三方库 ——
    "pandas",
    "numpy",
    "polars",
    "pyarrow",
    # —— 云与序列化 ——
    "boto3",
    "shutil",
    "tempfile",
    "pickle",
    "shelve",
    "importlib",
    "ctypes",
    # —— 三方向导（禁止 core 直接出进度条 I/O）——
    "tqdm",
}

# 允许的标准库（零依赖承诺的白名单）
ALLOWED_STDLIB = {
    "__future__",
    "dataclasses",
    "decimal",
    "datetime",
    "enum",
    "typing",
    "collections",
    "collections.abc",
    "math",
    "calendar",
    "bisect",
    "re",
    "functools",
    "itertools",
    "statistics",
    "warnings",
    "operator",
    "abc",
    "numbers",
}

FORBIDDEN_CALLS = {
    "open",
    "exec",
    "eval",
    "__import__",
    "compile",
    "input",
}


def scan_violations(root: Path) -> list[str]:
    """扫描目录下所有 ``.py`` 的 import 违规，返回违规清单（空 = 通过）。

    统一遍历 ``ast.Import`` 与 ``ast.ImportFrom`` 两类节点（B01 的核心修复）。
    相对导入（``from . import x``）视为包内自身，跳过。
    """
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # 语法错本身就是问题
            violations.append(f"{path.name}: 语法错误 {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in FORBIDDEN_MODULES:
                        violations.append(
                            f"{path.name}:{node.lineno} import {alias.name}（禁用）"
                        )
                    elif root_mod not in ALLOWED_STDLIB and root_mod != "gwfund_core":
                        violations.append(
                            f"{path.name}:{node.lineno} import {alias.name}（非标准库，违反零依赖）"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # 相对导入 = 包内自身
                if node.module is None:
                    continue
                root_mod = node.module.split(".")[0]
                if root_mod in FORBIDDEN_MODULES:
                    violations.append(
                        f"{path.name}:{node.lineno} from {node.module} import ...（禁用）"
                    )
                elif root_mod not in ALLOWED_STDLIB and root_mod != "gwfund_core":
                    violations.append(
                        f"{path.name}:{node.lineno} from {node.module} import ...（非标准库）"
                    )
    return violations


def scan_forbidden_calls(root: Path) -> list[str]:
    """扫描对 ``open/exec/eval`` 等内建函数的调用。"""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    violations.append(f"{path.name}:{node.lineno} {node.func.id}()")
    return violations


@pytest.mark.architecture
def test_guard_self_check_finds_package() -> None:
    """守卫自身前提：必须真的扫到了源文件（防止「扫了空目录所以 0 违规」）。"""
    files = sorted(PACKAGE_ROOT.rglob("*.py"))
    assert len(files) >= 8, f"扫描到 {len(files)} 个文件，过少——守卫可能指错了目录"
    assert any(p.name == "identities.py" for p in files)


@pytest.mark.architecture
def test_no_forbidden_imports() -> None:
    """包内不得 import 任何 I/O / 数值三方库 / os·pathlib·sys。"""
    violations = scan_violations(PACKAGE_ROOT)
    assert not violations, "core 出现禁止的 import：\n" + "\n".join(violations)


@pytest.mark.architecture
def test_no_forbidden_calls() -> None:
    """包内不得调用 open / exec / eval 等能触达外部世界的函数。"""
    violations = scan_forbidden_calls(PACKAGE_ROOT)
    assert not violations, "core 出现禁止的调用：\n" + "\n".join(violations)


@pytest.mark.architecture
def test_negative_control_guard_catches_import_os() -> None:
    """**负控（B01）**：注入 ``import os`` 后，守卫必须报违规。

    这是守卫的「元测试」。若把 :func:`scan_violations` 的实现改坏（例如又只遍历
    ``ImportFrom``），本测试会先红——而不是等到生产代码里混入 ``os.environ`` 才发现。

    衡鉴原实验：注入 ``import os; os.environ.get()`` 后原守卫仍 5 passed。
    修复后本负控必须让守卫报出违规。
    """
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "fake_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "bad_import.py").write_text(
            "import os\n\n\ndef leak() -> str | None:\n    return os.environ.get('SECRET')\n",
            encoding="utf-8",
        )
        violations = scan_violations(pkg)
        assert violations, "负控失败：注入 `import os` 后守卫未报违规——守卫已失效！"
        assert any("os" in v for v in violations), f"未命中 os：{violations}"


@pytest.mark.architecture
@pytest.mark.parametrize(
    "bad_source",
    [
        "import sys\n",
        "import pathlib\n",
        "from pathlib import Path\n",
        "import requests\n",
        "from duckdb import connect\n",
        "import subprocess\n",
        "import tqdm\n",
    ],
    ids=["sys", "pathlib", "from_pathlib", "requests", "duckdb", "subprocess", "tqdm"],
)
def test_negative_control_catches_each_forbidden_module(bad_source: str) -> None:
    """**负控（B01）扩展**：禁用名单里每个模块都必须能被单独抓到。"""
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "fake_pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_text(bad_source, encoding="utf-8")
        assert scan_violations(pkg), f"负控失败：`{bad_source.strip()}` 未被抓到"


@pytest.mark.architecture
def test_negative_control_allows_clean_module() -> None:
    """**负控的对照**：合法模块不得报违规（防止守卫「一律报错」的假阳性）。"""
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "fake_pkg"
        pkg.mkdir()
        (pkg / "clean.py").write_text(
            "from __future__ import annotations\n"
            "from decimal import Decimal\n"
            "from . import sibling\n"
            "\n"
            "def f(x: int) -> int:\n"
            "    return x + 1\n",
            encoding="utf-8",
        )
        (pkg / "sibling.py").write_text("VALUE = 1\n", encoding="utf-8")
        assert scan_violations(pkg) == []


@pytest.mark.architecture
def test_no_file_size_violation() -> None:
    """NFR-09：单文件 ≤ 800 行。"""
    oversized = [
        f"{p.name}: {len(p.read_text(encoding='utf-8').splitlines())} 行"
        for p in sorted(PACKAGE_ROOT.rglob("*.py"))
        if len(p.read_text(encoding="utf-8").splitlines()) > 800
    ]
    assert not oversized, "以下文件超过 800 行：\n" + "\n".join(oversized)


@pytest.mark.architecture
def test_version_is_semver() -> None:
    """SemVer 版本号（SOW §11.2.1）。"""
    import re as _re

    import gwfund_core

    # 允许 PEP 440 预发布后缀（rc1 / a1 / b2 / .dev1），如 1.0.0rc1
    assert _re.fullmatch(
        r"\d+\.\d+\.\d+(?:[-.]?(?:a|b|rc|dev|post)\d+)?", gwfund_core.__version__
    ), f"__version__ 不是 SemVer：{gwfund_core.__version__}"
