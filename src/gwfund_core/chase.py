"""追高资金分析（Chasing Money）。

对应《附件A 算法引擎计算规范》§7：

* §7.1 式(9.1)  追高资金金额 ``Chase(θ) = Σ_{π_t ≥ θ} max(0, NetSub_t)``
* §7.1 式(9.2)  净值历史分位 ``π_t``（**必须用自成立以来全部历史净值**）
* §7.1 式(9.3)  追高资金后续盈亏（20 日 / 60 日 / 持有至今）
* §7.1 式(9.4)  追高资金占比
* §7.1 式(9.5)  追高资金亏损金额（只累加亏损部分）
* §7.2          手算样例（000001：观测期内持续净赎回 → ``Chase = 0``）
* §7.3          实现要求（分位口径 / 净申购符号 / 累计净值 / 覆盖率）
* §11 V10       必须能输出「无追高资金」结论，**不得强行拟合**

产品含义：``Chase = 0`` 本身就是有价值的结论。直觉上「老基金高位吸金」很常见，
但数据说 000001 不是——系统必须能输出「无追高资金」，而不是硬找一个数字。
"""

from __future__ import annotations

from bisect import bisect_right, insort
from collections.abc import Sequence

DEFAULT_THETA = 0.80
"""追高分位阈值 θ 默认 80%（附件A §7.1，可配）。"""


# --------------------------------------------------------------------------
# 式(9.2) 净值历史分位
# --------------------------------------------------------------------------


def percentile_series(navs: Sequence[float]) -> list[float]:
    """逐期计算净值在该基金**自成立以来全部历史**中的分位 ``π_t``。

    附件A §7.1 式(9.2)：

    ``π_t = #{s ≤ t : N_s ≤ N_t} / (t − t_inception + 1) ∈ [0, 1]``

    Args:
        navs: 自成立以来的净值序列（升序），**必须是累计净值或复权净值**——
            用单位净值会因分红除息造成假下跌，系统性高估追高比例
            （附件A §7.3「净值口径」）。

    Returns:
        与 ``navs`` 等长的分位序列，取值 ``[0, 1]``；首期为 ``1.0``
        （单点即自身历史最高分位）。

    Note:
        实现用有序插入 + 二分统计，复杂度 O(n²) 但常数极小；
        6,000 个日频点约 0.3 秒。**禁止**改用「近 3 年」作分母——
        那会系统性高估追高比例（附件A §7.3）。
    """
    out: list[float] = []
    sorted_hist: list[float] = []
    for i, n in enumerate(navs):
        rank = bisect_right(sorted_hist, n)  # 已有历史中 <= n 的个数
        out.append((rank + 1) / (i + 1))  # +1 计入当期自身
        insort(sorted_hist, n)
    return out


# --------------------------------------------------------------------------
# 式(9.1)(9.4)(9.5) 追高金额 / 占比 / 亏损
# --------------------------------------------------------------------------


def chase_amount(
    net_subs: Sequence[float],
    percentiles: Sequence[float],
    *,
    theta: float = DEFAULT_THETA,
) -> float:
    """追高资金金额 ``Chase(θ)``，附件A §7.1 式(9.1)。

    ``Chase(θ) = Σ_{t: π_t ≥ θ} max(0, NetSub_t)``

    Args:
        net_subs: 各期净申购金额（元）。**只用 > 0 的期**——
            净赎回期不算追高，即使在高位（附件A §7.3「净申购符号」）。
        percentiles: 与 ``net_subs`` 对齐的净值分位序列。
        theta: 分位阈值，默认 80%。

    Returns:
        追高资金金额（元，float）。
    """
    if len(net_subs) != len(percentiles):
        raise ValueError(
            f"net_subs 与 percentiles 长度不一致：{len(net_subs)} vs {len(percentiles)}"
        )
    total = 0.0
    for ns, p in zip(net_subs, percentiles):
        if p >= theta and ns > 0:
            total += ns
    return total


def total_inflow(net_subs: Sequence[float]) -> float:
    """区间总申购金额 ``Σ max(0, NetSub_t)``（式 9.4 的分母）。"""
    return sum(ns for ns in net_subs if ns > 0)


def chase_ratio(
    chase: float,
    inflow: float,
) -> float | None:
    """追高资金占比 ``Ratio_chase``，附件A §7.1 式(9.4)。

    ``Ratio_chase = Chase(θ) / Σ_t max(0, NetSub_t)``

    Returns:
        占比（0–1）；无任何申购时返回 ``None``（分母为 0，**不得返回 0**）。
    """
    if inflow == 0:
        return None
    return chase / inflow


def chase_future_pnl(
    net_subs: Sequence[float],
    percentiles: Sequence[float],
    navs_now: Sequence[float],
    navs_future: Sequence[float],
    *,
    theta: float = DEFAULT_THETA,
) -> float:
    """追高资金后续盈亏（式 9.3），附件A §7.1。

    ``P_chase(h) = Σ_{t: π_t ≥ θ, NetSub_t > 0} NetSub_t · (N_{t+h}/N_t − 1)``

    Args:
        net_subs: 各期净申购金额（元），与 ``percentiles`` 对齐。
        percentiles: 各期净值分位。
        navs_now: 各期对应净值 ``N_t``。
        navs_future: 各期对应未来净值 ``N_{t+h}``；**视窗末尚不存在的
            未来日应传 ``None``**（该期不计入，不得用当前值顶替）。

    Returns:
        追高资金的后续盈亏（元）；正值表示追高资金浮盈。
    """
    for name, seq in (
        ("net_subs", net_subs),
        ("percentiles", percentiles),
        ("navs_now", navs_now),
        ("navs_future", navs_future),
    ):
        length = len(seq)
        if length != len(net_subs):
            raise ValueError(f"{name} 长度不一致：{length} vs {len(net_subs)}")
    total = 0.0
    for ns, p, n0, n1 in zip(net_subs, percentiles, navs_now, navs_future):
        if n1 is None or n0 is None or n0 <= 0:
            continue
        if p >= theta and ns > 0:
            total += ns * (n1 / n0 - 1.0)
    return total


def chase_loss(
    net_subs: Sequence[float],
    percentiles: Sequence[float],
    navs_entry: Sequence[float],
    nav_now: float,
    *,
    theta: float = DEFAULT_THETA,
) -> float:
    """追高资金亏损金额 ``Loss_chase``，附件A §7.1 式(9.5)。

    ``Loss_chase = −Σ_{t: π_t ≥ θ, NetSub_t > 0} NetSub_t · min(0, N_now/N_t − 1)``

    只累加**亏损**部分；盈利的追高资金不计入「亏损金额」。
    返回值**为正数**表示亏损的家数（取负号前的毛亏额度）。

    Returns:
        亏损金额（元，≥ 0）。
    """
    if len(net_subs) != len(percentiles) or len(net_subs) != len(navs_entry):
        raise ValueError("三个序列长度必须一致")
    if nav_now is None or nav_now <= 0:
        raise ValueError(f"当前净值必须为正：{nav_now}")
    total = 0.0
    for ns, p, n0 in zip(net_subs, percentiles, navs_entry):
        if n0 is None or n0 <= 0:
            continue
        if p >= theta and ns > 0:
            change = nav_now / n0 - 1.0
            if change < 0:
                total += -ns * change
    return total


# --------------------------------------------------------------------------
# 汇总（附件A §7.3：必须同时给占比与亏损金额两个数）
# --------------------------------------------------------------------------


def chase_summary(
    net_subs: Sequence[float],
    navs_history: Sequence[float],
    *,
    theta: float = DEFAULT_THETA,
) -> dict[str, object]:
    """追高资金汇总，附件A §7.1 + §7.3「呈现」要求。

    值得注意：必须**同时**给「追高资金占比」与「追高资金亏损金额」两个数，
    **只给一个视为不合格**。

    Args:
        net_subs: 各期净申购金额（元）。
        navs_history: **自成立以来**的净值序列；``net_subs`` 对齐到其末尾。
        theta: 分位阈值。

    Returns:
        ``{"chase_amount", "total_inflow", "chase_ratio", "entry_navs",
        "percentiles", "theta", "has_chasing", "coverage"}``。

        ``has_chasing`` 为 ``False`` 表示观测期内无追高资金——这是**合法且
        有价值的结论**（附件A §7.2 / V10）。
    """
    if not net_subs:
        return {
            "chase_amount": 0.0,
            "total_inflow": 0.0,
            "chase_ratio": None,
            "entry_navs": [],
            "percentiles": [],
            "theta": theta,
            "has_chasing": False,
            "coverage": 0.0,
        }
    pct_all = percentile_series(navs_history)
    k = len(net_subs)
    if k > len(pct_all):
        raise ValueError(
            f"净申购期数 {k} 超过净值历史长度 {len(pct_all)}，无法对齐"
        )
    pcts = pct_all[-k:]
    entries = list(navs_history)[-k:]
    amount = chase_amount(net_subs, pcts, theta=theta)
    inflow = total_inflow(net_subs)
    valid = sum(1 for ns in net_subs if ns is not None)
    return {
        "chase_amount": amount,
        "total_inflow": inflow,
        "chase_ratio": chase_ratio(amount, inflow),
        "entry_navs": entries,
        "percentiles": pcts,
        "theta": theta,
        "has_chasing": amount > 0,
        "coverage": valid / k if k else 0.0,
    }


# --------------------------------------------------------------------------
# 式(9.6) 监管人数口径与份额口径的方向校验
# --------------------------------------------------------------------------


def estimate_profit_investor_ratio(
    sub_shares: Sequence[float],
    sub_navs: Sequence[float],
    nav_now: float,
) -> float | None:
    """份额口径的「盈利投资者占比」估算（式 8.9），用于校验监管 M312 的方向。

    ``π̂_est = (Σ_k 1[N_now ≥ N_pk] · Sub^sh_k − Σ_j 1[N_now < N_qj] · Red^sh_j) / Σ_k Sub^sh_k``

    Note:
        这是**方向校验工具**，不是 M312 的替代品。两者口径不同
        （等权 vs 份额加权、是否剔 7 日内），差异 > 10 pct 触发口径核查（式 9.6）。

    Returns:
        估算占比；无申购份额时返回 ``None``。
    """
    denom = sum(sub_shares)
    if denom <= 0:
        return None
    winners = sum(s for s, n in zip(sub_shares, sub_navs) if nav_now >= n)
    return winners / denom


def caliber_check(
    m312_disclosed: float,
    pi_est: float | None,
    *,
    warn_pct: float = 0.10,
    block_pct: float = 0.20,
) -> str:
    """口径核查触发（式 9.6 / 附件A §7.4）。

    Returns:
        ``"PASS"`` / ``"CALIBER_MISMATCH_WARN"`` / ``"CALIBER_MISMATCH_BLOCK"``。

    Note:
        ``BLOCK`` 时该基金的历史季频归因**全部标记为不可信**，
        UI 降级为「仅展示披露值」。
    """
    if pi_est is None:
        return "CALIBER_CHECK_NO_DATA"
    diff = abs(m312_disclosed - pi_est)
    same_sign = (m312_disclosed - 0.5) * (pi_est - 0.5) >= 0
    if not same_sign or diff > block_pct:
        return "CALIBER_MISMATCH_BLOCK"
    if diff > warn_pct:
        return "CALIBER_MISMATCH_WARN"
    return "PASS"
