"""指标覆盖矩阵的可执行校验。

`COVERAGE.md` 里每个标「✅ 已实现」的指标都必须在 core 里**真实存在**对应函数——
否则矩阵就是一张自说自话的表。本脚本把这句话变成会失败的检查。

用法::

    python scripts/check_coverage.py          # 校验
    python scripts/check_coverage.py --json   # 机器可读
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gwfund_core  # noqa: E402

#: 指标 → (模块, 函数名)。**这是 COVERAGE.md 的唯一真相来源**。
COVERAGE: dict[str, tuple[str, str] | None] = {
    # ---------------- L1 基础层 ----------------
    "M101": None,  # 单位净值：源数据直取
    "M102": None,  # 累计净值：源数据直取
    "M103": ("returns", "adjusted_nav_series"),
    "M104": None,  # 总份额：源数据直取
    "M105": ("identities", "p_inv"),  # A_t = S·N 用于 p_inv 的入参，无独立函数
    "M106": None,  # 区间申购份额：源数据直取
    "M107": None,  # 区间赎回份额：源数据直取
    "M108": None,  # 管理费率：源数据直取
    "M109": None,  # 托管费率：源数据直取
    "M110": None,  # 销售服务费率：源数据直取
    "M111": ("fees", "lookup_tier_rate"),
    "M112": ("fees", "lookup_tier_rate"),
    "M113": ("returns", "derive_dividend_series"),
    "M114": None,  # 基金分类：源数据直取
    "M115": None,  # 经理任职：源数据直取
    # ---------------- L2 派生层 ----------------
    "M201": ("returns", "daily_return"),
    "M202": ("returns", "twr_from_adjusted_nav"),
    "M203": ("returns", "annualize"),
    "M204": ("metrics", "annualized_volatility"),
    "M205": ("metrics", "max_drawdown"),
    "M206": ("metrics", "sharpe_ratio"),
    "M207": ("metrics", "sortino_ratio"),
    "M208": ("metrics", "calmar_ratio"),
    "M209": ("aggregate", "percentile_rank"),
    "M210": ("fees", "daily_fee"),
    "M211": ("fees", "cumulative_fee"),
    "M212": ("fees", "cumulative_fee"),
    "M213": ("fees", "cumulative_fee"),
    "M214": ("fees", "trade_fee_from_turnover"),
    "M215": ("fees", "cumulative_fee"),
    "M216": ("mixing", "quarterly_net_sub"),
    "M217": ("identities", "p_inv"),
    "M218": ("returns", "chain_return"),
    "M219": ("returns", "mwr_from_flows"),
    # ---------------- L3 结论层 ----------------
    "M301": ("returns", "investor_gap"),
    "M302": ("attribution", "waterfall"),
    "M303": ("aggregate", "net_sub_share_of_growth"),
    "M304": ("chase", "chase_ratio"),
    "M305": ("chase", "chase_loss"),
    "M306": ("metrics", "fee_per_yuan_pnl"),
    "M307": ("metrics", "holding_experience"),
    "M308": ("aggregate", "aggregate_company"),
    "M309": ("aggregate", "aggregate_manager"),
    "M310": ("metrics", "fee_ratio"),
    "M311": ("aggregate", "holder_structure"),
    "M312": ("chase", "caliber_check"),
    "M313": ("metrics", "turnover_disclosed"),
}

#: 需要外部披露值（D15/D18/D19）才能算「实际值」的指标——core 只提供计算与校验能力
NEEDS_DISCLOSURE = {"M214", "M215", "M312", "M313"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验指标覆盖矩阵")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)

    missing: list[str] = []
    for mid, target in COVERAGE.items():
        if target is None:
            continue
        mod, fn = target
        module = getattr(gwfund_core, mod, None)
        if module is None:
            missing.append(f"{mid}: 模块 {mod} 不存在")
            continue
        if not hasattr(module, fn):
            missing.append(f"{mid}: {mod}.{fn} 不存在")

    total = len(COVERAGE)
    core_impl = sum(1 for v in COVERAGE.values() if v is not None)
    raw_only = total - core_impl

    if args.json:
        print(
            json.dumps(
                {
                    "total": total,
                    "core_implemented": core_impl,
                    "raw_data_only": raw_only,
                    "needs_disclosure": sorted(NEEDS_DISCLOSURE),
                    "missing": missing,
                    "passed": not missing,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"指标总数 {total}（SOW §5.3 表内实际 ID 数，非标题的 33）")
        print(f"  core 计算 {core_impl}   源数据直取 {raw_only}   需披露值 {len(NEEDS_DISCLOSURE)}")
        if missing:
            print(f"\n[FAIL] {len(missing)} 处映射失效：")
            for m in missing:
                print("  -", m)
        else:
            print("\n[PASS] 所有「core 实现」的指标均有对应函数")

    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
