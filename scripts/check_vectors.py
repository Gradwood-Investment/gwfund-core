"""四端一致性对拍器：按 `contracts/golden_vectors.json` 逐项校验。

**任何一端都复制这个脚本的逻辑**（不是复制算法）：
API / Web / 桌面 / 小程序各自把「按 vector_id 算出的数」提交给本脚本的形状，
由 CI 比对差异是否 ≤ tolerance（默认 1e-6）。差异超容差**阻断发布**。

用法::

    python scripts/check_vectors.py                 # 校验 core 自身
    python scripts/check_vectors.py --json out.json # 额外输出机器可读结果
    python scripts/check_vectors.py -v              # 逐项打印明细

退出码：0 = 全部通过；1 = 存在超差项或契约缺失。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from gwfund_core import attribution, chase, fees, identities, mixing, returns  # noqa: E402

VECTORS_PATH = ROOT.parent / "contracts" / "golden_vectors.json"


class Comparer:
    """按容差比较，容忍 None / bool / 数值三类。"""

    def __init__(self, tol: float) -> None:
        self.tol = tol
        self.failures: list[str] = []

    def check(self, vid: str, key: str, actual: object, expected: object) -> None:
        if expected is None or actual is None:
            if actual is not expected:
                self.failures.append(f"{vid}.{key}: 期望 {expected!r}，实得 {actual!r}")
            return
        if isinstance(expected, bool) or isinstance(actual, bool):
            if bool(actual) is not bool(expected):
                self.failures.append(f"{vid}.{key}: 期望 {expected!r}，实得 {actual!r}")
            return
        a, e = float(actual), float(expected)
        diff = abs(a - e)
        scale = max(abs(e), 1.0)
        if diff > self.tol and diff / scale > self.tol:
            self.failures.append(
                f"{vid}.{key}: 期望 {e!r}，实得 {a!r}，差 {diff:.3e}（容差 {self.tol:.0e}）"
            )


def compute_core(v: dict) -> dict:
    """给定契约中的一组向量，用 core 现算一遍，返回可比对的字典。"""
    vid, module, inp = v["id"], v["module"], v["inputs"]

    if vid == "GV-01":
        navs, cf, ts = inp["navs"], inp["cashflows"], inp["times"]
        rs = [returns.daily_return(navs[i - 1], navs[i]) for i in range(1, len(navs))]
        twr = returns.chain_return(rs)
        mwr = returns.irr(cf, ts).value
        return {"twr": twr, "mwr": mwr, "gap_pct": returns.investor_gap(twr, mwr) * 100}

    if vid == "GV-02":
        cf, ts = inp["cashflows"], inp["times"]
        twr = returns.chain_return([1.5 / 1.0 - 1.0, 1.2 / 1.5 - 1.0])
        mwr = returns.irr(cf, ts).value
        p = identities.p_inv(a_end=840.0, div_cash=0.0, a_start=100.0, net_sub=900.0)
        return {
            "twr": twr,
            "mwr": mwr,
            "gap_pct": returns.investor_gap(twr, mwr) * 100,
            "p_inv": float(p),
        }

    if vid == "GV-03":
        a = inp["a_prev"]
        acc = fees.daily_accrual(
            a, date(2026, 6, 1), inp["rate_mgmt_pct"], inp["rate_custody_pct"], inp["rate_sales_pct"]
        )
        acc_leap = fees.daily_accrual(
            a, date(2024, 6, 3), inp["rate_mgmt_pct"], inp["rate_custody_pct"], inp["rate_sales_pct"]
        )
        return {
            "mgmt_2026": float(acc.mgmt),
            "custody_2026": float(acc.custody),
            "total_2026": float(acc.total),
            "mgmt_2024_leap": float(acc_leap.mgmt),
        }

    if vid == "GV-04":
        a0, at = inp["a_start"], inp["a_end"]
        ns, dv = inp["net_sub"], inp["div_cash"]
        rn = attribution.r_net_from_identity(a0, at, ns, dv)
        wf = attribution.waterfall(a0, ns, rn, dv, at)
        return {
            "r_net": float(rn),
            "residual": float(wf.residual),
            "residual_pct": wf.residual_pct,
        }

    if vid == "GV-05":
        out: dict[str, object] = {}
        for case in inp["cases"]:
            r = returns.annualize(case["r"], case["days"])
            if case["days"] == 30:
                out["annualized_30"] = r.annualized
                out["value_30"] = r.value
            else:
                out[f"value_{case['days']}"] = r.value
        return out

    if vid == "GV-06":
        f26 = fees.daily_fee(inp["a_prev"], inp["rate_pct"], date(2026, 6, 1))
        f24 = fees.daily_fee(inp["a_prev"], inp["rate_pct"], date(2024, 6, 3))
        return {"ratio_non_leap_over_leap": float(f26 / f24)}

    if vid == "GV-07":
        dates = [date(2026, 4, 1) + timedelta(days=i) for i in range(10)]
        ns = mixing.quarterly_net_sub(
            inp["sub_shares"], inp["red_shares"], inp["navs"], dates
        )
        return {
            "net_sub": float(ns),
            "mean_nav": sum(inp["navs"]) / len(inp["navs"]),
        }

    if vid == "GV-08":
        YI = 100_000_000.0
        subs = [x * YI for x in inp["net_subs_yi"]]
        hist = [0.9, 0.95, 1.05, 1.1123, 1.0823, 1.02006, 1.63674]
        s = chase.chase_summary(subs, hist, theta=inp["theta"])
        return {
            "chase_amount": s["chase_amount"],
            "has_chasing": s["has_chasing"],
            "chase_ratio": s["chase_ratio"],
        }

    if vid == "GV-09":
        YI = 100_000_000.0
        subs = [x * YI for x in inp["net_subs_yi"]]
        s = chase.chase_summary(subs, inp["navs"], theta=inp["theta"])
        return {
            "chase_amount": s["chase_amount"],
            "chase_ratio": s["chase_ratio"],
            "has_chasing": s["has_chasing"],
        }

    if vid == "GV-10":
        tier = [(Decimal(t) if t is not None else None, r) for t, r in inp["tier"]]
        f = fees.daily_fee_by_aum_tier(Decimal(str(inp["a_prev"])), date(2026, 6, 1), tier)
        return {"daily_fee": float(f)}

    raise KeyError(f"{vid}: 未知向量（module={module}）——契约新增了向量但本脚本未实现")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="四端一致性对拍")
    ap.add_argument("--json", type=Path, help="额外输出机器可读结果")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if not VECTORS_PATH.exists():
        print(f"[FAIL] 契约文件缺失：{VECTORS_PATH}")
        print("       先运行 python 10_dev/contracts/build_golden_vectors.py")
        return 1

    data = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    tol = float(data.get("tolerance", 1e-6))
    cmp = Comparer(tol)

    results = []
    for v in data["vectors"]:
        try:
            actual = compute_core(v)
        except Exception as exc:  # noqa: BLE001
            cmp.failures.append(f"{v['id']}: 计算抛错 {type(exc).__name__}: {exc}")
            continue
        before = len(cmp.failures)
        for key, expected in v["expected"].items():
            if key not in actual:
                cmp.failures.append(f"{v['id']}.{key}: 引擎未返回该字段")
                continue
            cmp.check(v["id"], key, actual[key], expected)
        ok = len(cmp.failures) == before
        results.append({"id": v["id"], "name": v["name"], "tier": v["tier"], "passed": ok})
        if args.verbose:
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {v['id']} (Tier-{v['tier']}) {v['name']}")

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{'='*70}")
    print(f"四端一致性对拍 · core {data.get('core_version')} · 契约 {data.get('schema_version')}")
    print(f"  向量 {total} 组：通过 {passed} / 失败 {total - passed}   容差 {tol:.0e}")
    if cmp.failures:
        print(f"\n  失败明细（{len(cmp.failures)} 条）：")
        for f in cmp.failures:
            print(f"    - {f}")
    print(f"{'='*70}")

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "core_version": data.get("core_version"),
                    "tolerance": tol,
                    "total": total,
                    "passed": passed,
                    "results": results,
                    "failures": cmp.failures,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return 0 if not cmp.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
