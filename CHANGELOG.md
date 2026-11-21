# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[Semantic Versioning](https://semver.org/lang/zh-CN/)。

> **纪律**（SOW §11.2.1）：「CHANGELOG 必须由人写，不得自动生成后不读。」

---

## [0.4.0] - 2026-11-21

### Added —— 对账 8 步（目标①交付物）

- 新增 `reconcile` 模块：附件A §8.2 的 8 个步骤逐条实现
  （份额×净值 / 份额连续性 / 现金分红 / 净申购双方案 / 管理费 / 费率表 / 归属期间 / Other 残差）
- `ReconcileStep` / `ReconcileReport`：判级 PASS/WARN/BLOCK/SKIP + 放行裁定 
  PASS/CONDITIONAL_PASS/FAIL（§8.3）
- `render()` 输出与 §8.2 样例**逐字对齐**的结构化报告
- 三条纪律固化为断言：**不得跳步**（缺步/乱序/重复即抛错）、
  **SKIP ≠ PASS**（无披露数据不算通过）、**步 1–3 失败即 FAIL**（地基不牢）
- 测试 +26 项（237 全绿），覆盖率 90.59% → **91.33%**

### Changed

- 版本 0.3.0 → 0.4.0；黄金向量 62 → **67 组**（新增 5 组对账向量）

---

## [1.0.0rc1] - 2026-11-21

### Added —— 裁定书三 §二 C1-C4（诊断可见性与拦截）

`R_net` 双路对账在 M1 仅作诊断，但「诊断」不等于「可以静默」。四条硬条件落地：

- **C1** `build_attribution`：`reconcile_gap` + `estimate_grade` **永远在场**（无法计算时为 None）
- **C2** `should_include_components`：C 级分量**默认不返回**，需显式 `include_diagnostics=True`
- **C3** `assert_export_allowed`：文章/分享卡/报告引用 C 级分量抛 `DiagnosticNotAllowedError`
- **C4** `reconcile_alert`：返回 `(是否告警, 日志码, 文案)`；**core 不写日志**，由 API 层记

阶梯门写死：M1 仅诊断 / M2 ≤5% / M3 ≤1%；未达则分量永久标 C。

### Changed

- 版本 0.4.0 → **1.0.0rc1**（M1 有条件通过，待衡鉴复跑后转 v1.0.0）
- `test_version_is_semver` 支持 PEP 440 预发布后缀（`1.0.0rc1`）
- README 补 §7.5：C1-C4 纪律 + ratio 定义 + 两个中位统计量的分工

### Fixed

- **架构门抓到 core 引入 `logging`**：初版在 core 里写 WARN 日志，违反零依赖纯函数约束。
  改为「core 判定该不该叫，API 决定叫到哪」，并新增纯度守卫断言。
  这不是妥协，是正确的分层——日志是 I/O 副作用，不属于纯函数库。

### 测试

- 298 项全绿（+32），覆盖率 **92.12%**
- 黄金向量 67/67 通过

---

## [Unreleased]

### Planned（M1 · 目标 v1.0.0）

- 黄金向量由 12 组补到 50 组
- 陷阱 T5 / T6 / T10 / T12 / T13 实现（依赖数据层字段）
- 对账 8 步流程封装为可执行报告（附件A §8.2）
- 混频降级的完整日志与精度徽章输出

---

## [0.3.0] - 2026-11-21

**M1 门禁返工版**（依《裁定书二》P0-1~P0-6 + P1-1/P1-2）。
core v0.1.0 的裁定是「可作开发基线，不可作验收基线」——两条最关键的守卫是恒真的：
架构守卫拦不住 `os.environ`，归因残差门永远等于 0。本版全部修复。

### Fixed — BLOCK 级

- **B01 架构守卫形同虚设**：`test_only_stdlib_imports` 只遍历 `ast.ImportFrom`，
  不看 `ast.Import`，禁用名单无 `os/pathlib/sys`。
  → 扫描逻辑抽成纯函数 `scan_violations()`，统一遍历两类节点；
  名单补 `os/pathlib/sys/subprocess/requests/httpx/duckdb/sqlalchemy/tqdm`；
  **新增 8 条负控测试**（注入 `import os` 等必须报违规）。
  **实证**：注入 `import os` 后 `test_no_forbidden_imports` 失败，移除后 14 passed。

- **B02 归因残差门恒真**：`r_net_from_identity` 由 A 式反解 `R_net`、`waterfall` 再用它
  ⇒ 残差 ≡ 0；期末 AUM 改大 27% 仍 `passed=True`。
  → 改为**双路独立**：
  `r_net_from_identity`（仅诊断，docstring 标注禁用）/ `r_net_from_return`（A_0×收益率）/
  `r_net_from_disclosure`（R_gross−Fee）；新增 `reconcile_r_net()` 与
  `waterfall_independent()`；`waterfall()` 检测到「残差恰为 0」时发 `SelfProvisionWarning`。
  **实证**：AUM 偏 1% → 残差 10.714%、`passed=False`、瀑布抛 `AttributionResidualError`。

### Fixed — P0 / 偏离

- **W01 / DEP-01**：`return_decomposition.div_effect` 符号与式(6.4) 相反
  → 改为 `−Div_cash/A_0`，三分量严格可加；新增闭合性断言（容差 1e-9）与
  「旧缺陷指纹」回归测试（误用正号时差值恒为 `2×Div/A_0`）。
- **DEV-001b**：年化缺口 17.2438 → **17.2456**（原为截断值相减之误）。
- **9 条恒绿测试**删除/改写：
  `test_deviation_dev001_is_documented` 改为断言「引擎值 == 修订后文档值」；
  7 条 `assert reason` 的陷阱占位改为**覆盖登记表自检**
  （已实现陷阱必须有真实测试函数、未实现陷阱必须写明阻塞原因、数量钉死）。
- **DEP-02 / SPEC-GAP-01**：业绩报酬 HWM 在未达 hurdle 时仍上移
  → 改为「仅在超额被计提时上移」；另提供显式变体
  `performance_fee_daily_hwm_follows_nav()` 供合同特别约定时使用。
- **DEP-03 / SPEC-GAP-03**：`trade_cost_rate_pct` 的 `include_stamp_duty` 默认 `True`
  导致买入侧多计 0.05 pct → 改为 `side: Literal["buy","sell"]` **必填**，
  印花税仅卖出侧；新增 `trade_cost_rate_both_sides()`。
- **SPEC-GAP-04**：`annualize_by_periods` docstring 标注「仅等间隔算例，生产禁用」。
- **SPEC-GAP-05**：`PrecisionLevel` 四值 + 中文标签 + JSON 往返 + 不得序列化为空。

### Added — 9 项缺失指标（MISS-01~09，新模块 `metrics`）

M204 年化波动率 / M205 最大回撤（带峰谷定位）/ M206 夏普 / M207 索提诺 /
M208 卡玛 / M306 每元管理费对应基民盈亏 / M307 持有体验分布 /
M310 费用率 / M313 换手率（式 3.6 估算 + 披露口径 + 偏离告警）。

### Added — 契约

- `contracts/golden_vectors.json` 由 4 组 → **12 组**（Tier-1 手算 4 / Tier-2 回归 8）
- GV-01 增补 `mwr_pct`、`gap_ann_pp = 17.2456`，并引入**按字段容差**
  （文档只公布到 4 位小数的字段用 1e-4，其余仍 1e-6）
- 新增 GV-11（M205）、GV-12（M307）

### Changed

- 版本 `0.2.0` → `0.3.0`（**BREAKING**：`trade_cost_rate_pct` /
  `trade_fee_from_turnover` 新增必填 `side`；`waterfall` 语义改为「仅做算术」
  ，生产请用 `waterfall_independent`）
- 测试 140 → **211** 项，覆盖率 88.11% → **90.59%**

### Fixed — 工程

- `src/gwfund_core/__init__.py` 曾被 PowerShell 文本往返破坏
  （中文注释乱码 + `"attribution"` 被并入注释行导致未导出）→ 整文件重写

---

## [0.2.0] - 2026-11-21

### Added

- `returns.dividend_from_acc_nav()` / `returns.derive_dividend_series()`
  —— 由「累计净值 − 单位净值」的差分还原每份现金分红。

  **解决了一个已知数据缺口**：东财 `pingzhongdata` 的 `unitMoney` 金额已被服务端剥离
  （只剩「每10份派现金元」空模板），而 `acc_nav` 保留完整数值。
  该推导是「现金分红总额 `Div_cash`」口径的**唯一可用入口**。

  实测校验：000001 推出 **25 笔、合计 2.5730 元/份**，与《附件A》§8.2 对账样例
  「步3 现金分红：PASS（25 笔，合计 2.573 元/份）」逐位一致。

- `scripts/check_vectors.py` —— 四端一致性对拍器（读 `contracts/golden_vectors.json`）
- `tests/test_contract.py` —— 4 项契约测试（存在性 / Tier-1 ≥4 / 全量通过 / 版本锁）
- `tests/test_dividend.py` —— 7 项分红还原测试

### Changed

- 版本 `0.1.0` → `0.2.0`（新增公开 API，MINOR 递增；无破坏性变更）
- 测试 128 → **140** 项，覆盖率 87.93% → **88.11%**

### Fixed

- 修正 `pyproject.toml` 与 `__init__.py` 的 UTF-8 BOM 污染（Windows PowerShell 写文件所致，
  会导致 TOML 解析失败）

---

## [0.1.0] - 2026-11-21

首个可运行版本：三条恒等式 + TWR/MWR + 费用计提 + 规模归因 + 混频 + 追高资金。

### Added

- `returns`：单期净收益（式 1.2）、TWR 链式（式 5.1）、复权净值（M103）、
  MWR/IRR（牛顿法 + 二分法双保险 + 多解检测，式 5.2）、基民收益缺口（式 5.3）、
  年化（T9「不足 1 年不年化」）
- `fees`：三类固定费率逐日计提（式 3.1–3.3，**分母用 `A_{t−1}`、闰年 366**）、
  累计费用（式 3.4）、规模分档浮动费率、业绩报酬型浮动费率（高水位路径）、
  交易费用估算（式 3.5）、年报校准（式 3.7）、申赎费分档查询（**闭区间**语义）
- `identities`：`P_inv`（式 4.1）、`NetSub` 费前口径（式 4.2a）、
  三条恒等式 A/B/C 与 (C')、`Other` 反解（式 4.4）、归一化残差（式 4.5）、
  `Other` 与赎回费相关性自检（陷阱 T2）、四端比对工具（容差 1e-6）
- `attribution`：规模归因瀑布（式 6.1）、归因残差门禁（式 6.2）、
  份额归因（式 6.3）、收益分解（式 6.4）
- `mixing`：方案 1 阶梯 / 方案 2 插值 / 方案 3 季度明细（**默认**）、
  三套日内分配权重（均匀/V 型/月末）、精度等级 A/B/C（§6.4）、
  AUM 误差上界（式 8.6/8.7）
- `chase`：净值历史分位（式 9.2）、追高金额/占比/后续盈亏/亏损（式 9.1/9.4/9.3/9.5）、
  监管人数口径 M312 的方向校验（式 9.6）
- `types`：`NavPoint` / `SharePeriod` / `FeeRate` / `FeeAccrual` / `IdentityCheck` /
  `WaterfallResult` / `MwrResult` / `PrecisionLevel` / `MixingMethod` / `WeightScheme`
- `errors`：4 个硬错误 + 4 个警告（残差超阈值**抛异常**，不静默）

### Tests

- 128 项，覆盖率 **87.9%**（要求 ≥80%）
- 黄金向量 4 组逐位复现：附件A §4.5 三期样例、决策 D10 MWR 用例、
  附件A §3.5 费用计提、附件A §5.3 归因样例
- 架构测试 5 项：禁止 I/O import、禁止 `open/exec/eval`、仅标准库、单文件 ≤800 行、SemVer
- 陷阱测试：T1/T2/T3/T4/T9/T11/T14 已覆盖；T5/T6/T7/T8/T10/T12/T13 显式登记为待实现

### Known Issues

- **DEV-001**：《附件A》§4.5 的 MWR 原文值 `0.4944%` 经三步独立复算为 **`0.497828%`**；
  本实现按数学真值，已在 `DEVIATIONS.md` 登记待裁定
- 33 指标 L3 结论层尚未全部封装
- 对账 8 步流程尚未封装

---

## 版本策略说明

| 阶段 | 规则 |
|---|---|
| `0.x` | MINOR 可含破坏性变更，须在 CHANGELOG 标 `BREAKING` |
| `1.0.0` | M1 门禁通过后冻结；此后 MINOR 只增不改 |
| 四端引用 | 严格等值锁 `gwfund-core==X.Y.Z`，不用 `>=` / `^` |
