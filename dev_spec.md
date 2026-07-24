# DataPilot 主链迁移与小范围试点 Development Specification

> **当前状态：未完成，禁止按可上线或可试点交付。**
>
> 本文件是当前修复工作的唯一产品、架构、实施顺序和验收权威来源。历史 Phase 0–6 报告、旧 handoff、离线 benchmark、测试数量或对话与本文件冲突时，以本文件为准。
>
> 2026-07-24 已确认：后端 `385 passed`，但真实 `/api/chat` 仍稳定出现 GMV 权限拒绝、追问丢上下文、DELETE 被路由成数据地图等问题。根因是新分析内核主要运行在测试和离线 runner，线上仍执行旧 LangGraph 主链。旧阶段的完成标记全部失效，必须按本文重新验收。

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
- 现有授权、只读 SQL、空间隔离、审计、会话和前端基础。

### 2.2 当前线上实际执行链

```text
/api/chat 或 /api/chat/stream
  → backend/app/services/agent.py 的旧 LangGraph
  → 旧 Supervisor 路由
  → metric YAML / 旧 parser / 旧 working_memory
  → 旧 SQL generator / Query executor
```

新 `SemanticCatalog → AnalysisSpec → Controlled Loop → ActiveAnalysisState` 尚未成为线上唯一主链。

### 2.3 已复现的线上问题

| 问题 | 已确认原因 |
|---|---|
| tester 查询 GMV 被拒绝 | `ecommerce.yml` 的 GMV roles 不含 tester；预检未成为真实准入门禁 |
| “只看 paid”“按渠道拆”丢指标 | ActiveAnalysisState 只在离线 runner 使用，Chat Session 未持久化和恢复 |
| 任意 MySQL 建档答非所问 | Profiler 主要解析离线 DDL，未形成 live information_schema → SemanticCatalog 主链 |
| DELETE 被路由成 data_map | 写操作没有模型前的确定性 L0 拒绝 |
| JSON 报告只返回“正在启动” | `/api/chat` 与 `/api/chat/stream` 业务语义不一致，完整编排只接在 stream |
| `LLM 返回为空` | 旧 parser 的技术失败直接暴露，没有结构化降级 |
| 385 测试通过但真人失败 | 测试集中验证组件和 runner，没有从公开 API 进入新内核 |

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
4. Supervisor controlled observe → decide → act
5. Build or patch AnalysisSpec
6. GuardedMySQLExecutor executes compiled SQL
7. Produce QueryOutcome + EvidenceBundle
8. Persist ActiveAnalysisState and artifacts
9. Assemble answer or run admitted diagnosis
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

### 3.2 统一输入输出契约

`TurnRequest` 至少包含：

```text
question
user_id
user_role
space_id
session_id
workspace_id
selected_metric
selected_query_type
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
rows
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

### 3.3 SSE 终止协议

- 对外终止事件统一为 `complete`，一条请求必须且只能出现一次。
- 内部 Graph、节点和 Agent 生命周期禁止再发送名为 `complete` 的事件。
- `complete` 之后不得再发送业务事件。
- 客户端只能在收到终止 `complete`、网络关闭或用户取消时结束。
- 深度诊断的 `task_created`、`agent_progress`、`diagnosis_stopped` 是中间事件，不是最终答案。

### 3.4 Feature Flag 必须控制真实路由

现有 `analysis_kernel` flag 不能只出现在 `/api/pilot/status`。

- 开启：所有试点 Chat 请求进入新应用服务。
- 关闭：进入明确标记的 `legacy_rollback`。
- 回滚路径不得伪装成新内核。
- 每个 Trace 记录使用的 kernel route。
- 试点验收拒绝任何 `legacy_rollback` 样本。

---

## 4. 核心行为契约

### 4.1 测试账号与权限预检

- tester 在试点空间拥有全部已开放指标和业务数据读取权限。
- 预检必须读取真实部署配置或数据库中的指标权限，不得使用测试内构造的 role map。
- 预检失败时：
  - 空间标记为 `BLOCKED_FOR_PILOT`；
  - 不允许启动真人脚本；
  - 返回缺失权限列表；
  - 不通过修改评分规则继续测试。
- 独立负向账号验证越权拒绝。
- tester 全权限不绕过只读 SQL、空间隔离、敏感字段和审计。

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

Supervisor 的动作只能来自：

```text
clarify
build_spec
patch_spec
query
relax_once
explain_limit
admit_diagnosis
insight
report
review
stop_success
stop_empty
stop_denied
stop_error
```

规则：

- 一次执行一个动作；
- 动作之间通过结构化工件交接；
- Agent 不得互相调用；
- 相同 Spec 指纹不得重复查询；
- 达到调用或墙钟预算立即终止；
- 普通查询和普通 follow-up 不唤醒 Insight、Report、Review 或 Export。

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

在 LLM Supervisor 之前确定性识别：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
REPLACE
CREATE
GRANT
REVOKE
```

明确写操作请求必须：

- 返回 `SQL_REJECTED`；
- 说明系统只读；
- 不进入 schema/data_map；
- 不调用数据库；
- 不依赖 LLM 分类结果。

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

---

## 5. Recovery 分阶段实施

所有复选框在本文重写时均为未完成。后续模型只能在真实证据满足对应条目后勾选。

### Recovery Phase 0：建立真实黑盒基线

**目标：** 把已知问题固化成从公开 API 进入的可复现失败用例，停止“组件绿、产品红”。

交付：

- `tests/integration` 或等价目录下的黑盒 API 测试；
- 4 套真实 MySQL 测试 Schema；
- 当前 18 轮真人脚本的严格评分器；
- 新旧 kernel route 观测字段；
- 历史报告标记为 `component_only` 或 `obsolete_for_release`。

硬验收：

- [ ] 黑盒测试通过真实 FastAPI 路由进入，不直接调用 `run_analysis`、`run_turn` 或 `admit_diagnosis`。
- [ ] 测试使用真实 MySQL fixture，不使用 SQLite executor。
- [ ] tester GMV、追问继承、DELETE 拒绝、JSON/SSE 报告一致性、无数据诊断、任意 MySQL 建档均有失败复现。
- [ ] 18 轮脚本不再用 HTTP 200 或“有文本”记为正确。
- [ ] 每一轮记录期望行为、Ground Truth、terminal status、kernel route 和 Trace ID。
- [ ] Phase 0 报告明确当前真实业务可用率，禁止使用 oracle 数字代替。

### Recovery Phase 1：统一产品入口和链路终止

**目标：** JSON、SSE 和前端全部经过唯一应用服务；先解决双链、权限、写操作和终止状态。

交付：

- `AnalysisApplicationService` 和统一 contracts；
- JSON/SSE 传输适配器；
- `analysis_kernel` flag 真实路由；
- tester 真实权限预检；
- QueryOutcome 主链桥接；
- L0 写操作拒绝；
- exactly-once SSE terminal。

硬验收：

- [ ] `/api/chat` 与 `/api/chat/stream` 对相同请求返回等价 terminal status、message、SQL、rows 和 stop reason。
- [ ] 两个入口都记录 `kernel_route=analysis_kernel_v2`。
- [ ] SSE 每个请求只出现一次 `complete`，且它是最后一个业务事件。
- [ ] JSON 深度诊断不会返回“正在启动”，而是等待并返回最终停止或批准结果。
- [ ] tester 对试点空间能力预检为 100%；否则试点被阻止。
- [ ] 负向权限账号在 5 秒内 `PERMISSION_DENIED`，下游重型 Agent 调用数为 0。
- [ ] DELETE 等写操作 100% 返回 `SQL_REJECTED`，数据库调用数为 0。
- [ ] `SUCCESS_EMPTY` 最多放宽一次；拒绝、异常和超时不伪装为空结果。
- [ ] 公开 API 故障状态矩阵行为正确率为 100%。

### Recovery Phase 2：真实 MySQL 自动建档

**目标：** 让任意结构 MySQL 连接后生成主链可消费的 SemanticCatalog。

交付：

- Live information_schema profiler；
- 受控统计采集器；
- Catalog repository 和版本管理；
- Readiness API 与前端状态；
- Schema 变化检测和重建。

硬验收：

- [ ] 4 套真实 MySQL Schema 的表和字段发现完整率为 100%。
- [ ] 显式主键、外键和索引发现准确率为 100%。
- [ ] 高置信推断关系精确率不低于 95%；低置信关系自动 JOIN 次数为 0。
- [ ] 时间字段与真实数据边界识别准确率不低于 95%。
- [ ] 建档不得向模型发送无界原始行，不得泄露凭证。
- [ ] 200 张表、3000 字段以内在 120 秒内返回 Readiness。
- [ ] `BLOCKED` 不能进入分析；`DEGRADED` 明确展示受限能力。
- [ ] Catalog 持久化后可被 Chat 主链加载；不存在“建档成功但查询仍只读 YAML”的情况。

### Recovery Phase 3：线上通用查询与主动澄清

**目标：** 让公开 API 使用 SemanticCatalog、AnalysisSpec 和真实 MySQL executor 完成查询。

交付：

- live Planner；
- AnalysisSpec validator；
- GuardedMySQLExecutor；
- EvidenceBundle；
- 用户可见口径、时间和证据；
- LLM 无效输出的确定性降级。

硬验收：

- [ ] 时间敏感指标缺少时间时，主动澄清正确率不低于 98%，静默默认时间次数为 0。
- [ ] 口径或关系歧义时，澄清或拒绝率为 100%。
- [ ] 120 个以上 API 单轮任务完整正确率不低于 95%。
- [ ] 数值 Ground Truth 匹配率不低于 98%。
- [ ] 高置信关系路径多表任务正确率不低于 95%。
- [ ] 越权、写操作、注入和不安全 SQL 阻断率为 100%。
- [ ] 普通查询 P95 不高于 20 秒。
- [ ] 每个成功答案展示实际时间范围、指标口径和证据来源。
- [ ] 成功样本的 `kernel_route` 全部是 `analysis_kernel_v2`。
- [ ] `ecommerce.yml` 等预置指标只能增强语义，删除预置后基础通用查询仍然可用。

### Recovery Phase 4：线上连续追问与 Session 恢复

**目标：** ActiveAnalysisState 成为真实会话状态，而不是 runner 内变量。

交付：

- ActiveAnalysisState repository；
- Session load/save；
- Spec patch 主链；
- Catalog version 校验；
- 刷新、重启和并发版本控制。

硬验收：

- [ ] 40 条 3–5 轮 API 连续任务整链完成率不低于 95%。
- [ ] 拆维、增加过滤和修改时间正确继承指标的比例不低于 98%。
- [ ] 上下文不足时主动澄清；错误继承比例低于 1%。
- [ ] 相同 Spec 无意义重复查询次数为 0。
- [ ] 服务重启后继续追问成功。
- [ ] 页面刷新后继续追问成功。
- [ ] 普通 follow-up 不唤醒 Insight、Report、Review 或 Export。
- [ ] Catalog 版本变化后不会使用过期字段执行 SQL。

### Recovery Phase 5：线上证据诊断和报告闭环

**目标：** 只有真实有效证据才能进入诊断，并能在后续会话继续引用。

交付：

- 统一入口前置 diagnosis admission；
- evidence gap 规划；
- Report/Review 门禁；
- DiagnosisSummary 持久化；
- JSON/SSE/前端报告一致性。

硬验收：

- [ ] 无 `SUCCESS_WITH_DATA` 创建重型诊断任务的次数为 0。
- [ ] 无数据、权限拒绝和 SQL 拒绝在 15 秒内给出明确最终结果。
- [ ] 每条关键报告结论证据引用覆盖率为 100%。
- [ ] 无证据因果断言次数为 0。
- [ ] 同一证据缺口最多补查一次。
- [ ] 有数诊断在 90 秒内返回批准报告或明确拒绝原因。
- [ ] JSON、SSE 和前端看到的报告状态与内容一致。
- [ ] “一句话结论”和“下一步查什么”能够从 Session 中读取当前报告回答。
- [ ] Review 未批准时不能生成可下载的最终报告。

### Recovery Phase 6：真实小范围试点

**目标：** 证明非开发用户能在无开发人员陪跑的情况下稳定完成工作。

准入硬门禁：

- [ ] Recovery Phase 0–5 全部通过真实证据验收。
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
| Frontend authenticated E2E | 验证用户实际入口 | Recovery 5–6 必须 |
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
- 拒绝相同时间一次循环伪装的“连续三天”；
- 拒绝包含 `legacy_rollback` 的试点样本；
- 任何阶段失败时 `admission_pass=false`。

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

1. 建立 Recovery 0 黑盒失败测试，证明旧主链问题。
2. 建立统一 `AnalysisApplicationService`，让 JSON/SSE 共享业务入口。
3. 让 feature flag 真正切换主链，保留旧 Graph 仅作回滚。
4. 接入真实 tester 权限预检、QueryOutcome 和写操作 L0 拒绝。
5. 实现 live MySQL profiler 与 Catalog repository。
6. 将 AnalysisSpec/Compiler 接入 GuardedMySQLExecutor。
7. 将 ActiveAnalysisState 接入 Session 持久化。
8. 将 diagnosis admission、summary 和报告回写接入统一入口。
9. 重写 Phase 6 admission，使其解析真实报告内容。
10. 最后运行真人脚本和小范围试点。

明确禁止：

- 在旧 `backend/app/services/agent.py` 上继续堆业务关键词作为主修复方案；
- 同时维护两套独立的 JSON/SSE 业务链；
- 用 preconfigured ecommerce happy path 代替任意 MySQL 验证；
- 为通过评测专门识别固定 case 文案；
- 在真实主链未接通前继续开发新的 Agent、PDF 样式或报告模板。

---

## 9. 关键文件处理原则

### 9.1 应保留并接入

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
- `backend/app/services/persistence.py`
- `backend/app/services/db_connection_service.py`
- `backend/app/pilot/admission.py`
- `backend/app/pilot/multi_day_eval.py`
- 前端 SSE 解析和 Chat complete 处理。

### 9.3 旧 Graph

`backend/app/services/agent.py`：

- 在迁移期间保留为 `legacy_rollback`；
- 不再作为新能力实现位置；
- 新试点流量不得进入；
- 主链迁移完成并稳定后，再另行设计删除或拆除计划；
- 本轮不做大规模无关重构。

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
- [ ] ActiveAnalysisState 在真实 Session 中持久化。
- [ ] 写操作在 L0 被明确拒绝。
- [ ] tester 权限预检真实执行并通过。
- [ ] 无数据或失败不能进入重型诊断。
- [ ] 报告批准、拒绝和摘要能在会话中恢复。
- [ ] API + real MySQL 单轮完整正确率不低于 95%。
- [ ] API 连续追问整链完成率不低于 95%。
- [ ] 所有安全阻断率为 100%。
- [ ] 3 个自然日评测通过。
- [ ] 3–5 名非开发用户 5 日试点通过。
- [ ] 产品负责人确认“小范围试点通过”。

在此之前，任何交接必须明确写：

> **未完成，禁止按可上线或可试点交付。**
