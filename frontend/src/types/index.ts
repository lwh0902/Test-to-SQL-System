export const CHAT_RESPONSE_TYPES = ['answer', 'clarification', 'error'] as const;

export interface TimeRange {
  start: string;
  end: string;
}

export interface QueryIntent {
  metric: string | null;
  query_type: string | null;
  time_range: TimeRange | null;
  dimensions: string[];
  filters: Record<string, string>;
  confidence: number;
  clarification_reason: string | null;
}

export interface TraceStep {
  node: string;
  status: string;
  output: Record<string, unknown>;
}

export interface MetricCandidate {
  key: string;
  name: string;
  description: string;
  default_query_type: string;
  default_time_range: string;
}

export interface ChatResponse {
  type: 'answer' | 'clarification' | 'error';
  trace_id: string;
  answer: string | null;
  intent: QueryIntent | null;
  sql: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  chart: ChartConfig | null;
  trace: TraceStep[];
  message: string | null;
  candidates: MetricCandidate[];
  plan?: PlanStep[];
  plan_results?: PlanResult[];
}

export interface PlanStep {
  question: string;
  metric: string;
  query_type: string;
}

export interface PlanResult {
  step: number;
  question: string;
  metric: string;
  rows?: Record<string, unknown>[];
  columns?: string[];
  chart?: ChartConfig;
  sql?: string;
  error?: string;
}

export interface ChartConfig {
  type: string;
  x_field: string;
  y_fields: string[];
  labels: Record<string, string>;
  value_format: string | null;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}
