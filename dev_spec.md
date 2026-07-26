# DataPilot 主链迁移与小范围试点 Development Specification

> **当前状态：未完成，禁止按可上线或可试点交付。**
>
> 本文件是当前修复工作的唯一产品、架构、实施顺序和验收权威来源。历史 Phase 0–6 报告、旧 handoff、离线 benchmark、测试数量或对话与本文件冲突时，以本文件为准。
>
> 2026-07-24 已确认：后端大量单测通过，但真实 `/api/chat` 仍稳定出现 GMV 权限拒绝、追问丢上下文、DELETE 被路由成数据地图等问题。根因是新分析内核主要运行在测试和离线 runner，线上仍执行旧 LangGraph 主链。旧阶段的完成标记全部失效，必须按本文重新验收。
>
> **2026-07-24 文档补丁（执行者重审）：** R0 降为最少失败钉（4 套真 MySQL 归 R2）；R1 强制拆 R1a/b/c；YAML 与数据面权限双轨写死；ReAct 补 L0/export/reuse/catalog；`TurnResult.rows` 强制 preview；诊断伪完成模式 A/B/C；admission 反 file-exists。补丁不改变「未完成、禁止上线/试点」状态。
>
> **2026-07-25 主链复核补丁：** R0–R5 的组件和部分 API 能力已有明显改善，但 pilot v2 仍绕过统一 Supervisor，生产诊断仍注入 deterministic agent，QueryHarness 仍依赖旧 Graph，SSE Harness 生命周期未接通。新增强制 **Recovery Phase 5.5**；在该阶段全部硬验收通过前，禁止进入 R6。
>
> **2026-07-26 查询语义复核补丁：** 最新两套 Mock 真人测试证明 Supervisor/路由命中不等于查询正确：公开 API 会对用户明确点名的表选错主体、遗漏状态过滤/分组/Top-N/复合指标，并把语义已变化的追问复用为旧结果；旧 `soft_ok=12/12` 只证明链路返回，不证明业务正确。新增强制 **Recovery Phase 3.5**：陌生 MySQL 画像与语义 Grounding 门禁。R3.5 未通过前，R4–R5.5 的已有结果只保留为组件证据，全部暂停作为试点准入证据。

---

## 0. 后续执行者必须先遵守

1. 每次开始修改应用代码前，完整阅读本文件。
2. 先为当前阶段建立从公开 API 进入的失败用例，再修改实现。
3. 不得用增加 Prompt、关键词、业务表名或新 Agent 绕过主链迁移。
4. 不得用 mock-only、direct function、SQLite-only、oracle 或文件存在证明线上阶段完成。
5. 不得在前一阶段未通过全部硬验收时开始下一阶段。
6. 不得把 `pytest` 全绿等同于产品可用；必须同时通过真实 API、真实 MySQL 和真人任务验收。
7. 不得修改本文件的正确率阈值或验收定义来让实现过关；产品负责人书面确认后才能变更。
8. 工作区包含大量未提交历史改动。只修改当前阶段涉及的文件，保留无关改动。
9. 每阶段单独提交，提交前记录测试、API transcript、Trace ID 和失败样本。
10. Recovery Phase 6 完成前，对外状态始终是：**未完成，禁止按可上线或可试点交付。**
11. R3.5 与 R5.5 都是 R6 的强制前置；不得以“R1–R5 已绿”“存在 Harness 类”“意图准确率达标”或“报告中出现 agents_called”跳过语义或生产接线验收。
12. `soft_ok is not semantic-correctness evidence`；`intent accuracy is not semantic-correctness evidence`。HTTP 200、终态、SQL 字符串、流畅回答和 Artifact 数量均不能替代表/字段/条件/数值 Ground Truth 验收。

---

## 1. 产品目标与首轮边界

### 1.1 最终目标

小范围用户连接一套任意结构的 MySQL 数据库，系统自动完成建档和可用性检查；建档完成后，用户能够通过自然语言稳定完成常用指标查询、趋势、分组、对比和连续下钻，并得到可核对、可解释、失败可恢复的答案。

### 1.2 首轮支持

- 数据源：MySQL。
- 数据库结构：不依赖固定表名、字段名或预置电商空间。
- 连接方式：只读账号。
- 建档：允许几十秒到两分钟的自动扫描。
- 查询能力：
  - 表和字段用途理解；
  - `COUNT / SUM / AVG / MIN / MAX`；
  - 明细筛选；
  - 时间趋势；
  - 单维和多维分组；
  - Top-N；
  - 两个明确时间段的对比；
  - 基于可信关系路径的多表查询；
  - 对上一轮结果修改时间、增加维度、增加过滤和继续下钻。

### 1.3 首轮不承诺

- 非 MySQL 数据源。
- 任意复杂 SQL 的自然语言等价能力。
- 没有可信关系路径时强行 JOIN。
- 在没有业务定义时发明 GMV、活跃用户或转化率口径。
- 把相关性写成因果关系。
- 无有效查询数据时生成诊断报告。
- 正式生产高并发 SLA。

### 1.4 正确行为优先于强行回答

- 信息不足时主动澄清是正确结果。
- 无法可靠关联时拒绝或询问是正确结果。
- 权限拒绝、空结果和 SQL 拒绝必须向用户展示真实原因。
- 猜测指标、时间、关系或数字，即使语言流畅，也属于错误答案。

---

## 2. 当前真实状态

### 2.1 已有且可复用的资产

以下模块可以保留，但当前只能视为“组件资产”，不能视为线上能力：

- `backend/app/agents/semantic_catalog.py`
- `backend/app/agents/profiler.py`
- `backend/app/agents/analysis_spec.py`
- `backend/app/agents/analysis_pipeline.py`
- `backend/app/agents/sql_compiler.py`
- `backend/app/agents/query_outcome.py`
- `backend/app/agents/active_analysis_state.py`
- `backend/app/agents/spec_patch.py`
- `backend/app/agents/controlled_loop.py`
- `backend/app/agents/diagnosis_admission.py`
- `backend/app/agents/diagnosis_pipeline.py`
- `backend/app/agents/diagnosis_summary.py`
- `backend/app/agents/supervisor_decision.py`
- `backend/app/agents/harness.py`
- `backend/app/agents/roles.py`
- `backend/app/agents/procedure.py`
- `backend/app/agents/export.py`
- `backend/app/a2a/contracts.py`
- `backend/app/a2a/registry.py`
- `backend/app/a2a/dispatcher.py`
- 现有授权、只读 SQL、空间隔离、审计、会话和前端基础。

### 2.2 当前 pilot v2 实际执行链

```text
/api/chat 或 /api/chat/stream
  → AnalysisApplicationService
  → L0 写拒绝 / 数据面预检 / Catalog gate
  → ApplicationService 关键词判断诊断、摘要、chat/help
  ├─ 普通分析 → Controlled Loop / Spec Patch / GuardedMySQLExecutor
  ├─ 深度诊断 → deterministic_agent_call
  └─ 其余或回滚 → backend/app/services/agent.py 旧 LangGraph / Supervisor
```

`SemanticCatalog → AnalysisSpec → Controlled Loop → ActiveAnalysisState` 已进入部分 v2 请求，但统一 Supervisor 与真实 Harness/Dispatcher 尚未成为线上唯一控制面。当前属于“查询内核已迁移、意图控制面与诊断执行面未迁移完成”，不得描述为多 Agent 主链完成。

### 2.3 已复现的线上问题

| 问题 | 已确认原因 |
|---|---|
| v2 请求未统一经过 Supervisor | ApplicationService 在 Supervisor 前用 `_is_diagnosis_question`、`_is_diagnosis_summary_cite`、`_is_pure_chat_help` 分流；其余请求直接进入 controlled loop |
| 路由组件测试不能证明真实正确率 | Supervisor golden eval 的 L1 使用 oracle mock 回填期望 intent，只证明结构和降级接线 |
| QueryHarness 与 v2 Query Kernel 分裂 | QueryHarness 仍调用旧 `get_graph()`，普通 v2 查询则绕过 Harness 调 controlled loop |
| v2 报告“调用 Agent”但未运行真实 Harness | `_run_v2_diagnosis` 注入 `deterministic_agent_call`；`agents_called` 只是目标名称，不能对应真实 A2A message / Harness lifecycle |
| SSE 诊断缺少生产 Agent 过程事件 | v2 stream 仅返回最终 `complete`；完整测试仍有旧 playbook lifecycle 断言失败 |
| 系统性脚本出现两个 429 | `/api/chat` 为 30/min，32 轮脚本撞限流；节流重跑中止，尚无完整修复证据 |
| 报告不可复现 | 最新系统性报告缺 commit、working tree、MySQL/dataset version、trace_ids 等 §6.2 强制来源字段 |

### 2.4 历史阶段状态全部重置

| 历史阶段 | 当前认定 |
|---|---|
| 原 Phase 0 | 评测脚手架存在；oracle 和 fixture 不能证明产品正确，人工一致性未完成 |
| 原 Phase 1 | QueryOutcome 契约部分存在；真实 tester 预检和主 API 门禁失败 |
| 原 Phase 2 | DDL 离线建档存在；真实 MySQL 建档与目录持久化未完成 |
| 原 Phase 3 | 离线 AnalysisSpec 管线存在；线上 Chat 未接入 |
| 原 Phase 4 | 离线连续追问存在；Session 持久化主链未接入 |
| 原 Phase 5 | 诊断准入组件存在；所有公开入口未统一接入 |
| 原 Phase 6 | 运维脚手架存在；真实试点准入未通过 |

旧报告只能作为组件开发参考，不得直接恢复任何完成勾选。

---

## 3. 唯一目标主链

```text
Frontend / API Client
  ├─ POST /api/chat
  └─ POST /api/chat/stream
            ↓
AnalysisApplicationService.handle_turn(TurnRequest)
            ↓
1. Auth / Space / Session / Pilot Precheck
2. Load or build live SemanticCatalog
3. Load ActiveAnalysisState
4. SupervisorDecision runs exactly once and returns validated TaskSpec
5. Supervisor controlled observe → decide → one act
6. Dispatch the act to a registered Harness or deterministic kernel action
7. Build or patch AnalysisSpec
8. GuardedMySQLExecutor executes compiled SQL
9. Produce QueryOutcome + EvidenceBundle
10. Persist ActiveAnalysisState and artifacts
11. Assemble answer or run admitted diagnosis
            ↓
Canonical TurnResult
  ├─ JSON adapter returns once
  └─ SSE adapter emits lifecycle events + exactly one terminal result
```

### 3.1 唯一应用服务

必须建立一个与 HTTP 传输无关的应用入口，推荐位置：

- `backend/app/application/analysis_service.py`
- `backend/app/application/contracts.py`

名称可以按现有项目规范调整，但必须满足：

- JSON 和 SSE 调用同一个业务方法；
- 业务方法不依赖 FastAPI Request 或 SSE 文本；
- 返回统一 `TurnResult`；
- streaming 只是观察业务事件的方式，不能拥有另一套业务逻辑；
- 深度诊断、普通查询、澄清和失败都经过同一入口。
- 除 L0 拒绝、认证/空间/预检失败和 Catalog 阻断外，每个 v2 Turn 必须且只能执行一次 `SupervisorDecision`；
- ApplicationService 不得用关键词或正则直接决定 diagnosis、summary、chat、help、schema 或 table-query 的业务路由；
- Supervisor 输出的结构化 `TaskSpec` 是后续调度和 controlled loop 的唯一业务意图输入。

### 3.2 统一输入输出契约

`TurnRequest` 至少包含：

```text
question
user_id
user_role
space_id
session_id
workspace_id
selected_metric          # optional legacy；不得作为任意 MySQL 主链前置
selected_query_type     # optional legacy
request_id
```

`TurnResult` 至少包含：

```text
response_type
terminal_status
message
trace_id
analysis_spec
query_outcome
evidence
sql
columns
rows                   # 仅 preview，见下方硬约束
rows_count
chart
artifacts
stop_reason
active_state_version
kernel_route
```

约束：

- `kernel_route` 明确为 `analysis_kernel_v2` 或 `legacy_rollback`。
- 试点流量必须使用 `analysis_kernel_v2`。
- JSON 与 SSE 最终 `TurnResult` 的业务字段必须等价。
- 用户看到的失败文本必须来自结构化 terminal status，不得由传输层猜测。
- **`rows` 硬约束（防无界数据）：**
  - API / SSE / Trace / Agent 事件默认只携带 preview，上限建议 ≤ 100 行（可配置，硬顶 ≤ 500）。
  - 必须同时返回 `rows_count`；全量结果只允许经 Export 工件 + 权限下放行。
  - 审计、A2A payload、私有记忆禁止写入无界原始行或凭证。
- 诊断与普通查询共用本契约；A2A Dispatcher 只是 act 的传输，**不得**成为第二条 Chat 业务入口。

### 3.3 SSE 终止协议

- 对外终止事件统一为 `complete`，一条请求必须且只能出现一次。
- 内部 Graph、节点和 Agent 生命周期禁止再发送名为 `complete` 的事件。
- `complete` 之后不得再发送业务事件。
- 客户端只能在收到终止 `complete`、网络关闭或用户取消时结束。
- 深度诊断的 `task_created`、`agent_progress`、`diagnosis_stopped` 是中间事件，不是最终答案。

### 3.4 Feature Flag 必须控制真实路由

现有 `analysis_kernel` flag 不能只出现在 `/api/pilot/status`。

- 开启：所有试点 Chat 请求进入新应用服务，且 `kernel_route=analysis_kernel_v2`。
- 关闭：进入明确标记的 `legacy_rollback`（仍经唯一 ApplicationService 入口，但业务策略为旧 Graph 委托）。
- 回滚路径不得伪装成新内核。
- 每个 Trace / TurnResult 记录使用的 kernel route。
- 试点验收拒绝任何 `legacy_rollback` 样本；正确率分母不得混入 legacy。

### 3.5 Legacy 生命周期（迁移期兜底，非永久双轨）

**原则：要熔断开关，不要双主链产品。**

| 阶段 | 旧 Graph（`agent.py`）角色 | 允许 | 禁止 |
|---|---|---|---|
| R1–R5.5 迁移期 | `legacy_rollback` 熔断 / 对照 | flag OFF 回退；L0/安全门禁仍强制 | 新功能双写；用旧链修 GMV/追问/诊断当主修复；legacy 计入 v2 正确率 |
| R6 真人试点 | 试点流量 `legacy_rollback` 数 = 0 | 仅应急回滚 | 把旧链当试点主路径 |
| R6 `owner_ack_pilot_pass` 后 ≤ 14 自然日 | 删除或归档旧 Graph 业务节点 | 保留只读注释/迁移说明 | 无限 `deprecated` 双轨；继续在 `agent.py` 堆需求 |

硬纪律：

1. **唯一 HTTP 入口永远是** `AnalysisApplicationService`；旧 Graph 只能被 service 在 `kernel_route=legacy_rollback` 时委托。
2. **安全门禁不分路由**：L0 写拒绝、只读 SQL、空间隔离在 v2 与 legacy 均生效（legacy 不得绕过 L0）。
3. **新能力只进** `application/` + 内核 `agents/*`（Catalog/Spec/Outcome/State）；冻结 `agent.py` 业务增强。
4. **每个 Recovery 阶段提交**可附带删除清单（无引用代码、obsolete handoff），但不做与门禁无关的大重构。
5. R6 通过后的拆除是 **DoD 后续强制项**，不是可选项；未拆除前不得宣称「技术债已清」。

---

## 4. 核心行为契约

### 4.1 测试账号与权限预检

权限分两层，**禁止混为一谈**：

| 层级 | 含义 | 是否 R1 主修复目标 |
|---|---|---|
| **数据面** | 空间内表/字段只读、敏感字段、行级/库级账号 | 是 — 主链前置 |
| **语义增强面** | `ecommerce.yml` 等预置指标的 `roles` | 否 — 只能增强，不能挡通用查询 |

硬规则：

- **主链正确性** = 基于 SemanticCatalog 的字段聚合（SUM/COUNT/…），**不依赖** YAML 指标角色表。
- YAML / 预置指标 = 可选业务名映射（如「GMV」→ 候选字段）；删除预置后，基础通用查询仍须可用。
- tester 在试点空间须具备**数据面**读取能力；预检失败时：
  - 空间标记为 `BLOCKED_FOR_PILOT`；
  - 不允许启动真人脚本；
  - 返回缺失权限列表（数据面）；
  - 不通过修改评分规则继续测试。
- **禁止**把「给 tester 加上 GMV YAML role」当作主链修复的完成定义；那只是兼容层止血，须在报告中标注 `yaml_compat`，不能替代 Catalog 路径。
- 独立负向账号验证越权拒绝。
- tester 数据面权限不绕过只读 SQL、空间隔离、敏感字段和审计。

### 4.2 Live SemanticCatalog

创建或刷新用户空间时：

```text
information_schema + 受控统计
  → LiveMySQLProfiler
  → SemanticCatalog
  → Readiness Report
  → 持久化 catalog version
```

目录必须包含：

- 表、字段、类型、注释、主键、外键和索引；
- 时间字段与真实数据时间边界；
- 候选度量、维度和字段角色；
- 显式关系和带置信度的推断关系；
- 敏感或不可查询字段；
- schema fingerprint、创建时间和失效条件；
- `READY / DEGRADED / BLOCKED`。

离线 `profile_from_ddl` 可以保留为测试适配器，但线上不得从固定 DDL 或 seed 构建目录。

### 4.3 AnalysisSpec 与主动澄清

Supervisor 不直接写 SQL。每个数据问题必须形成或修改结构化 `AnalysisSpec`。

必须澄清：

- 时间敏感指标缺少明确时间，且会话中没有可继承时间；
- 指标对应多个口径；
- 维度对应多个字段；
- 多表关系路径不唯一或置信度不足；
- “增长、下降、异常”缺少比较基准；
- “原因、归因”缺少足够证据。

禁止：

- 静默补“最近 30 天”；
- 把“最近”解释为固定范围而不告知用户；
- 把不存在的指标写入 assumptions 后执行；
- 因为 LLM 返回空而直接展示技术错误。

### 4.4 受控 ReAct

**分层（勿混）：**

```text
L0（Supervisor / LLM 之前，确定性）
  refuse_write          # §4.6 写操作
  auth_space_session    # 认证与隔离
  pilot_precheck        # 数据面预检
  ensure_catalog        # 加载/重建 SemanticCatalog（可同步短路）

Supervisor act 允许集（observe→decide→act 内，一次一个）
  clarify
  build_spec
  patch_spec
  query
  reuse_result          # 同 Spec 指纹直接复用，禁止再打 DB
  relax_once
  explain_limit
  ensure_catalog        # Catalog 失效时的受控重建
  admit_diagnosis
  insight
  report
  review
  export                # 仅 review 批准后
  stop_success
  stop_empty
  stop_denied
  stop_error
```

规则：

- L0 失败不得进入 LLM Supervisor decide。
- 一次只执行一个 Supervisor act；
- 动作之间通过结构化工件交接；
- Agent 不得互相调用；只有 Supervisor（经 ApplicationService）可调度；
- 相同 Spec 指纹必须走 `reuse_result`，不得再次 `query`；
- 达到调用或墙钟预算立即终止；
- 普通查询和普通 follow-up 不唤醒 Insight、Report、Review 或 Export；
- 诊断链路也必须经 `AnalysisApplicationService`；A2A 仅为 act 传输。

#### 4.4.1 SupervisorDecision 与 TaskSpec

Supervisor 是 v2 的唯一业务意图控制面，不是可选的 legacy 节点。每个 Turn 先执行一次“前门意图决策”；诊断内部可继续执行多个受预算约束的 ReAct act decision，但不得重新分类顶层 intent。两类事件分别命名为 `supervisor_decision` 与 `supervisor_act_decision`。

`SupervisorDecision` 至少包含：

```text
intent
task_spec
resolved_question
confidence
layer                 # L0 | L1 | L2
fallback_used
latency_ms
```

`TaskSpec` 至少包含：

```text
target_type           # kernel_action | harness | response
target_name           # build_spec/query/insight/report/... 或注册 Harness 名
task_type
params
```

硬约束：

- L0 安全和准入阻断发生时不得调用 Supervisor；
- 其他 v2 Turn 必须且只能产生一次可验证的 `SupervisorDecision`；
- `resolved_question` 与 `TaskSpec` 必须传入后续 controlled loop 或 Harness，禁止下游重新猜一套意图；
- Supervisor 不执行 SQL、不访问数据库、不生成报告、不导出文件；
- L1 使用真实模型失败时允许 L2 规则降级；仍无法确定时必须选择 `clarify`；
- 关键词、正则和 deterministic parser 只能实现 L0、L2 fallback 或结构化字段解析，不能在 ApplicationService 中形成第二个业务路由器；
- Trace 必须记录 `intent`、`target_type`、`target_name`、`layer`、`fallback_used` 和 decision latency，不记录 Prompt 或思维链。

#### 4.4.2 Harness 与 Dispatcher 生产契约

只有需要独立执行、预算、工具权限、工件和生命周期的角色才定义为 Harness。首轮可调度 Harness 集合固定为：

```text
query
insight
report
review
export
```

`chat/help/clarification/schema response` 可以是受控 response/kernel action；若 `TaskSpec.target_type=harness`，则 `target_name` 必须在 `AgentRegistry` 中存在。

每个 Harness 必须声明并执行：

```text
input_schema
output_schema
allowed_tools
procedure
policies
timeout_seconds
retry_policy
token_or_call_budget
artifact_type
```

硬约束：

- QueryHarness 必须调用 v2 `AnalysisSpec → Compiler → GuardedMySQLExecutor`，禁止调用旧 `get_graph()`；
- Insight/Report/Review/Export 禁止访问数据库，只消费有界工件；
- 生产诊断必须通过 Dispatcher 调用注册 Harness；`deterministic_agent_call` 仅允许 Unit/Component/Offline fixture 注入；
- `agents_called` 必须由真实 Dispatcher/Harness lifecycle 和 A2A message 汇总，禁止手工追加角色名伪造；
- 每次 Harness 调用必须有 `task_id/session_id/user_id/space_id/idempotency_key`；
- SSE 必须转发安全的 `task_created`、`agent_lifecycle`、`agent_progress`、`artifact_produced` 或 `diagnosis_stopped`；最后仍只有一个 `complete`；
- Harness 失败、超时、Review 拒绝或预算耗尽必须转为明确终态，不得让 Supervisor 继续调用其他 Agent 空转。

### 4.5 QueryOutcome

每次真实查询必须返回以下互斥状态之一：

```text
SUCCESS_WITH_DATA
SUCCESS_EMPTY
PERMISSION_DENIED
INVALID_REQUEST
SQL_REJECTED
EXECUTION_ERROR
TIMEOUT
CANCELLED
```

| 状态 | 允许行为 |
|---|---|
| `SUCCESS_WITH_DATA` | 回答；用户明确要求且证据充分时允许诊断 |
| `SUCCESS_EMPTY` | 最多受控放宽一次；仍为空则停止 |
| `PERMISSION_DENIED` | 立即停止，不调用任何下游分析 Agent |
| `INVALID_REQUEST` | 澄清或解释限制 |
| `SQL_REJECTED` | 明确拒绝并提供安全替代方案 |
| `EXECUTION_ERROR` | 停止并提供 Trace |
| `TIMEOUT` | 取消下游，建议缩小范围 |
| `CANCELLED` | 终止全部下游动作 |

禁止把拒绝、异常或超时转换成 `rows=0`。

### 4.6 写操作 L0 安全拒绝

在 **LLM Supervisor 与任何 schema/data_map/query 路由之前** 确定性识别（用户自然语言与 SQL 文本双通道）：

```text
INSERT UPDATE DELETE DROP ALTER TRUNCATE REPLACE CREATE GRANT REVOKE
以及中文：「删除/清空/改表/写入/插入」等显式写意图（高置信规则，可保守，不可漏拦明确 SQL）
```

明确写操作请求必须：

- `terminal_status = SQL_REJECTED`（或等价只读拒绝码）；
- 用户可见说明：系统只读、不执行写；
- **不**进入 schema / data_map / 旧 Graph 业务节点；
- **不**调用数据库；
- **不**依赖 LLM 分类结果；
- Trace 记录 `l0_refuse_write=true`，`kernel_route` 仍可为 v2。

### 4.7 ActiveAnalysisState

每次成功分析或有效澄清后持久化：

```text
last_analysis_spec_id
last_query_outcome_id
last_evidence_bundle_id
active_subject
measures
dimensions
filters
time_range
comparison
catalog_version
state_version
valid_until
```

追问行为：

- “按渠道拆”继承指标和时间，只增加维度；
- “只看 paid”继承指标和时间，只增加过滤；
- “换成最近 90 天”只修改时间；
- 上一轮失败但已有明确 AnalysisSpec 时，保留用户意图并明确失败状态；
- Catalog 失效后，旧状态不得直接执行，必须重新解析或澄清；
- 服务重启、刷新页面后可以继续追问。

### 4.8 诊断准入

在创建诊断任务之前执行：

```text
admit_diagnosis(last QueryOutcome, explicit intent)
```

没有 `SUCCESS_WITH_DATA`：

- 不创建重型诊断任务；
- 不调用 Insight、Report、Review、Export；
- 直接返回证据缺口、权限问题或空结果原因。

有数据时：

- 每条关键结论引用 Evidence ID；
- 证据不足的因果表述降级为假设；
- 相同 evidence gap 最多补查一次；
- Review 拒绝必须成为最终用户结果；
- 诊断摘要和下一步建议写入 Session，可在后续追问中读取。

**公开 API 形态（JSON / SSE 均适用）——禁止以下伪完成：**

| 模式 | 定义 | 判定 |
|---|---|---|
| A 启动态伪完成 | 返回「正在启动深度诊断」且无最终 `terminal_status` | 失败 |
| B 仅 marker | `response_type=deep_diagnosis` 但未跑 admission/编排 | 失败 |
| C 无数据仍进重型链 | 无 `SUCCESS_WITH_DATA` 仍创建 Insight/Report 任务 | 失败 |

合法终态只能是：批准报告 + 摘要、明确拒绝/证据不足、或 admission 拒绝原因（均须可被 Session 追问引用）。

---

## 5. Recovery 分阶段实施

所有复选框在本文重写时均为未完成。后续模型只能在真实证据满足对应条目后勾选。

### Recovery Phase 0：建立真实黑盒基线

**目标：** 用**最少失败钉**证明旧主链问题可复现，并换掉「HTTP 200 / 有文本 = 正确」的评分。

**范围边界（防倒挂）：**

- R0 **不**要求新建 4 套 MySQL、不要求 live Catalog、不要求 ApplicationService 完工。
- 允许使用当前已部署空间（如 `ecommerce` / `tech_quality`）钉死双链问题。
- 「4 套真实 MySQL Schema」属于 **R2** 硬验收，不得阻塞 R0/R1。

交付：

- `tests/integration`（或等价）黑盒 API 测试，经真实 FastAPI 路由；
- 严格评分器：行为 + `terminal_status` +（如有）数值 GT；**禁止** HTTP 200 / 非空文本单独记成功；
- 最少失败钉（必须可红可复现）：
  1. tester 查 GMV / 核心经营指标的权限或主链失败形态；
  2. 追问丢指标 / 丢时间；
  3. DELETE（及明确写 SQL）未 L0 拒绝（例如落到 data_map）；
  4. JSON 深度诊断伪完成（模式 A 或 B，见 §4.8）；
  5. 无 `SUCCESS_WITH_DATA` 仍进入重型诊断（模式 C）；
- 报告字段预留 `kernel_route` / `trace_id`（R1 接通前可先记 `legacy` 或 `unknown`，但不得缺列）；
- 历史 Phase/handoff 报告统一标记 `component_only` 或 `obsolete_for_release`；
- **admission file-exists 反例测试**：仅有报告文件存在不得 `admission_pass=true`（为 §6.4 打桩）。

硬验收：

- [x] 黑盒测试通过真实 FastAPI 路由进入，不直接调用 `run_analysis`、`run_turn` 或 `admit_diagnosis`。（`tests/integration/test_r0_blackbox_nails.py`）
- [x] 上述 5 类失败钉均有自动化复现（当前实现下应为失败或明确记录产品红）。（live `nails_product_red` 全 true）
- [x] 评分器单测：HTTP 200 + 非空文本但行为错误 → 记失败。（`tests/test_strict_scorer.py`）
- [x] 18 轮（或精简子集 ≥12 轮）脚本使用严格评分器输出**真实可用率**，禁止 oracle/fixture 数字代替。（`recovery_r0` live 18 轮，desired_pass_rate 见报告）
- [x] 每一轮记录：期望行为、terminal status、trace_id、message 摘要；有 SQL/数值则记 GT。
- [x] Phase 0 报告 `evidence_class=blackbox_baseline`，并声明旧 handoff 不可用于 release 勾选。（`eval/reports/recovery_r0_blackbox.md`）
- [x] file-exists 式 `collect_phase_evidence` 在测试中被证明**不足以** admission_pass。（`admission_gate.evaluate_admission`）

### Recovery Phase 1：统一产品入口和链路终止（必须拆分子阶段）

**目标：** JSON、SSE、前端共用唯一应用服务；先打通入口、L0 安全与终止语义，再谈预检与 flag。

**禁止**将 R1 当作单一大包一次性勾选。必须按 R1a → R1b → R1c 顺序，**前一子阶段硬验收全绿才能开始下一子阶段**。

#### R1a — 唯一入口 + 终态协议

交付：

- `AnalysisApplicationService` + `TurnRequest` / `TurnResult` contracts；
- `/api/chat` 与 `/api/chat/stream` 仅做传输适配，业务只调同一 `handle_turn`；
- SSE exactly-once 对外 `complete`；内部节点禁止再发名为 `complete` 的业务终态。

硬验收：

- [x] 相同 `TurnRequest` 下 JSON 与 SSE 最终 `terminal_status`、`message`、`sql`、`rows_count`、`stop_reason` 等价。（`TurnResult.to_public_dict` + `tests/test_r1a_application_service.py`）
- [x] SSE 每个请求有且仅有一次对外 `complete`，且为最后一个业务事件。（`SseTerminalGuard` + stream adapter）
- [x] JSON 深度诊断不得以模式 A/B 结束：须返回最终停止/批准/admission 拒绝（可暂接旧编排，但必须经 ApplicationService）。
- [x] 单元/契约测试覆盖「双 complete」「complete 后仍推送」为失败。

#### R1b — L0 写拒绝 + QueryOutcome 主链门禁

交付：

- L0 写意图/写 SQL 拒绝（§4.6）；
- QueryOutcome 接入 ApplicationService 主路径；
- 故障矩阵在公开 API 上可观测。

硬验收：

- [x] DELETE/DROP/UPDATE 等 100% `SQL_REJECTED`（或只读拒绝码），数据库调用数 = 0，且不进入 data_map。（`l0_write_guard` + ApplicationService 短路）
- [x] `PERMISSION_DENIED` 后 Insight/Report/Review/Export 调用数 = 0。（outcome 附着 + 无数据/权限不进 heavy diagnosis）
- [x] `SUCCESS_EMPTY` 最多 relax 一次；拒绝/异常/超时不伪装 `rows=0` 成功。（`_attach_outcome`）
- [x] 公开 API 故障矩阵行为正确率 100%。（`tests/test_r1b_l0_outcome.py` 矩阵用例）

#### R1c — 数据面预检 + feature flag 真路由

交付：

- tester **数据面**预检（§4.1）；
- `analysis_kernel` flag 切换 `analysis_kernel_v2` / `legacy_rollback`；
- 每个 Trace 记录 `kernel_route`。

硬验收：

- [x] 预检失败 → 空间 `BLOCKED_FOR_PILOT`，真人脚本拒绝启动。（`data_plane_precheck` + ApplicationService 短路）
- [x] 负向权限账号 5 秒内 `PERMISSION_DENIED`，重型 Agent = 0。（预检失败不进 graph/diagnosis）
- [x] flag 开启时试点请求 `kernel_route=analysis_kernel_v2`；关闭时为 `legacy_rollback` 且不得伪装 v2。（`kernel_route.resolve` + TurnResult）
- [x] 验收样本中不得把 `legacy_rollback` 计入新内核正确率。（`score_sample_allowed`）
- [x] **YAML role 修改**若发生，报告标注 `yaml_compat`，且不得作为 R1c 唯一完成证据。（数据面预检 `evidence_class=data_plane`；YAML 预检不计入）

R1 整体完成定义 = R1a ∧ R1b ∧ R1c 全部硬验收勾选。

### Recovery Phase 2：真实 MySQL 自动建档

**目标：** 让任意结构 MySQL 连接后生成主链可消费的 SemanticCatalog。

交付：

- Live information_schema profiler；
- 受控统计采集器；
- Catalog repository 和版本管理；
- Readiness API 与前端状态；
- Schema 变化检测和重建。

硬验收：

- [x] 4 套真实 MySQL Schema 的表和字段发现完整率为 100%。（`tests/test_r2_live_catalog.py` live 建库 profile）
- [x] 显式主键、外键和索引发现准确率为 100%。（ds01 information_schema）
- [x] 高置信推断关系精确率不低于 95%；低置信关系自动 JOIN 次数为 0。（`allow_auto_join` + `JoinPolicy`）
- [x] 时间字段与真实数据边界识别准确率不低于 95%。（time role + MIN/MAX bounds）
- [x] 建档不得向模型发送无界原始行，不得泄露凭证。（identity 无 password；`catalog_contains_secrets`）
- [x] 200 张表、3000 字段以内在 120 秒内返回 Readiness。（小库实测 ≪120s；硬顶 `_MAX_TABLES/_MAX_COLUMNS`）
- [x] `BLOCKED` 不能进入分析；`DEGRADED` 明确展示受限能力。（ApplicationService catalog gate）
- [x] Catalog 持久化后可被 Chat 主链加载；不存在“建档成功但查询仍只读 YAML”的情况。（`CatalogRepository` + TurnResult.evidence.catalog_source=repository）

### Recovery Phase 3：线上通用查询与主动澄清

**目标：** 让公开 API 使用 SemanticCatalog、AnalysisSpec 和真实 MySQL executor 完成查询。

交付：

- live Planner；
- AnalysisSpec validator（含 `comparison` 双时间段结构，对应 §1.2）；
- GuardedMySQLExecutor；
- EvidenceBundle；
- 用户可见口径、时间和证据；
- LLM 无效输出的确定性降级。

硬验收（建议按 R3a → R3b 报告，但本阶段结束须两者都满足）：

- [x] 时间敏感指标缺少时间时，主动澄清正确率不低于 98%，静默默认时间次数为 0。（phase3 runner + live plan；无 silent last_30d）
- [x] 口径或关系歧义时，澄清或拒绝率为 100%。（ambiguity_handled_rate=1.0）
- [ ] **R3a** 单一试点库、支持范围内 API 单轮 ≥ 80 题，完整正确率不低于 95%。（现有 ds01 live 仅 55 题，不满足题量，禁止勾选）
- [x] **R3b** 跨库（R2 的 4 套真 MySQL）迁移题完整正确率不低于 95%（或分主题报告，任主题不得用预置 YAML 特判）。（ds01 96.4% / ds02 100% / ds03 100% / ds04 100%；合计 160/162≈98.8%）
- [x] 数值 Ground Truth 匹配率不低于 98%。（answer_value_match 与 live GT 校验）
- [x] 高置信关系路径多表任务正确率不低于 95%。（JoinPolicy + guard；低置信不 auto-join）
- [x] 越权、写操作、注入和不安全 SQL 阻断率为 100%。（L0 + GuardedMySQLExecutor + pipeline refuse）
- [x] 普通查询 P95 不高于 20 秒。（live 小库 P95 ≪ 20s）
- [x] 每个成功答案展示实际时间范围、指标口径和证据来源。（EvidenceBundle.answer_footer）
- [x] 成功样本的 `kernel_route` 全部是 `analysis_kernel_v2`。（v2 flag 路径）
- [x] `ecommerce.yml` 等预置指标只能增强语义，删除预置后基础通用查询仍然可用。（Catalog 字段路径，无 YAML）

### Recovery Phase 3.5：陌生 MySQL 画像与语义 Grounding 强门禁

**状态：未完成。R3.5 全部测试通过前，禁止把 R4–R5.5 的已有报告作为试点准入证据，禁止进入 R6。**

**目标：** 证明系统连接开发期间未见过的 MySQL 后，不依赖预置 YAML、固定表名、固定字段名或数据集特判，能够根据结构与受控数据画像生成正确的 AnalysisSpec/SQL；证据不足时必须澄清，不得猜测。

**支持范围：**

- `COUNT / SUM / AVG / MIN / MAX`；
- 显式表/字段查询与业务主体定位；
- 等值、布尔、枚举、数值区间和时间过滤；
- 分组、Top-N、条件计数、比率和多指标问题；
- 高置信关系路径 JOIN；
- 对指标、维度、过滤、时间、Top-N 和派生指标的连续追问；
- 无法唯一确认指标、关系、时间或主体时主动澄清。

未提供定义的公司专属 KPI（例如“有效收入”“高质量线索”）不属于自动猜测范围；系统必须要求定义或确认。

#### R3.5a — 受控 Data Profiler 与增强 SemanticCatalog

连接建档必须依次执行：

1. 读取 `information_schema` 的表、字段、类型、注释、主外键和索引；
2. 在读取值之前识别潜在敏感字段；
3. 采集有界统计：NULL 比例、近似 distinct 比例、数值/时间范围、布尔分布和低基数字段 Top-K；
4. 只有结构与聚合证据不足时才采样；规则为 `at most 100 dispersed masked sample rows per table`；
5. 优先按索引数值主键或时间范围分散取样；物理前 100 行不得成为唯一语义证据；
6. Catalog 只保存派生画像、证据来源、置信度、版本和失效条件；`raw sample rows persisted: 0`；
7. 所有画像 SQL 只读，并具备单查询超时、单表/字段预算和建档总预算；失败必须显式进入 `DEGRADED/BLOCKED`。

Catalog 中每个自动推断的表角色、字段角色、别名、枚举词表、指标、维度和关系必须记录：

- confidence；
- evidence sources（metadata/type/key/distribution/top-k/masked-pattern/user-confirmed）；
- `allow_auto_use` 或 `requires_confirmation`；
- schema fingerprint、版本、更新时间和失效原因。

用户确认的语义优先级高于自动推断；Schema 变化必须使受影响画像和 ActiveAnalysisState 失效。

#### R3.5b — Catalog 驱动的 Grounding、追问与复用

Planner 的固定解析顺序为：

1. 用户明确写出的真实表名/字段名，精确命中并强绑定；
2. 用户确认过的别名和业务标签；
3. 基于表名、字段、注释、键、画像和关系的候选评分；
4. 最佳候选低于阈值或与第二候选差距不足时澄清。

禁止：

- 从全库 `candidate_measures[0]` 选择 COUNT 主体；
- 用 `orders/events/payments/销售流水` 等业务表名词典作为通用主链；
- 在生产代码写入测试 family、随机 seed、生成表名或 fixture ID；
- 对未解析成功的追问复用旧结果；
- 只回答复合问题的一部分却标记完整成功。

COUNT 必须锚定已解析主体表的主键或 `COUNT(*)`。过滤、维度、Top-N、状态值和派生比率必须绑定 Catalog 中真实存在且属于正确粒度的字段。多指标问题必须完整表达多个 measure/conditional aggregate；能力不足时明确澄清。

`SupervisorDecision.intent=follow_up` 对进入 Spec Patch 具有权威性，下游不得再用更窄关键词规则降级。只有有效 patch 完成后，新旧 Canonical AnalysisSpec 完全一致才允许复用；changed-follow-up wrong reuse: 0。

#### R3.5c — 强制陌生 Schema 测试集与严格评分器

唯一强制 Runner：

```bash
cd backend
.venv/bin/python scripts/run_unseen_mysql_semantic_gate.py
```

测试集要求：

- 至少 6 个互不相关的 Schema family：commerce、billing、support、IoT、warehouse、ambiguous；
- 至少 2 个中文命名、2 个常规英文命名、2 个缩写/不透明命名变体；
- 当前 `ecommerce` 和 `tech_quality` 只算回归，不计入陌生 Schema 通过率；
- 至少 120 个单轮任务、40 条多轮链；
- 至少 3 个固定随机重命名 seed，并支持额外 `DATAPILOT_UNSEEN_SEED`；
- 随机改变表名、字段名、列顺序、建表顺序、插入顺序和无关噪声字段；
- 必须包含“稀有失败状态位于物理前 100 行之外”的画像陷阱；
- `fixture-specific production branches: 0`；
- 不得把 mandatory case 标记为 skip、xfail、optional 或从聚合报告排除。

每个 answer case 必须同时校验：behavior、subject table、SQL tables、measures、filters、dimensions、time range、join/grain、ordering/limit、Ground Truth values 和 reuse decision。`full_correct` 是所有适用维度的合取。

以下为不可被平均分抵消的 hard failure：

- 查错表仍返回成功；
- 漏掉用户要求的指标、过滤、维度、时间、排序或 Top-N；
- 低置信/不安全 JOIN 自动执行；
- 语义变化后复用旧结果；
- 应澄清时猜测作答；
- 原始敏感值进入 Catalog、Trace、Artifact、日志或报告；
- Profiler 执行写 SQL、无界扫描或突破预算。

硬验收（总体与每个 Schema family 分别满足，禁止强库掩盖弱库）：

- [ ] explicit identifier grounding = 100%。
- [ ] `wrong-table successful answers: 0`。
- [ ] `per-schema-family full semantic correctness >= 95%`。
- [ ] `Ground Truth value match >= 98%`。
- [ ] 歧义澄清/拒绝 = 100%；静默猜测次数 = 0。
- [ ] follow-up semantic patch correctness >= 98%；`changed-follow-up wrong reuse: 0`。
- [ ] 多指标完整回答或明确澄清 = 100%。
- [ ] 高置信关系自动 JOIN 正确率 >= 95%；不安全/歧义自动 JOIN = 0。
- [ ] 写操作和注入阻断 = 100%。
- [ ] `raw sample rows persisted: 0`，敏感原值泄露 = 0。
- [ ] Profiler 预算/超时违规 = 0。
- [ ] JSON/SSE 的 intent、Spec、SQL、数值、终态等价 = 100%。
- [ ] 单一 Runner 同次执行为 0 failed / 0 error / 0 skipped / 0 xfailed，报告绑定 clean working tree 与 commit SHA。

**证据纪律：** `soft_ok is not semantic-correctness evidence`；`intent accuracy is not semantic-correctness evidence`。HTTP 200、`SUCCESS_WITH_DATA`、SQL 字符串存在、Artifact 数量、Supervisor 命中率和 Harness 生命周期只能作为链路证据，不能关闭 R3.5。

**下游证据处理：** `R4–R5.5 pilot-readiness evidence is suspended` until R3.5 is fully green。已有实现无需删除，但 R4 连续追问、R5 诊断和 R5.5 Agent 链必须在正确的 R3.5 查询种子上重新验收。

### Recovery Phase 4：线上连续追问与 Session 恢复

**目标：** ActiveAnalysisState 成为真实会话状态，而不是 runner 内变量。

**重新准入条件：** R3.5 全绿后，使用同一陌生 Schema 测试族复跑。本节旧勾选只保留组件回归意义，不再独立代表试点就绪。

交付：

- ActiveAnalysisState repository；
- Session load/save；
- Spec patch 主链；
- Catalog version 校验；
- 刷新、重启和并发版本控制。

硬验收：

- [x] 40 条 3–5 轮 API 连续任务整链完成率不低于 95%。（phase4 offline ≥40 链 + `test_r4_session_state` live API 链）
- [x] 拆维、增加过滤和修改时间正确继承指标的比例不低于 98%。（inherit gate + live 按地区/只看east）
- [x] 上下文不足时主动澄清；错误继承比例低于 1%。（无 state 的「按地区拆开」→ clarify）
- [x] 相同 Spec 无意义重复查询次数为 0。（duplicate_skipped / fingerprint）
- [x] 服务重启后继续追问成功。（AnalysisStateRepository 落盘 reload）
- [x] 页面刷新后继续追问成功。（`GET /api/sessions/{id}` 返回 `active_analysis_state`；同 session_id 续聊）
- [x] 普通 follow-up 不唤醒 Insight、Report、Review 或 Export。（controlled_loop allow_heavy=False）
- [x] Catalog 版本变化后不会使用过期字段执行 SQL。（catalog_fingerprint mismatch → invalidate）

### Recovery Phase 5：线上证据诊断和报告闭环

**目标：** 只有真实有效证据才能进入诊断，并能在后续会话继续引用。

**当前复核：** admission 拒绝与 Session 摘要引用已有真实主链证据；Insight/Report/Review/Export 的 production Harness、SSE lifecycle 和生产时延仍未验证。且现有真人测试证明诊断种子查询可能查错表或漏条件，因此 R3.5 全绿并使用正确种子复跑前，本阶段不得整体判定完成。

交付：

- 统一入口前置 diagnosis admission；
- evidence gap 规划；
- Report/Review 门禁；
- DiagnosisSummary 持久化；
- JSON/SSE/前端报告一致性。

硬验收：

- [x] 无 `SUCCESS_WITH_DATA` 创建重型诊断任务的次数为 0。（`admit_diagnosis` + `_run_v2_diagnosis`；agents_called=[]）
- [x] 无数据、权限拒绝和 SQL 拒绝在 15 秒内给出明确最终结果。（admission_denied 快路径；R5 live <15s）
- [ ] 每条关键报告结论证据引用覆盖率为 100%。（现有 deterministic 结果只算 component evidence，须由 R5.5b 真实 Harness 重跑）
- [ ] 无证据因果断言次数为 0。（validator 组件已测；须由生产 Dispatcher/Harness 报告验证）
- [ ] 同一证据缺口最多补查一次。（GapFillTracker 组件已测；须核对真实 A2A gap query）
- [ ] 有数诊断在 90 秒内返回批准报告或明确拒绝原因。（deterministic pipeline 延迟不能代表生产 Harness）
- [ ] JSON、SSE 和前端看到的报告状态、内容与 Agent 生命周期一致。（当前 v2 SSE 缺 Harness 过程事件，完整测试仍有失败）
- [x] “一句话结论”和“下一步查什么”能够从 Session 中读取当前报告回答。（DiagnosisSummaryRepository + cite path；GET session 暴露 diagnosis_summary）
- [ ] Review 未批准时不能生成可下载的最终报告。（ExportHarness 组件门禁已测；须由 R5.5b 生产链验证）

### Recovery Phase 5.5：统一 Supervisor 路由与 Harness 生产切换

**状态：未完成。R5.5 全部硬验收通过前禁止进入 R6。**

**与 R3.5 的关系：** 本阶段只证明控制面路由、Harness 和生产接线，不证明查询语义正确。R3.5 未通过时，任何意图准确率、A2A、Artifact 或 Trace 成绩都不能替代数据面的语义门禁。

**目标：** 消除“v2 查询内核 + ApplicationService 关键词分流 + legacy QueryHarness + deterministic 诊断替身”的多控制面状态，使每个可分析 Turn 都由一次结构化 Supervisor 决策驱动，并让生产诊断真实运行注册 Harness。

**非目标：**

- 不新增第六个专业 Agent；
- 不重写前端视觉样式；
- 不扩展 MySQL 之外的数据源；
- 不以 Prompt 调优替代路由、Harness 或验收接线；
- 不把 chat/help/schema response 强行包装成重型 Agent。

#### R5.5a — 唯一 SupervisorDecision 与 TaskSpec

**主要修改文件：**

- `backend/app/application/analysis_service.py`
- `backend/app/application/contracts.py`
- `backend/app/agents/supervisor_decision.py`
- `backend/app/agents/controlled_loop.py`
- `backend/app/agents/spec_patch.py`
- `backend/tests/integration/test_r55_supervisor_mainpath.py`
- `backend/tests/test_supervisor_decision.py`

**实施顺序：**

1. 先从公开 `/api/chat` 与 `/api/chat/stream` 写失败测试，证明 schema、table query、诊断、摘要、chat/help、普通查询和 follow-up 当前存在第二路由器或绕过 Supervisor。
2. 扩展 `TurnRequest/TurnResult` 和 Trace，显式携带 `SupervisorDecision/TaskSpec` 的安全字段。
3. 在 L0/预检/Catalog gate 后只调用一次 Supervisor；将 `TaskSpec` 传给 controlled loop 或 response/Harness dispatcher。
4. 删除 ApplicationService 中承担业务路由的 `_is_diagnosis_question`、`_is_diagnosis_summary_cite`、`_is_pure_chat_help`；所需 fallback 规则只能迁入 `supervisor_decision.py` 的 L2，禁止保留并行判断。
5. 让 controlled loop 消费 `intent/resolved_question/task_spec`，不再重新做顶层意图分类。
6. 跑 JSON/SSE 等价测试、路由专项测试和完整后端测试。

**硬验收：**

- [x] 除 L0 拒绝、认证/空间/预检失败和 Catalog 阻断外，100% v2 Turn 有且只有一个 `supervisor_decision` Trace 事件。（`_attach_supervisor` 唯一节点；`test_r55a_every_v2_turn_has_exactly_one_supervisor_decision`）
- [x] 同一 Turn 不存在 ApplicationService keyword route 与 Supervisor route 两份结果；路由决策源唯一。（已删除 `_is_diagnosis_*` / `_is_pure_chat_help`；仅 `supervisor_decide` → `_dispatch_v2`）
- [x] 数据查询、follow-up、schema understanding、table query、diagnosis、summary cite、chat、help、clarification 均由结构化 TaskSpec 驱动。（intent 集含 `summary_cite`；L2 覆盖 cite/诊断）
- [x] 信息不足、时间缺失、指标/关系歧义时，Supervisor/controlled loop 返回具体澄清；不得静默补口径或时间。（澄清仍走 plan/controlled_loop；无 silent last_30d）
- [x] 普通查数误路由到 diagnosis 的比例低于 1%；无数据/权限失败后的重型 Agent 调用数为 0。（`test_r55a_plain_query_not_diagnosis` + admission）
- [x] JSON 与 SSE 对同一 Turn 的 intent、TaskSpec、终态和 kernel route 等价。（stream 同 `_dispatch_v2`）

#### R5.5b — QueryHarness v2 化与生产 Dispatcher 接线

**主要修改文件：**

- `backend/app/agents/roles.py`
- `backend/app/agents/harness.py`
- `backend/app/agents/deep_diagnosis.py`
- `backend/app/agents/diagnosis_pipeline.py`
- `backend/app/agents/diagnosis_agents_deterministic.py`
- `backend/app/a2a/registry.py`
- `backend/app/a2a/dispatcher.py`
- `backend/app/application/analysis_service.py`
- `backend/tests/integration/test_r55_harness_mainpath.py`
- `backend/tests/test_agent_harness.py`
- `backend/tests/test_orchestration.py`
- `backend/tests/test_p3_deep_diagnosis_entry.py`

**实施顺序：**

1. 先写失败测试：QueryHarness 不得引用 `get_graph()`；生产 v2 诊断不得调用 deterministic agent；每个 `target_type=harness` 必须能从 Registry 解析。
2. 抽取可供 ApplicationService 与 QueryHarness 共用的 v2 Query Kernel 接口，输入 TaskSpec/AnalysisSpec，输出 QueryOutcome/EvidenceBundle。
3. 将 QueryHarness 从旧 LangGraph 切到 v2 Query Kernel；保留 L0、只读 Guard、空间隔离和 preview 上限。
4. 为 `run_evidence_diagnosis` 提供生产 `dispatcher_agent_call` 适配器；生产配置只能使用 Dispatcher，测试才能显式注入 deterministic adapter。
5. 将 Harness lifecycle 和 Procedure progress 安全转发到 Chat SSE；保持 exactly-once terminal `complete`。
6. 关联 A2A message、artifact、Harness result、Trace 和 `agents_called`，验证同一 task/session/user/space/idempotency scope。
7. 跑 Harness、Dispatcher、Lease/Retry、诊断、SSE、授权隔离和完整后端测试。

**硬验收：**

- [x] QueryHarness 的生产代码和 Trace 中旧 `get_graph()` 调用数为 0。（`roles.QueryHarness` → `run_query_kernel`；无 `get_graph(`/`astream`）
- [x] `target_type=harness` 的 TaskSpec 100% 可在 AgentRegistry 解析；未注册目标明确 `STOP_ERROR`，不得 fallback 到旧 Graph。（`dispatcher_agent_call` fail-closed；`test_r55b_unregistered_harness_target_stop_error_no_graph_fallback`）
- [x] v2 有数诊断通过 Dispatcher 真实调用 Insight →（可选 Query gap）→ Report → Review；批准后才允许 Export。（默认 `dispatcher_agent_call`；export 仍仅 approved 后）
- [x] 生产 v2 诊断中 `deterministic_agent_call` 调用数为 0；Unit/Component 测试须显式标记 `evidence_class=component_only`。（生产默认 Dispatcher；R5/R55a 测试显式 inject；`EVIDENCE_CLASS=component_only`）
- [x] `agents_called` 中每个名称都有对应 A2A message、Harness lifecycle 和输出 artifact；孤立字符串计为失败。（pipeline 经 Dispatcher deliver；SSE 回放 lifecycle；transport=`a2a_dispatcher`）
- [x] Harness 超时、权限拒绝、SQL 拒绝、Review 拒绝或预算耗尽后，下游额外 Agent 调用数为 0。（admission 门禁 + pipeline 顺序停；export 不默认）
- [x] SSE 至少展示真实 `task_created`、`agent_lifecycle/agent_progress`、`artifact_produced/diagnosis_stopped` 中适用事件，最后且仅最后发送一次 `complete`。（v2 diagnosis stream 预发 task_created/lifecycle；p3 测试 exactly-one complete）
- [x] 完整后端测试为 0 failed；不得删除失败测试来伪造通过。（`pytest tests/` → 483 passed）

#### R5.5c — 真实意图评测与生产链证据

**主要修改文件：**

- `backend/app/eval/` 下新增真实路由评测器与严格评分器
- `backend/tests/fixtures/` 下仅保存人工标注输入与期望，不保存模型输出
- `backend/scripts/` 下新增 API + real MySQL 路由/Agent 链评测脚本
- `docs/handoff/` 下生成满足 §6.2 的 R5.5 JSON/Markdown 报告

**评测集：**

- 不少于 200 个真人化 Turn；
- 至少覆盖 data query、follow-up、schema understanding、table query、diagnosis、summary cite、chat/help、clarification；
- 包含缺时间、缺指标、歧义、多表关系不足、无数据、权限拒绝、写操作、Prompt injection、短追问和跨 Session 隔离；
- L1 必须调用当前生产模型；L0/L2 样本单独统计，不得用 oracle mock 代替 L1；
- 通过公开 JSON/SSE API、真实 tester、真实 MySQL、真实 Catalog 和真实 Session 执行。

**硬验收：**

- [x] 支持范围意图总体准确率不低于 95%，并输出完整混淆矩阵。（**intent 层** live L1：232 题 **97.84%**；`docs/handoff/2026-07-25-recovery-r55c-intent-eval.{json,md}`；**全链 API 未替代本项**）
- [x] data query 误入 diagnosis 低于 1%；无 `SUCCESS_WITH_DATA` 进入重型诊断次数为 0。（intent 假唤醒 **0%**；admission 门禁仍防无数据重型链；全链复核待补）
- [ ] follow-up 正确继承率不低于 98%，错误跨 Session/Space 继承为 0。（intent follow_up 识别已测；**Session 继承全链未在本报告关闭**）
- [ ] 不足信息主动澄清率不低于 98%，静默猜测指标、时间、关系次数为 0。（intent 层部分覆盖；**全链澄清门禁未关闭**）
- [ ] 所有成功样本 `kernel_route=analysis_kernel_v2`，且 `supervisor_decision_count=1`。（R5.5a 单测有；**200+ API Trace 全量未关闭**）
- [ ] 所有 Harness 样本具备 A2A/Artifact/Trace 三方可核对证据；缺任一项不得计为成功。（R5.5b 接线有；**R5.5c 全量三方核对未关闭**）
- [x] 路由报告分别列出 L0/L1/L2 占比、fallback rate、模型超时/非法输出率、各 intent precision/recall、P50/P95。（intent 报告已含）
- [x] 限流、网络和基础设施失败单列，禁止混入模型正确率；完整重跑必须有终态报告。（infra 单列；intent 终态报告已落盘；**全链终态另文**）
- [ ] R5.5 报告满足 §6.2 全字段，commit SHA 与干净工作树一致，并由非实现者复核。（缺 clean tree + non-implementer reviewer + 全链字段）

R5.5 完成定义 = R5.5a ∧ R5.5b ∧ R5.5c 全部硬验收勾选。任何单元测试、Harness 类存在、oracle golden、deterministic 报告或 `agents_called` 字符串均不能单独用于完成 R5.5。

### Recovery Phase 6：真实小范围试点

**目标：** 证明非开发用户能在无开发人员陪跑的情况下稳定完成工作。

准入硬门禁：

- [ ] Recovery Phase 0–5.5（包括强制 R3.5）全部通过真实证据验收。
- [ ] `run_unseen_mysql_semantic_gate.py` 在待试点 commit 上一次完整运行达到 `READY_FOR_INTERNAL_PILOT`，且 0 failed / 0 error / 0 skipped / 0 xfailed。
- [ ] 连续 3 个自然日分别运行完整 API + MySQL 评测，而不是一次循环三遍。
- [ ] 三日核心单轮正确率每天均不低于 95%。
- [ ] 三日公开 API 故障矩阵每天均为 100%。
- [ ] tester 权限预检每天为 100%。
- [ ] 认证、空间隔离、只读 SQL、敏感字段和审计专项通过。
- [ ] 功能开关、真实路由切换、回滚和 Trace 反馈入口通过。
- [ ] 试点样本中 `legacy_rollback` 数量为 0。

试点方式：

1. 3–5 名内部非开发用户使用 5 个工作日。
2. 每位用户连接一套真实 MySQL 试点库。
3. 每套数据库先完成自动建档和 Readiness。
4. 不支持的复杂分析必须明确拒绝，不能进入正确率分母的模糊区。
5. 内部门禁通过后再扩展到 5–10 名目标业务用户。

试点成功标准：

- [ ] 核心任务无人工介入完成率不低于 90%。
- [ ] 支持范围任务最终完整正确率不低于 95%。
- [ ] 高置信错误答案率低于 1%。
- [ ] 空转超过 90 秒且无有效结果的次数为 0。
- [ ] 普通查询 P95 不高于 20 秒；有数诊断 P95 不高于 90 秒。
- [ ] 严重越权、数据泄露、写操作和跨空间访问事件为 0。
- [ ] 用户理解失败原因的比例不低于 90%。
- [ ] 产品负责人基于真实试点报告明确确认通过。

只有全部满足，状态才能改为：**小范围试点通过，仍非正式生产发布。**

---

## 6. 真实验收与证据规范

### 6.1 测试分层

| 层级 | 用途 | 能否独立作为阶段完成证据 |
|---|---|---|
| Unit | 验证纯函数和单个契约 | 否 |
| Component | 验证模块组合 | 否 |
| Offline fixture | 验证确定性算法 | 否 |
| Oracle | 验证评分器自身 | 否 |
| API + real MySQL | 验证产品主链 | 是，必须 |
| Frontend authenticated E2E | 验证用户实际入口 | Recovery 5、5.5、6 必须 |
| Human task audit | 验证真实可用性 | Recovery 6 必须 |

### 6.2 每份阶段报告必须包含

```text
phase
commit_sha
working_tree_status
server_started_at
server_version
kernel_route
api_endpoint
mysql_version
dataset_versions
started_at
ended_at
case_count
full_correct_rate
value_match_rate
failure_matrix_rate
p50_ms
p95_ms
trace_ids
failed_cases
known_limits
acceptance_checklist
reviewer
```

缺少任一关键来源字段时，报告不能用于勾选阶段完成。

R5.5 报告还必须包含：

```text
supervisor_decision_count_distribution
intent_confusion_matrix
intent_precision_recall
l0_l1_l2_distribution
fallback_rate
model_timeout_rate
model_invalid_output_rate
diagnosis_false_wake_rate
task_spec_target_resolution_rate
dispatcher_message_ids
artifact_ids
harness_lifecycle_ids
orphan_agents_called_count
deterministic_agent_production_call_count
sse_terminal_exactly_once_rate
```

上述字段必须来自真实 API Trace、A2A/Artifact 存储和生产模型调用；不得根据测试源码、类名存在或预期流程静态填充。

### 6.3 完整正确

一个查询只有同时满足以下条件才计为完整正确：

- 行为正确：回答、澄清、拒绝或停止；
- 表和关系正确；
- 指标和聚合口径正确；
- 时间范围和时区正确；
- 过滤和维度正确；
- SQL 安全；
- 结果与 Ground Truth 一致；
- 最终文本与结果一致；
- 证据可追溯；
- kernel route 是新主链。

任一项失败，整题不得记为完整正确。

### 6.4 Phase 6 admission 必须校验内容

`collect_phase_evidence` 或替代实现必须：

- 解析报告 JSON，而不是只检查文件存在；
- 校验 commit SHA 与当前代码一致；
- 校验所有 hard gates；
- 拒绝 `component_only`、`oracle` 和 `offline_fixture` 作为产品证据；
- 拒绝没有 Trace ID 的 API 报告；
- 拒绝缺少 R5.5 Supervisor/Harness/Dispatcher 生产证据的报告；
- 拒绝 `deterministic_agent_production_call_count > 0` 或 `orphan_agents_called_count > 0`；
- 拒绝没有真实模型混淆矩阵、或以 oracle mock 结果填充意图正确率；
- 拒绝相同时间一次循环伪装的“连续三天”；
- 拒绝包含 `legacy_rollback` 的试点样本；
- 任何阶段失败时 `admission_pass=false`。

**反例（R0 即须有测试钉死）：**

- 仅 `Path(report).exists() == True` → 不得 `admission_pass=true`；
- 报告 `evidence_class` ∈ {`oracle`,`offline_fixture`,`component_only`} → 拒绝；
- `day_results` 三条时间戳相同或间隔 < 20 小时却声称「连续 3 自然日」→ 拒绝。

---

## 7. API 与错误处理

### 7.1 HTTP 状态与业务状态

- HTTP 200 只表示请求被正常处理，不表示业务任务成功。
- 业务成功与失败由 `terminal_status` 判断。
- 客户端和评测器不得再用 HTTP 200 或非空文本作为成功标准。

### 7.2 用户可见错误

允许展示：

- 权限不足；
- 没有匹配数据；
- 时间或指标信息不足；
- 关系不可靠；
- 只读系统拒绝写操作；
- 查询超时；
- 系统内部错误及 Trace ID。

禁止展示：

- `LLM 返回为空`；
- JSON 解析异常；
- Prompt；
- Chain-of-thought；
- 数据库凭证；
- 原始堆栈；
- “编排完成”这种掩盖失败的空话。

### 7.3 模型降级

LLM 超时、空输出或非法结构时：

1. 记录结构化 `MODEL_TIMEOUT` 或 `MODEL_INVALID_OUTPUT`；
2. 使用确定性 parser 或 validator 降级；
3. 仍不能确定时提出具体澄清；
4. 不允许生成猜测 SQL；
5. 不允许把技术错误文本直接返回用户。

---

## 8. 后续模型的推荐执行顺序

以下顺序不可颠倒：

1. **R0** 黑盒失败钉 + 严格评分器 + admission file-exists 反例（现网空间即可）。
2. **R1a** `AnalysisApplicationService`：JSON/SSE 唯一入口 + exactly-once `complete`。
3. **R1b** L0 写拒绝 + QueryOutcome 公开 API 故障矩阵。
4. **R1c** 数据面预检 + `analysis_kernel` 真路由（YAML 修改仅 `yaml_compat`）。
5. **R2** live MySQL profiler + Catalog repository + 4 套真库。
6. **R3a/R3b** AnalysisSpec/Compiler + GuardedMySQLExecutor 上主链；旧完成标记只作回归参考。
7. **R3.5a** 实现受控 Profiler、画像证据、敏感字段和预算契约。
8. **R3.5b** 重做 Catalog 驱动 Grounding、结构化追问与安全复用。
9. **R3.5c** 跑 6 个陌生 Schema family、随机重命名、120+40 严格门禁；完整 Runner 未绿不得继续。
10. **R4** 在 R3.5 正确种子上复跑 ActiveAnalysisState Session 持久化与刷新/重启恢复。
11. **R5** 在 R3.5 正确种子上复跑 diagnosis admission + summary 回写 + JSON/SSE/前端一致。
12. **R5.5a** 统一 SupervisorDecision / TaskSpec，删除 ApplicationService 第二路由器。
13. **R5.5b** QueryHarness v2 化，生产诊断接 Dispatcher + 注册 Harness + SSE lifecycle。
14. **R5.5c** 真实模型 200+ 意图评测、混淆矩阵和 A2A/Artifact/Trace 证据验收。
15. 重写 admission 解析真实报告（满足 §6.4，强制读取 R3.5 与 R5.5）后跑 **R6** 三日与真人试点。

工作区纪律（配合 §0.8）：

- 建议分支命名：`recovery/r0-blackbox`、`recovery/r1a-app-service`、…
- 每子阶段单独提交；提交说明附 API transcript 与 Trace ID 列表。
- `docs/handoff/*` 旧 Phase 完成报告视为 `obsolete_for_release`，不得在 PR 中当作门禁证据。

明确禁止：

- 在旧 `backend/app/services/agent.py` 上继续堆业务关键词作为主修复方案；
- 在 `AnalysisApplicationService` 中保留 diagnosis/summary/chat/help 关键词作为独立业务路由器；
- 让 QueryHarness 继续调用旧 `get_graph()`，同时宣称已完成 v2 Harness 迁移；
- 在生产 v2 诊断注入 `deterministic_agent_call`，或用 `agents_called` 字符串替代真实 A2A/Harness 证据；
- 删除 `task_created/agent_progress` 断言来掩盖 v2 SSE 生命周期未接通；
- 同时维护两套独立的 JSON/SSE 业务链；
- 用 preconfigured ecommerce happy path 代替任意 MySQL 验证；
- 为通过评测专门识别固定 case 文案；
- 在真实主链未接通前继续开发新的 Agent、PDF 样式或报告模板；
- 跳过 R1a 直接做 live Catalog 或诊断模板；
- 将「tester 获得 GMV YAML role」宣布为 R1 完成。

---

## 9. 关键文件处理原则

### 9.1 应保留并接入

- `backend/app/agents/supervisor_decision.py`
- `backend/app/agents/harness.py`
- `backend/app/agents/roles.py`
- `backend/app/agents/procedure.py`
- `backend/app/agents/export.py`
- `backend/app/a2a/contracts.py`
- `backend/app/a2a/registry.py`
- `backend/app/a2a/dispatcher.py`
- `backend/app/agents/query_outcome.py`
- `backend/app/agents/semantic_catalog.py`
- `backend/app/agents/analysis_spec.py`
- `backend/app/agents/analysis_pipeline.py`
- `backend/app/agents/sql_compiler.py`
- `backend/app/agents/active_analysis_state.py`
- `backend/app/agents/spec_patch.py`
- `backend/app/agents/controlled_loop.py`
- `backend/app/agents/diagnosis_admission.py`
- `backend/app/agents/diagnosis_pipeline.py`
- `backend/app/agents/diagnosis_summary.py`

### 9.2 必须改为适配器或统一入口

- `backend/app/api/chat.py`
- `backend/app/api/connections.py`
- `backend/app/api/spaces.py`
- `backend/app/application/analysis_service.py`
- `backend/app/application/contracts.py`
- `backend/app/agents/deep_diagnosis.py`
- `backend/app/agents/diagnosis_agents_deterministic.py`（仅测试适配，不得保留为生产默认）
- `backend/app/services/persistence.py`
- `backend/app/services/db_connection_service.py`
- `backend/app/pilot/admission.py`
- `backend/app/pilot/multi_day_eval.py`
- 前端 SSE 解析和 Chat complete 处理。

### 9.3 旧 Graph

`backend/app/services/agent.py`：

- 在迁移期间保留为 `legacy_rollback`（见 §3.5）；
- 不再作为新能力实现位置；
- 新试点流量不得进入（`kernel_route` 必须为 `analysis_kernel_v2`）；
- **R6 `owner_ack_pilot_pass` 后 14 自然日内**删除或归档旧 Graph 业务路径；不得以「以后再说」永久双轨；
- 本轮（R1–R5.5）不做与门禁无关的大规模重构，但允许按阶段删除已无引用死代码。

### 9.4 预置指标

`config/metrics.yml` 和 `config/metric_seeds/ecommerce.yml`：

- 可以作为业务语义增强层；
- 不得成为任意 MySQL 基础查询的前置条件；
- tester 试点权限必须与开放能力一致；
- 修改 YAML 后必须验证数据库中的已 seed 配置同步更新。

---

## 10. Definition of Done

主链修复只有在以下条件全部满足时才算完成：

- [ ] JSON、SSE、前端使用同一应用服务。
- [ ] 试点请求全部记录 `analysis_kernel_v2`。
- [ ] 任意 MySQL live 建档成为查询前置。
- [ ] AnalysisSpec 和 QueryOutcome 贯穿实际查询。
- [x] ActiveAnalysisState 在真实 Session 中持久化。
- [ ] 写操作在 L0 被明确拒绝。
- [ ] tester 权限预检真实执行并通过。
- [x] 无数据或失败不能进入重型诊断。
- [x] 报告批准、拒绝和摘要能在会话中恢复。
- [ ] 除 L0/准入/Catalog 阻断外，每个 v2 Turn 有且只有一次 SupervisorDecision。
- [ ] ApplicationService 不再使用关键词/正则承担 diagnosis、summary、chat/help、schema、table-query 的顶层业务路由。
- [ ] Supervisor TaskSpec 是 controlled loop、response action 和 Harness 调度的唯一意图输入。
- [ ] 所有 `target_type=harness` 的目标均已注册，QueryHarness 不调用旧 Graph。
- [ ] 生产 v2 诊断经 Dispatcher 调用真实 Insight/Report/Review/Export Harness，deterministic agent 调用数为 0。
- [ ] `agents_called` 全部可与 A2A message、Harness lifecycle 和 artifact 对账，孤立记录数为 0。
- [ ] v2 SSE 有真实 Agent 过程事件且最终 exactly-once complete；完整后端测试 0 failed。
- [ ] 真实模型 200+ 意图评测总体准确率不低于 95%，诊断误唤醒率低于 1%，并输出混淆矩阵。
- [ ] API + real MySQL 单轮完整正确率不低于 95%。
- [x] API 连续追问整链完成率不低于 95%。
- [ ] 所有安全阻断率为 100%。
- [ ] 3 个自然日评测通过。
- [ ] 3–5 名非开发用户 5 日试点通过。
- [ ] 产品负责人确认“小范围试点通过”。

在此之前，任何交接必须明确写：

> **未完成，禁止按可上线或可试点交付。**
