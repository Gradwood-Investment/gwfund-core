"""聚合与排名类指标：M209 / M303 / M308 / M309 / M311。

这些指标**必须**在 core 里实现，不得由各端各写一遍（附件A MC-01）。
它们都是纯函数：输入已算好的明细，输出聚合结果。

对应《附件A》§5.3 指标字典：

======  ==========================  ==========================================
ID      指标                        要点
======  ==========================  ==========================================
M209    同类排名分位                同类池内百分位；**并列要按平均秩**处理
M303    规模增长来源占比            ``NetSub ÷ ΔAUM``；ΔAUM≈0 时必须返回 None
M308    公司层面聚合                去重后加总；**A/C 份额合并开关**会改变结果
M309    经理层面聚合                含离任产品——**否则是幸存者偏差**
M311    持有人结构                  机构/个人/内部三档，和与 100% 的偏离要报出
======  ==========================  ==========================================
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

_CENT = Decimal("0.01")


def _d(v: Decimal | int | float | str) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


# --------------------------------------------------------------------------
# M209 同类排名分位
# --------------------------------------------------------------------------


def percentile_rank(
    value: float,
    peers: Sequence[float],
    *,
    higher_is_better: bool = True,
) -> float | None:
    """同类排名分位 M209（百分位，0–100）。

    用**平均秩**处理并列：并列样本共享同一分位，避免「同样收益排名不同」。

    Args:
        value: 本基金指标值。
        peers: 同类池（可含本基金；实现会按值判定，不依赖 include_self）。
        higher_is_better: 指标越大越好（如收益率）；``False`` 用于回撤、费率等。

    Returns:
        分位（0–100）。``higher_is_better=True`` 时 100 = 同类最好。
        同类池为空返回 ``None``（不得返回 0——0 表示「最差」）。
    """
    if not peers:
        return None
    n = len(peers)
    less = sum(1 for p in peers if p < value)
    equal = sum(1 for p in peers if p == value)
    # 平均秩（1-based 转 0-based 分位）
    avg_rank0 = less + (equal - 1) / 2.0
    pct = avg_rank0 / (n - 1) * 100.0 if n > 1 else 50.0
    return pct if higher_is_better else 100.0 - pct


# --------------------------------------------------------------------------
# M303 规模增长来源占比
# --------------------------------------------------------------------------


def net_sub_share_of_growth(
    delta_aum_wan: Decimal | int | float | str,
    net_sub_wan: Decimal | int | float | str,
    *,
    min_abs_growth_wan: Decimal = Decimal("1"),
) -> float | None:
    """规模增长来源占比 M303：``NetSub ÷ ΔAUM``。

    回答「规模涨了 40%，多少是客户买的、多少是市场涨的」。

    Args:
        delta_aum_wan: 区间规模变化 ``ΔAUM``（万元）。
        net_sub_wan: 区间净申购 ``NetSub``（万元）。
        min_abs_growth_wan: ``|ΔAUM|`` 小于该值时返回 ``None``——
            分母接近 0 时占比会爆到几百倍，无解释意义。

    Returns:
        占比（小数，可 >1 或 <0）。``ΔAUM`` 过小时返回 ``None``。

    Note:
        占比 > 1 表示「净申购超过规模增长」，意味着投资收益为负；
        占比 < 0 表示「规模在缩，但客户仍在净申购」——两种都能解读，
        **不要**为了让数字好看而截断到 [0,1]。
    """
    da = _d(delta_aum_wan)
    if abs(da) < min_abs_growth_wan:
        return None
    return float(_d(net_sub_wan) / da)


# --------------------------------------------------------------------------
# M308 公司层面聚合
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FundFigure:
    """单只基金在某区间的账面数字（聚合输入）。"""

    fund_code: str
    p_inv_wan: Decimal | int | float | str
    fee_wan: Decimal | int | float | str
    aum_wan: Decimal | int | float | str = 0
    manager: str | None = None
    share_class: str | None = None
    is_active: bool = True
    """``False`` = 已清盘/已转型（M309 必须包含，否则幸存者偏差）。"""


def aggregate_company(
    figures: Iterable[FundFigure],
    *,
    merge_share_classes: bool = False,
) -> dict[str, object]:
    """公司层面聚合 M308。

    Args:
        figures: 该公司旗下基金的明细。
        merge_share_classes: **A/C 合并开关**（决策 C7「口径可配置」）。
            ``True`` 时按「去掉份额类别后缀的基名」去重，只保留 AUM 最大的一档，
            避免同一只基金被 A/C 重复计入两次。

    Returns:
        ``{"n_funds", "n_after_merge", "p_inv_wan", "fee_wan", "aum_wan",
        "fee_per_yuan_pnl", "merged"]``。

    Warning:
        开关会**改变结果**，UI 必须显示当前口径并提示「切换后规模与盈亏会变」。
    """
    rows = list(figures)
    merged = False
    if merge_share_classes:
        best: dict[str, FundFigure] = {}
        for f in rows:
            key = f.fund_code.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ") or f.fund_code
            cur = best.get(key)
            if cur is None or _d(f.aum_wan) > _d(cur.aum_wan):
                best[key] = f
        if len(best) != len(rows):
            merged = True
        rows = list(best.values())

    p_inv = sum((_d(f.p_inv_wan) for f in rows), Decimal(0))
    fee = sum((_d(f.fee_wan) for f in rows), Decimal(0))
    aum = sum((_d(f.aum_wan) for f in rows), Decimal(0))
    return {
        "n_funds": len(rows),
        "n_after_merge": len(rows),
        "p_inv_wan": p_inv.quantize(_CENT),
        "fee_wan": fee.quantize(_CENT),
        "aum_wan": aum.quantize(_CENT),
        "fee_per_yuan_pnl": (float(p_inv / fee) if fee else None),
        "merged": merged,
    }


# --------------------------------------------------------------------------
# M309 经理层面聚合
# --------------------------------------------------------------------------


def aggregate_manager(
    figures: Iterable[FundFigure],
    *,
    include_departed: bool = True,
) -> dict[str, object]:
    """经理层面聚合 M309。

    Args:
        include_departed: **默认必须为 True**。只统计在管产品会产生
            **幸存者偏差**——离任前管的产品做得再差，也会因为「现在不管了」被剔除。

    Returns:
        ``{"n_funds", "n_active", "n_departed", "p_inv_wan", "fee_wan",
        "n_excluded_due_to_survivorship"}``。
    """
    rows = list(figures)
    active = [f for f in rows if f.is_active]
    departed = [f for f in rows if not f.is_active]
    used = rows if include_departed else active

    p_inv = sum((_d(f.p_inv_wan) for f in used), Decimal(0))
    fee = sum((_d(f.fee_wan) for f in used), Decimal(0))
    return {
        "n_funds": len(used),
        "n_active": len(active),
        "n_departed": len(departed),
        "p_inv_wan": p_inv.quantize(_CENT),
        "fee_wan": fee.quantize(_CENT),
        "n_excluded_due_to_survivorship": 0 if include_departed else len(departed),
    }


# --------------------------------------------------------------------------
# M311 持有人结构
# --------------------------------------------------------------------------


def holder_structure(
    inst_pct: float | None,
    retail_pct: float | None,
    internal_pct: float | None,
    *,
    tolerance_pct: float = 0.5,
) -> dict[str, object]:
    """持有人结构 M311 + 勾稽校验。

    Args:
        inst_pct / retail_pct / internal_pct: 机构 / 个人 / 内部持有占比（百分数）。
        tolerance_pct: 三档之和与 100% 的允许偏离（百分点）。

    Returns:
        ``{"inst", "retail", "internal", "total_pct", "deviation_pct",
        "passed", "reason"}``。

    Note:
        三档之和应 ≈ 100%。偏离超容差通常意味着：① 有第四类持有人未取到；
        ② 某一档口径是「占净值比」而非「占份额比」。
        **不得把缺失档按 0 补齐**——缺失时 ``total_pct`` 为 ``None``。
    """
    vals = [inst_pct, retail_pct, internal_pct]
    present = [v for v in vals if v is not None]
    missing = sum(1 for v in vals if v is None)
    if not present:
        return {
            "inst": None, "retail": None, "internal": None,
            "total_pct": None, "deviation_pct": None, "passed": None,
            "reason": "三档全部缺失",
        }
    total = sum(present)
    if missing:
        return {
            "inst": inst_pct, "retail": retail_pct, "internal": internal_pct,
            "total_pct": None, "deviation_pct": None, "passed": None,
            "reason": f"{missing} 档缺失，不得按 0 补齐后判勾稽",
        }
    dev = abs(total - 100.0)
    return {
        "inst": inst_pct, "retail": retail_pct, "internal": internal_pct,
        "total_pct": total, "deviation_pct": dev,
        "passed": dev <= tolerance_pct,
        "reason": None if dev <= tolerance_pct else f"三档合计 {total:.2f}%，偏离 {dev:.2f} pct",
    }
