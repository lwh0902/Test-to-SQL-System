export const CHAT_RESPONSE_TYPES = [
  'answer',
  'clarification',
  'error',
  'chat',
  'help',
  'schema_help',
  'data_map',
  'deep_diagnosis',
] as const;

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

export interface ChatNextAction {
  id: string;
  label: string;
}

export interface ChatResponse {
  type:
    | 'answer'
    | 'clarification'
    | 'error'
    | 'chat'
    | 'help'
    | 'schema_help'
    | 'data_map'
    | 'deep_diagnosis'
    | string;
  trace_id: string;
  answer: string | null;
  intent: QueryIntent | null;
  sql: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  rows_count?: number | null;
  chart: ChartConfig | null;
  trace: TraceStep[];
  message: string | null;
  candidates: MetricCandidate[];
  plan?: PlanStep[];
  plan_results?: PlanResult[];
  data_map?: import('../services/api').DataMap;
  db_identity?: DbIdentity;
  /** Canonical terminal fields */
  terminal_status?: string | null;
  stop_reason?: string | null;
  kernel_route?: string | null;
  task_id?: string | null;
  evidence?: Record<string, unknown> | null;
  artifacts?: Array<Record<string, unknown>>;
  query_outcome?: Record<string, unknown> | null;
  supervisor_decision?: Record<string, unknown> | null;
  task_spec?: Record<string, unknown> | null;
  clarify_slots?: string[];
  ux_hints?: string[];
  next_actions?: ChatNextAction[];
}

export interface DbIdentity {
  space_id: string;
  space_name: string;
  space_description: string;
  is_preset: boolean;
  table_count: number;
  connection?: {
    db_type: string;
    host_masked: string;
    port: number;
    db_name: string;
    last_tested_at: string | null;
  };
}

export interface PlanStep {
  id?: string;
  title?: string;
  question?: string;
  metric: string;
  query_type: string;
  time_range?: TimeRange;
  dimensions?: string[];
  purpose?: string;
}

export interface PlanResult {
  step_id?: string;
  step: number;
  title?: string;
  question: string;
  metric: string;
  rows?: Record<string, unknown>[];
  columns?: string[];
  chart?: ChartConfig;
  sql?: string;
  error?: string;
}

export interface PlanProgressStep {
  id: string;
  title: string;
  status: 'pending' | 'running' | 'done';
  rows?: number;
}

export interface PlanProgress {
  goal: string;
  steps: PlanProgressStep[];
  phase: 'planning' | 'executing' | 'summarizing' | 'done';
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
