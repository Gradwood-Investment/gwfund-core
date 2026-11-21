"""pytest 引导：把 ``src`` 加入 sys.path，使测试在未安装时也能跑。

这保证「``git clone`` 后 ``pytest`` 直接可运行」（M0 W1 判据：``make test`` 可跑）。
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
