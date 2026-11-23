"""基金经理口径核心算法（M401–M420，纯函数）。

## 依据

附件A §13「基金经理口径（M401–M420）」。定位是**基金口径的下一层**：
把落在基金上的收益/盈亏，按**经理任职区间**归属到人。

## 一句话铁律（附件A §13 开头）

> **经理盈亏的「共管拆分」是不可消除的假设** —— 基金从不披露各经理贡献比例。
> **必须把它写进 UI，而不是藏进算法。**

本模块的 `attributed_pnl` 返回结果里带 `is_estimate=True` 与
`assumption` 字段，**下游若把它当真实归属就是错的**。

## 区间语义（关键，DEV-065）

``Regime`` 用**左闭右开** `[begin, end_exclusive)`。

F10 源数据是左闭右闭（`截止期` = 任职最后一天），
解析器已转成 `end_exclusive = end_date + 1 天`。
**本模块只接受左闭右开的区间**，不接受「源数据口径」。

## 三个必须遵守的口径

| # | 口径 | 原因 |
|---|---|---|
| 1 | **空窗期跳过，不记 0 收益** | 记 0 会把生涯 TWR 拉低 |
| 2 | **年化用有效管理天数 `D_eff`** | 用「首末任职跨度」会把空窗算进分母 |
| 3 | **A/C 份额去重**（保留份额大者） | 同一只基金的 A/C 是同一笔资产 |

## 用法

```python
from gwfund_core.manager import Regime, effective_days, career_twr, attributed_pnl

regimes = [Regime("m1", "000001", date(2024,1,1), None)]
d_eff = effective_days(regimes)
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

__all__ = [
    "Regime",
    "split_equally",
    "effective_days",
    "active_funds_on",
    "co_management_coefficient",
    "daily_manager_return",
    "career_twr",
    "annualized_career",
    "attributed_pnl",
    "AttributedPnl",
]


# --------------------------------------------------------------------------- #
# 区间段
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Regime:
    """一段任职区间，**左闭右开** ``[begin, end_exclusive)``。

    Attributes
    ----------
    manager_id : 经理唯一 ID（**不是姓名** —— 实测 118 个姓名对应多个 ID）
    fund_code : 基金代码
    begin : 起始日（含）
    end_exclusive : 结束日（**不含**）。``None`` = 仍在任
    """

    manager_id: str
    fund_code: str
    begin: date
    end_exclusive: date | None = None

    def __post_init__(self) -> None:
        if self.end_exclusive is not None and self.end_exclusive <= self.begin:
            raise ValueError(
                f"end_exclusive 必须晚于 begin（左闭右开）："
                f"{self.begin} -> {self.end_exclusive}"
            )

    def covers(self, d: date) -> bool:
        """``d`` 是否落在 ``[begin, end_exclusive)`` 内。"""
        if d < self.begin:
            return False
        return self.end_exclusive is None or d < self.end_exclusive

    @property
    def n_days(self) -> int | None:
        """区间天数（右开，故直接相减）。仍在任返回 None。"""
        if self.end_exclusive is None:
            return None
        return (self.end_exclusive - self.begin).days


# --------------------------------------------------------------------------- #
# D_eff：有效管理天数
# --------------------------------------------------------------------------- #


def effective_days(
    regimes: Iterable[Regime],
    *,
    as_of: date | None = None,
) -> int:
    """**有效管理天数** ``D_eff``：有在管基金的日历日之和，**不含空窗**。

    多个区间重叠时**按日去重**（同一天管两只基金仍只算 1 天）。

    Parameters
    ----------
    as_of : 仍在任区间的截止计算日（默认今天）。

    为什么不能用「首末任职跨度」：那样会把空窗计入分母，
    使短任期的经理年化被系统性拉高或拉低。
    """
    as_of = as_of or date.today()
    days: set[date] = set()
    for r in regimes:
        stop = r.end_exclusive or as_of
        d = r.begin
        while d < stop:
            days.add(d)
            d += timedelta(days=1)
    return len(days)


# --------------------------------------------------------------------------- #
# F(m,t)：某日某经理在管的基金集合
# --------------------------------------------------------------------------- #


def active_funds_on(
    regimes: Iterable[Regime],
    manager_id: str,
    day: date,
) -> set[str]:
    """``F(m,t)``：经理 ``m`` 在 ``day`` 在管的基金集合。"""
    return {r.fund_code for r in regimes if r.manager_id == manager_id and r.covers(day)}


def co_management_coefficient(
    regimes: Iterable[Regime],
    fund_code: str,
    day: date,
) -> Decimal:
    """``s_i,t = 1 / n_i,t``：共管拆分系数。

    ``n_i,t`` = 基金 ``i`` 在 ``day`` 的在任经理人数。

    **这是不可消除的假设** —— 基金不披露各经理贡献比例，只能等分。
    返回 ``Decimal`` 以保证下游求和可复现。
    """
    n = sum(1 for r in regimes if r.fund_code == fund_code and r.covers(day))
    if n == 0:
        raise ValueError(f"{fund_code} 在 {day} 无在任经理 —— 数据缺口或日期越界")
    return Decimal(1) / Decimal(n)


# --------------------------------------------------------------------------- #
# M4.1 经理日收益
# --------------------------------------------------------------------------- #


def daily_manager_return(
    regimes: Iterable[Regime],
    manager_id: str,
    day: date,
    fund_returns: Mapping[str, float],
    fund_aum: Mapping[str, Decimal] | None = None,
) -> float | None:
    """**M4.1** 经理 ``m`` 在第 ``day`` 的组合收益率。

    $$
    r^{mgr}_t = \\frac{\\sum_i AUM_{i,t} \\cdot r_{i,t}}{\\sum_i AUM_{i,t}}
    $$

    ``i`` 遍历 ``F(m,t)``（当日在管基金）。

    Parameters
    ----------
    fund_returns : ``{fund_code: 日收益率}``。**缺失即视为无数据**，不按 0 处理。
    fund_aum : ``{fund_code: 规模}``。``None`` 时退回**等权**（并非常规做法，
        仅在没有 AUM 时兜底）。

    Returns
    -------
    ``None`` —— 当日在管基金**全部**无收益数据时（缺失即 ``None``，不编 0）。

    口径：净值费后（调用方负责传入费后收益）。
    """
    funds = active_funds_on(regimes, manager_id, day)
    if not funds:
        return None

    # 只用「有收益数据」且在管的基金；缺失基金不进分子也不进分母
    usable = [(f, fund_returns[f]) for f in funds if f in fund_returns]
    if not usable:
        return None

    if fund_aum is None:
        # 兜底：等权
        return sum(r for _, r in usable) / len(usable)

    num = Decimal(0)
    den = Decimal(0)
    for f, r in usable:
        w = fund_aum.get(f)
        if w is None or w <= 0:
            continue
        num += w * Decimal(str(r))
        den += w
    if den == 0:
        return None
    return float(num / den)


# --------------------------------------------------------------------------- #
# M4.2 生涯 TWR
# --------------------------------------------------------------------------- #


def career_twr(
    daily_returns: Mapping[date, float],
    *,
    active_days: Iterable[date] | None = None,
) -> float:
    """**M4.2** 生涯时间加权收益率（链式）。

    $$
    TWR = \\prod_{t} (1 + r_t) - 1
    $$

    **关键：只在「有在管基金」的交易日链式相乘，空窗期跳过、不记 0。**

    记 0 的后果：空窗期把乘积拉成 1，看似「无变化」，实则把空窗当成
    「持币不动」——那不是经理的业绩。

    Parameters
    ----------
    daily_returns : ``{date: 日收益率}``
    active_days : 有效管理日集合。``None`` 时用 ``daily_returns`` 的全部日期。
    """
    days = set(daily_returns) if active_days is None else set(active_days)
    prod = 1.0
    for d in sorted(days):
        r = daily_returns.get(d)
        if r is None:
            continue  # 无数据 → 跳过（不记 0）
        prod *= 1.0 + r
    return prod - 1.0


def annualized_career(
    twr: float,
    d_eff: int,
    *,
    min_days: int = 90,
) -> float | None:
    """生涯年化。**分母必须是 ``D_eff``，不是首末任职跨度。**

    Parameters
    ----------
    twr : 生涯 TWR（小数，如 0.35 表示 +35%）
    d_eff : 有效管理天数（``effective_days()`` 的结果）

    Returns
    -------
    ``None`` —— 有效天数不足 ``min_days`` 时（短任期年化无统计意义，
    强行年化会给出荒谬数字）。
    """
    if d_eff < min_days:
        return None
    return (1.0 + twr) ** (365.0 / d_eff) - 1.0


# --------------------------------------------------------------------------- #
# 等分金额（保证求和精确）
# --------------------------------------------------------------------------- #

_CENT = Decimal("0.01")


def split_equally(amount: Decimal, n: int) -> list[Decimal]:
    """把 ``amount`` 等分成 ``n`` 份，**保证求和 == amount**（验收判据 2）。

    ## 为什么不能简单地乘以 1/n

    ``Decimal(1) / Decimal(3)`` 只有 28 位有效数字，
    乘回去得 ``0.9999999999999999999999999999``，**不等于 1**。

    实测：3 位经理共管时，``3 × (1/3) = 0.999...9``。
    这会让「各经理之和 = 基金盈亏」的验收判据失败。

    ## 做法

    先按分（0.01 元）四舍五入等分，**余数全部给第一位**。
    这样：
    - 求和**精确等于**原额
    - 单份误差 ≤ 1 分
    - 结果可复现（不依赖字典顺序）

    例：100.00 分给 3 人 → ``[33.34, 33.33, 33.33]``，和 = 100.00。
    """
    if n <= 0:
        raise ValueError(f"n 必须为正整数，收到 {n}")
    if n == 1:
        return [amount]
    each = (amount / n).quantize(_CENT, rounding="ROUND_HALF_EVEN")
    parts = [each] * n
    # 余数给第一位，保证 sum == amount
    parts[0] = amount - each * (n - 1)
    return parts


# --------------------------------------------------------------------------- #
# M5.1 经理归属盈亏
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AttributedPnl:
    """经理归属盈亏结果。

    **``is_estimate`` 恒为 True** —— 共管拆分是假设，不是事实。
    """

    manager_id: str
    pnl: Decimal
    """归属盈亏（元）"""
    n_days: int
    n_funds: int
    is_estimate: bool = True
    assumption: str = (
        "按当日在任经理等分归属；基金不披露各经理贡献比例，"
        "此数为**估计**，不等于该经理的实际贡献"
    )
    excluded_days: int = field(default=0)
    """因无在任经理或无盈亏数据而跳过的天数"""


def attributed_pnl(
    regimes: Iterable[Regime],
    manager_id: str,
    fund_pnl: Mapping[tuple[str, date], Decimal],
) -> AttributedPnl:
    """**M5.1** 经理生涯归属盈亏。

    $$
    P^{mgr}(m) = \\sum_t \\sum_{i \\in F(m,t)} \\frac{P_{i,t}}{n_{i,t}}
    $$

    ``P_{i,t}`` = 基金 i 第 t 日的投资者盈亏日增量（§4.1 差分）。

    **本函数不做任何「聪明」的加权** —— 就是等分。
    理由是：任何其他权重都是编的。

    Returns
    -------
    :class:`AttributedPnl`，含 ``is_estimate=True`` 与 ``assumption`` 说明。
    """
    regs = list(regimes)
    pnl = Decimal(0)
    n_days = 0
    excluded = 0
    funds_touched: set[str] = set()

    # 收集该经理所有涉及的日期
    all_days: set[date] = set()
    for (f, d) in fund_pnl:
        if any(r.manager_id == manager_id and r.fund_code == f and r.covers(d) for r in regs):
            all_days.add(d)

    for d in sorted(all_days):
        funds_today = active_funds_on(regs, manager_id, d)
        if not funds_today:
            excluded += 1
            continue
        day_sum = Decimal(0)
        day_has = False
        for f in sorted(funds_today):
            v = fund_pnl.get((f, d))
            if v is None:
                continue
            # 用 split_equally 而非乘 1/n —— 保证各经理之和精确等于基金盈亏
            managers_today = sorted(
                r.manager_id for r in regs if r.fund_code == f and r.covers(d)
            )
            n = len(managers_today)
            idx = managers_today.index(manager_id)
            day_sum += split_equally(Decimal(v), n)[idx]
            day_has = True
            funds_touched.add(f)
        if day_has:
            pnl += day_sum
            n_days += 1
        else:
            excluded += 1

    return AttributedPnl(
        manager_id=manager_id,
        pnl=pnl,
        n_days=n_days,
        n_funds=len(funds_touched),
        excluded_days=excluded,
    )
