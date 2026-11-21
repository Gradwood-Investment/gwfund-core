"""架构测试：core 必须无 I/O（SOW §7.3 C10、附件A §10 E1/E3）。

这是四端一致性机制的地基：只要 ``gwfund-core`` 不能读文件、不能联网，
四端就只能通过调用同一份纯函数来拿数字，**没有任何端能"顺手"实现一遍公式**。

本测试用 AST 静态扫描实现，不依赖运行时行为——
这样即使某个分支永远不执行，违规 import 也会被抓到。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "gwfund_core"

# 禁止导入的模块：任何能触达外部世界的东西
FORBIDDEN_MODULES = {
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
    "subprocess",
    "multiprocessing",
    "threading",
    "asyncio",
    "sqlite3",
    "duckdb",
    "pandas",
    "numpy",
    "polars",
    "pyarrow",
    "psycopg2",
    "pymysql",
    "sqlalchemy",
    "boto3",
    "shutil",
    "tempfile",
    "pickle",
    "shelve",
    "importlib",
    "ctypes",
}

# 禁止调用的内建函数：能绕过 import 检查的动作
FORBIDDEN_CALLS = {
    "open",
    "exec",
    "eval",
    "__import__",
    "compile",
    "input",
}

# 允许的 pathlib 用法白名单（仅类型注解，不做 I/O）
ALLOWED_FROM_IMPORTS = {"__future__", "dataclasses", "decimal", "datetime", "enum", "typing",
                        "collections", "collections.abc", "math", "calendar", "bisect", "re",
                        "functools", "itertools", "statistics", "warnings", "operator"}


def _iter_py_files() -> list[Path]:
    files = sorted(PACKAGE_ROOT.rglob("*.py"))
    assert files, f"未找到任何源文件：{PACKAGE_ROOT}"
    return files


@pytest.mark.architecture
def test_no_forbidden_imports() -> None:
    """包内不得 import 任何 I/O / 数值三方库。"""
    violations: list[str] = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in FORBIDDEN_MODULES:
                        violations.append(f"{path.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                root = node.module.split(".")[0]
                if root in FORBIDDEN_MODULES:
                    violations.append(f"{path.name}:{node.lineno} from {node.module} import ...")
    assert not violations, "core 出现禁止的 import：\n" + "\n".join(violations)


@pytest.mark.architecture
def test_no_forbidden_calls() -> None:
    """包内不得调用 open / exec / eval 等能触达外部世界的函数。"""
    violations: list[str] = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    violations.append(f"{path.name}:{node.lineno} {node.func.id}()")
    assert not violations, "core 出现禁止的调用：\n" + "\n".join(violations)


@pytest.mark.architecture
def test_only_stdlib_imports() -> None:
    """除标准库与自身包外，不得 import 其他东西（零运行时依赖承诺）。"""
    violations: list[str] = []
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue  # 相对导入 = 包内自身
                if node.module is None:
                    continue
                root = node.module.split(".")[0]
                if root == "gwfund_core":
                    continue
                if root not in ALLOWED_FROM_IMPORTS:
                    violations.append(f"{path.name}:{node.lineno} from {node.module}")
    assert not violations, (
        "core 引入了非标准库依赖（违反零依赖承诺）：\n" + "\n".join(violations)
    )


@pytest.mark.architecture
def test_no_file_size_violation() -> None:
    """NFR-09：单文件 ≤ 800 行。"""
    oversized = [
        f"{p.name}: {len(p.read_text(encoding='utf-8').splitlines())} 行"
        for p in _iter_py_files()
        if len(p.read_text(encoding="utf-8").splitlines()) > 800
    ]
    assert not oversized, "以下文件超过 800 行：\n" + "\n".join(oversized)


@pytest.mark.architecture
def test_version_is_semver() -> None:
    """SemVer 版本号（SOW §11.2.1）。"""
    import re

    import gwfund_core

    assert re.fullmatch(r"\d+\.\d+\.\d+", gwfund_core.__version__), (
        f"__version__ 不是 SemVer：{gwfund_core.__version__}"
    )
