"""统一数据模型"""

from pydantic import BaseModel


class TimeRange(BaseModel):
    start: str
    end: str


class QueryIntent(BaseModel):
    metric: str | None = None
    query_type: str | None = None
    time_range: TimeRange | None = None
    dimensions: list[str] = []
    filters: dict = {}
    confidence: float = 0.0
    clarification_reason: str | None = None


class ChatRequest(BaseModel):
    question: str
    space_id: str = "tech_quality"
    session_id: str | None = None
    workspace_id: str = "default"
    selected_metric: str | None = None
    selected_query_type: str | None = None


class TraceStep(BaseModel):
    node: str
    status: str = "done"
    input: dict = {}
    output: dict = {}
    elapsed_ms: int = 0
    started_at: str | None = None
    ended_at: str | None = None


class ChartConfig(BaseModel):
    type: str  # line | bar | stat
    x_field: str = ""
    y_fields: list[str] = []
    labels: dict[str, str] = {}
    value_format: str | None = None  # percent | number | duration_ms


class MetricCandidate(BaseModel):
    key: str
    name: str
    description: str
    default_query_type: str = "trend"
    default_time_range: str = "last_7_days"


class ChatResponse(BaseModel):
    type: str  # answer | clarification | error | deep_diagnosis | ...
    trace_id: str
    answer: str | None = None
    intent: QueryIntent | None = None
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict] = []
    rows_count: int | None = None
    chart: ChartConfig | None = None
    trace: list[TraceStep] = []
    message: str | None = None
    candidates: list[MetricCandidate] = []
    # R1a canonical terminal fields (JSON/SSE parity)
    terminal_status: str | None = None
    stop_reason: str | None = None
    kernel_route: str | None = None
    task_id: str | None = None
    # UX / observability (optional; frontend uses when present)
    evidence: dict | None = None
    artifacts: list[dict] = []
    query_outcome: dict | None = None
    supervisor_decision: dict | None = None
    task_spec: dict | None = None
    clarify_slots: list[str] = []
    ux_hints: list[str] = []
    next_actions: list[dict] = []


class GuardCheckResult(BaseModel):
    passed: bool
    code: str | None = None
    message: str | None = None
    detail: str | None = None
