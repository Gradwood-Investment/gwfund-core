# 指标覆盖矩阵（COVERAGE）

> **口径来源**：《附件A》§5.3 指标字典。SOW §5.3 标题写「**33** 个指标」，但该节表格实际列出 **47** 个 ID
> （M101–M115 / M201–M219 / M301–M313）。此不一致已由凌霄渡复核为 **DEP-05** 登记，
> 本矩阵按**表内 47 个**统计并请求裁定（见 DEVIATIONS / 请示函 请示三）。
>
> **本文件不是自说自话的表**：`scripts/check_coverage.py` 会逐个验证「标为 core 实现」的指标
> 在 `gwfund_core` 里**真实存在**对应函数；`tests/test_coverage.py` 把它纳入 pytest。
> 任一处映射失效 ⇒ CI 红。

## 一、总览

| 类别 | 数量 | 说明 |
|---|---|---|
| 指标总数（SOW 表内 ID） | **47** | |
| **core 计算** | **37** | 纯函数实现，四端共用（MC-01） |
| 源数据直取 | 10 | L1 基础字段，core 不计算，由数据层提供 |
| 需外部披露值 | 4 | D15/D18/D19 三类不可完全自动化字段域；core 提供计算与校验能力，实际值待人工复核 |

## 二、逐项映射

标记：✅ core 纯函数 / 📥 源数据直取 / 🔶 需披露值（core 提供计算/校验）

### L1 基础层（15）

| ID | 指标 | 状态 | 实现 |
|---|---|---|---|
| M101 | 单位净值 | 📥 | 数据层 `fund_nav.unit_nav` |
| M102 | 累计净值 | 📥 | `fund_nav.acc_nav` |
| M103 | 复权净值 | ✅ | `returns.adjusted_nav_series` |
| M104 | 总份额 | 📥 | `fund_share.shares_wan` |
| M105 | 资产净值 | ✅ | `identities.p_inv`（`A_t = S_t × N_t`，式 1.1） |
| M106 | 区间申购份额 | 📥 | `fund_share.sub_shares_wan` |
| M107 | 区间赎回份额 | 📥 | `fund_share.red_shares_wan` |
| M108 | 管理费率 | 📥 | `fund_fee.management_rate_pct` |
| M109 | 托管费率 | 📥 | `fund_fee.custody_rate_pct` |
| M110 | 销售服务费率 | 📥 | `fund_fee.sales_service_rate_pct` |
| M111 | 申购费率 | ✅ | `fees.lookup_tier_rate` |
| M112 | 赎回费率 | ✅ | `fees.lookup_tier_rate`（**闭区间**语义） |
| M113 | 分红 | ✅ | `returns.derive_dividend_series`（Δacc − Δunit，见 DEV-010b） |
| M114 | 基金分类 | 📥 | `fund_profile.fund_type` |
| M115 | 经理任职 | 📥 | 待 `fund_manager` 表（本轮补） |

### L2 派生层（19）

| ID | 指标 | 状态 | 实现 |
|---|---|---|---|
| M201 | 日收益率 | ✅ | `returns.daily_return`（式 1.2） |
| M202 | 区间收益率 | ✅ | `returns.twr_from_adjusted_nav` |
| M203 | 年化收益率 | ✅ | `returns.annualize`（T9：不足 1 年不年化） |
| M204 | 年化波动率 | ✅ | `metrics.annualized_volatility` |
| M205 | 最大回撤 | ✅ | `metrics.max_drawdown`（带峰谷定位） |
| M206 | 夏普比率 | ✅ | `metrics.sharpe_ratio` |
| M207 | 索提诺比率 | ✅ | `metrics.sortino_ratio` |
| M208 | 卡玛比率 | ✅ | `metrics.calmar_ratio` |
| M209 | 同类排名分位 | ✅ | `aggregate.percentile_rank`（**并列按平均秩**） |
| M210 | 日度管理费 | ✅ | `fees.daily_fee`（式 3.1） |
| M211 | 累计管理费 | ✅ | `fees.cumulative_fee` |
| M212 | 累计托管费 | ✅ | `fees.cumulative_fee` |
| M213 | 累计销售服务费 | ✅ | `fees.cumulative_fee` |
| M214 | 累计交易费用 | 🔶 | `fees.trade_fee_from_turnover` + 年报校准（式 3.7）；披露值属 D15 |
| M215 | 费用合计 | 🔶 | `fees.cumulative_fee`；`F_other` 需年报（D15） |
| M216 | 净申购金额 | ✅ | `mixing.quarterly_net_sub`（方案 3，式 8.4/8.5） |
| M217 | 基民净盈亏 | ✅ | `identities.p_inv`（式 4.1） |
| M218 | TWR | ✅ | `returns.chain_return` / `twr_from_adjusted_nav` |
| M219 | MWR | ✅ | `returns.mwr_from_flows`（牛顿+二分，多解检测） |

### L3 结论层（13）

| ID | 指标 | 状态 | 实现 |
|---|---|---|---|
| M301 | 基民收益缺口 | ✅ | `returns.investor_gap`（式 5.3） |
| M302 | 规模归因 | ✅ | `attribution.waterfall`（式 6.1，六段） |
| M303 | 规模增长来源占比 | ✅ | `aggregate.net_sub_share_of_growth` |
| M304 | 追高资金占比 | ✅ | `chase.chase_ratio`（式 9.4） |
| M305 | 追高资金浮亏 | ✅ | `chase.chase_loss`（式 9.5） |
| M306 | 每元管理费盈亏 | ✅ | `metrics.fee_per_yuan_pnl` |
| M307 | 持有体验分布 | ✅ | `metrics.holding_experience` |
| M308 | 公司层面聚合 | ✅ | `aggregate.aggregate_company`（含 A/C 合并开关） |
| M309 | 经理层面聚合 | ✅ | `aggregate.aggregate_manager`（**默认含离任**） |
| M310 | 费用率 | ✅ | `metrics.fee_ratio` |
| M311 | 持有人结构 | ✅ | `aggregate.holder_structure`（**缺档不补 0**）；数据待 `fund_holder` 表 |
| M312 | 盈利投资者占比（监管） | 🔶 | `chase.caliber_check` + `estimate_profit_investor_ratio`；实际值属 D18 |
| M313 | 换手率 | 🔶 | `metrics.turnover_from_weights`（估算）+ `turnover_disclosed`（披露）；披露值属 D19 |

## 三、四个「需披露值」指标的现状

这四个是 SOW §6.1 点名的「**仅有的三个无法完全自动化字段域**」（D15 费用金额 / D18 盈利投资者占比 / D19 换手率）。
core 已提供**计算与校验能力**，缺的是**黄金标准值**：

| 指标 | core 已提供 | 缺什么 | 补法 |
|---|---|---|---|
| M214 交易费用 | 换手估算（式 3.5）+ 年报反算校准（式 3.7） | 年报「交易费用」实际金额 | D15 半自动提取 + 人工复核 |
| M215 费用合计 | 三类固定费率累计 | `F_other`（审计/信披/上市费） | 同上 |
| M312 盈利投资者占比 | `caliber_check`（式 9.6）方向校验 | 监管披露值（2026 中报首期） | D18 XBRL / 巨潮 |
| M313 换手率 | 式 3.6 估算 + 披露口径公式 | 年报披露换手率 | D19 |

> **纪律**：这四项在拿到披露值之前，对外必须标 `C 级` 或「估算」，**不得以实际值呈现**。

## 四、校验方式

```bash
cd 10_dev/gwfund-core
python scripts/check_coverage.py          # 人读
python scripts/check_coverage.py --json   # 机器读
python -m pytest -q tests/test_coverage.py
```

当前结果：

```
指标总数 47（SOW §5.3 表内实际 ID 数，非标题的 33）
  core 计算 37   源数据直取 10   需披露值 4
[PASS] 所有「core 实现」的指标均有对应函数
```

## 五、与 M1 门禁的关系

| 门禁要求 | 现状 |
|---|---|
| 「33 指标全覆盖」 | 按表内 47 计：37/37 可计算指标已实现 ✅；10 项属源数据；4 项待披露值 |
| 口径一致性 | 同一函数被四端共用（api / web / desktop / miniapp 均只调 API） |

**待裁定**：v1 的权威指标口径是 33 还是 47（请示函 请示三）。若定为 33 且与本表 47 的子集不同，本矩阵需重排。
