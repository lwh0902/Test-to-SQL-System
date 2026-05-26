# DataPilot Agent

AI 驱动的对话式数据分析工作台，用户用自然语言提问，系统自动完成意图识别、SQL 生成、权限校验、查询执行和可视化展示。

## 架构概览

```
用户提问 "为什么最近销售额下降"
        │
        ▼
  ┌─────────────┐
  │ Intent Router │  规则引擎 + LLM 兜底，识别意图类型
  └──────┬──────┘
         │ data_query / follow_up / plan_execute / chat / help
         ▼
  ┌──────────────────────────────────────────────────────┐
  │              LangGraph StateGraph (11 节点)            │
  │                                                        │
  │  ┌─ Single Query ──────────────────────────────────┐  │
  │  │ Planner → MetricResolver → PermissionGuard      │  │
  │  │ → SQLGenerator → SQLGuard → QueryExecutor       │  │
  │  └─────────────────────────────────────────────────┘  │
  │                                                        │
  │  ┌─ Plan-and-Execute ─────────────────────────────┐  │
  │  │ PlanGenerator → StepExecutor (loop)             │  │
  │  │              → SummaryAgent                     │  │
  │  └─────────────────────────────────────────────────┘  │
  │                                                        │
  │  ┌─ Follow-Up ────────────────────────────────────┐  │
  │  │ ContextResolver → Planner → ...                 │  │
  │  └─────────────────────────────────────────────────┘  │
  └──────────────────┬───────────────────────────────────┘
                     ▼
              Persister → MySQL
              SSE Events → Frontend
```

## 技术栈

| 层 | 技术 |
|---|------|
| 后端框架 | FastAPI + Uvicorn |
| Agent 编排 | LangGraph (StateGraph) |
| LLM | DeepSeek v4 Flash (Anthropic 兼容 API) |
| 数据库 | MySQL (SQLAlchemy 2.0) |
| SQL 安全 | sqlglot AST 解析 |
| 前端框架 | React 19 + TypeScript |
| UI 组件 | Ant Design 6 |
| 图表 | ECharts 6 |
| 流式通信 | SSE (Server-Sent Events) |

## 核心功能

### 意图路由 (Intent Router)

两层分类架构，优先规则引擎（零延迟），LLM 兜底：

- **data_query** — 单步数据查询，如 "最近7天销售额趋势"
- **follow_up** — 追问/指代，如 "按渠道拆一下"，需要上下文补全
- **plan_execute** — 多步推理，如 "为什么销售额下降"，触发 Plan-and-Execute
- **chat** — 闲聊
- **help** — 使用帮助

### Plan-and-Execute 多步推理

处理需要多步分析的复杂问题：

1. **PlanGenerator** — LLM 分析问题，生成 2-4 步查询计划
2. **StepExecutor** — 循环执行每一步，复用完整的单步查询流水线（含权限校验和 SQL 安全检查）
3. **SummaryAgent** — LLM 综合所有步骤结果，生成归因总结

### 指标匹配与参数补全

用户自然语言 → 结构化 `QueryIntent`：

```
"最近7天销售额按渠道拆解"  →  {
  metric: "gmv",
  query_type: "breakdown",
  time_range: {start: "2026-05-19", end: "2026-05-26"},
  dimensions: ["source_channel"],
  confidence: 0.92
}
```

- **Rule Parser** — 关键词匹配，零成本
- **LLM Parser** — 规则匹配失败时，LLM 从自然语言提取 metric / query_type / time_range / dimensions / filters

### 双重安全守卫

- **PermissionGuard** — RBAC 权限控制，基于指标配置的 roles 白名单
- **SQLGuard** — sqlglot AST 解析，校验表名白名单 + 敏感字段拦截

### 工作记忆 (Working Memory)

结构化 JSON 存储（`last_metric`, `last_query_type`, `last_time_range`），支持多轮追问时的上下文补全：

```
用户: 最近7天销售额趋势          → 查询 gmv trend
用户: 按渠道拆一下               → ContextResolver 补全为 "最近7天销售额按渠道拆解"
```

### SSE 流式输出

- 逐步推送节点状态（intent_router → planner → query_executor）
- Token 级流式文本（answer_chunk），打字机效果
- 自动生成 Session 标题

## 项目结构

```
datacheck/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/
│   │   │   ├── auth.py          # 登录认证 (JWT)
│   │   │   ├── chat.py          # SSE 流式 + JSON 接口
│   │   │   ├── spaces.py        # 分析空间管理
│   │   │   └── sessions.py      # 会话管理
│   │   ├── services/
│   │   │   ├── agent.py         # LangGraph 状态机 (核心)
│   │   │   ├── persistence.py   # 消息/Trace 持久化
│   │   │   ├── metric_service.py # 指标配置 + SQL 渲染
│   │   │   └── query_service.py # 查询执行
│   │   ├── parsers/
│   │   │   ├── llm_parser.py    # LLM 意图解析
│   │   │   └── rule_parser.py   # 规则关键词匹配
│   │   ├── guards/
│   │   │   ├── permission_guard.py # RBAC 权限守卫
│   │   │   └── sql_guard.py     # SQL 安全守卫
│   │   ├── models/
│   │   │   └── schemas.py       # Pydantic 数据模型
│   │   └── core/
│   │       └── database.py      # 数据库连接
│   ├── scripts/
│   │   ├── seed_system.py       # 初始化用户和空间
│   │   ├── generate_ecommerce_mock.py  # 电商模拟数据
│   │   └── run_eval.py          # 评估脚本
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # 主界面
│   │   ├── services/api.ts      # API + SSE 客户端
│   │   ├── types/index.ts       # TypeScript 类型定义
│   │   └── components/
│   │       ├── ChatMessage.tsx   # 消息渲染 (图表 + 表格)
│   │       └── LoginModal.tsx   # 登录弹窗
│   ├── vite.config.ts           # Vite 配置 (含 API 代理)
│   └── package.json
└── config/
    ├── metric_seeds/
    │   └── ecommerce.yml        # 电商指标配置 (15个指标, 40+ SQL 模板)
    ├── metrics.yml              # 技术质量指标配置
    └── init_*.sql               # 数据库建表脚本
```

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- MySQL 8.0+

### 1. 数据库初始化

```bash
mysql -u root -p < config/init_system_tables.sql
mysql -u root -p < config/init_ecommerce_tables.sql
```

### 2. 后端启动

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入数据库连接和 LLM API Key

# 初始化系统数据（用户、空间）
python scripts/seed_system.py

# 生成模拟数据
python scripts/generate_ecommerce_mock.py

# 启动服务
uvicorn app.main:app --reload --port 8000
```

### 3. 前端启动

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:3000

### 环境变量

复制示例文件并填入实际值：

```bash
cp backend/.env.example backend/.env
```

```env
# Database
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=datacheck

# LLM (Anthropic-compatible API)
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com/anthropic
LLM_MODEL=deepseek-v4-flash

# JWT
JWT_SECRET=your_jwt_secret
```

## 指标配置

指标通过 YAML 配置，无需改代码即可扩展。每个指标定义：

- **SQL 模板** — Jinja2 渲染，支持 fact / trend / breakdown / anomaly 四种查询类型
- **权限** — roles 白名单控制谁可以查
- **安全** — permitted_tables + sensitive_fields 白名单
- **图表** — 自动匹配 ECharts 配置（折线图、柱状图、统计卡片）

```yaml
gmv:
  name: 销售额 GMV
  description: 订单支付总金额
  allowed_query_types: [fact, trend, breakdown]
  roles: [admin, product, operator]
  permitted_tables: [ecom_orders, ecom_order_items]
  chart:
    trend: { type: line }
    breakdown: { type: bar }
```

## License

MIT
