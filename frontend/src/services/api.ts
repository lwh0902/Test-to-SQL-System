import type { ChatResponse } from '../types';

const API_BASE = '/api';

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('dp_token');
  if (token) {
    return { 'Authorization': `Bearer ${token}` };
  }
  return {};
}

// ======== Auth ========

export interface LoginRequest {
  phone?: string;
  username?: string;
  password: string;
}

export interface RegisterRequest {
  phone: string;
  password: string;
  display_name: string;
}

export interface AuthUser {
  id: number;
  username?: string;
  phone?: string;
  display_name: string;
  role: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

export async function refreshAuth(): Promise<TokenResponse> {
  const refresh_token = localStorage.getItem('dp_refresh_token');
  if (!refresh_token) throw new Error('No refresh token');
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token }),
  });
  if (!res.ok) throw new Error('Refresh failed');
  const data: TokenResponse = await res.json();
  localStorage.setItem('dp_token', data.access_token);
  localStorage.setItem('dp_refresh_token', data.refresh_token);
  localStorage.setItem('dp_user', JSON.stringify(data.user));
  return data;
}

async function fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
  const headers = { ...options.headers, ...authHeaders() };
  let res = await fetch(url, { ...options, headers });
  if (res.status === 401) {
    try {
      await refreshAuth();
      const retryHeaders = { ...options.headers, ...authHeaders() };
      res = await fetch(url, { ...options, headers: retryHeaders });
    } catch {
      localStorage.removeItem('dp_token');
      localStorage.removeItem('dp_refresh_token');
      localStorage.removeItem('dp_user');
      window.location.reload();
    }
  }
  return res;
}

export async function login(req: LoginRequest): Promise<TokenResponse> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `登录失败 (${res.status})`);
  }
  return res.json();
}

export async function register(req: RegisterRequest): Promise<TokenResponse> {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `注册失败 (${res.status})`);
  }
  return res.json();
}

export async function logout(): Promise<void> {
  try {
    await fetchWithAuth(`${API_BASE}/auth/logout`, { method: 'POST' });
  } catch {
    // ignore errors on logout
  }
}

// ======== Spaces ========

export interface Space {
  id: string;
  name: string;
  description: string;
  icon: string;
  dataset_id: string;
  metrics?: MetricInfo[];
}

export interface MetricInfo {
  key: string;
  name: string;
  description: string;
  default_query_type: string;
  default_time_range: string;
}

export interface DataMapColumn {
  name: string;
  type: string;
  comment: string;
}

export interface DataMapTable {
  name: string;
  title: string;
  description: string;
  columns: DataMapColumn[];
  key_columns: string[];
  time_columns: string[];
  measure_columns: string[];
  dimension_columns: string[];
}

export interface DataMapQuestion {
  text: string;
  metric?: string;
  query_type?: string;
  table?: string;
  intent?: string;
  source: 'metric' | 'schema';
}

export interface DataMapMetric {
  key: string;
  name: string;
  description: string;
  query_types: string[];
  tables: string[];
  recommended_questions: DataMapQuestion[];
}

export interface DataMap {
  space_id: string;
  mode: 'preset' | 'schema';
  summary: {
    table_count: number;
    field_count: number;
    metric_count: number;
  };
  tables: DataMapTable[];
  metrics: DataMapMetric[];
  recommended_questions: DataMapQuestion[];
}

export async function listSpaces(): Promise<Space[]> {
  const res = await fetchWithAuth(`${API_BASE}/spaces`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.spaces || [];
}

export async function getSpace(spaceId: string): Promise<Space> {
  const res = await fetchWithAuth(`${API_BASE}/spaces/${spaceId}`);
  return res.json();
}

export async function getDataMap(spaceId: string): Promise<DataMap | null> {
  const res = await fetchWithAuth(`${API_BASE}/spaces/${spaceId}/data-map`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.data_map || null;
}

// ======== Sessions ========

export interface DiagnosisBundle {
  task_id: string;
  report?: {
    id?: string;
    type?: string;
    payload?: { sections?: Array<{ title?: string; content?: string; evidence_ids?: string[] }> };
  };
  review?: {
    id?: string;
    type?: string;
    payload?: { approved?: boolean; reasons?: string[] };
  };
  exports?: Array<{
    id?: string;
    type?: string;
    payload?: {
      exported?: boolean;
      format?: string;
      file_name?: string;
      artifact_id?: string;
      download_url?: string;
    };
  }>;
  artifacts?: Array<Record<string, unknown>>;
}

export interface Session {
  id: string;
  user_id: number;
  space_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages?: Message[];
  /** Latest deep-diagnosis package for UI restore after refresh */
  diagnosis_bundle?: DiagnosisBundle | null;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  meta?: Record<string, unknown>;
  created_at: string;
}

export async function createSession(spaceId: string, title = '新对话'): Promise<Session> {
  const res = await fetchWithAuth(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ space_id: spaceId, title }),
  });
  return res.json();
}

export async function listSessions(spaceId?: string): Promise<Session[]> {
  const params = new URLSearchParams();
  if (spaceId) params.set('space_id', spaceId);
  const res = await fetchWithAuth(`${API_BASE}/sessions?${params}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.sessions || [];
}

export async function getSession(sessionId: string): Promise<Session> {
  const res = await fetchWithAuth(`${API_BASE}/sessions/${sessionId}`);
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/sessions/${sessionId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export async function renameSession(sessionId: string, title: string): Promise<void> {
  const res = await fetchWithAuth(`${API_BASE}/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`rename failed: ${res.status}`);
}

// ======== Connections ========

export interface Connection {
  id: string;
  name: string;
  host: string;
  port: number;
  db_name: string;
  db_user: string;
  status: string;
  last_tested_at: string | null;
  created_at: string;
}

export interface TestDirectRequest {
  host: string;
  port: number;
  db_user: string;
  db_password: string;
  db_name: string;
}

export async function listConnections(): Promise<{ connections: Connection[] }> {
  const res = await fetchWithAuth(`${API_BASE}/connections`);
  if (!res.ok) return { connections: [] };
  return res.json();
}

export async function createConnection(req: {
  name: string; host: string; port: number;
  db_user: string; db_password: string; db_name: string;
}): Promise<Connection> {
  const res = await fetchWithAuth(`${API_BASE}/connections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `创建失败 (${res.status})`);
  }
  return res.json();
}

export async function testDirectConnection(req: TestDirectRequest): Promise<{ ok: boolean; error?: string }> {
  const res = await fetchWithAuth(`${API_BASE}/connections/test-direct`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  return res.json();
}

export async function discoverSchemaDirect(req: TestDirectRequest): Promise<{
  ok: boolean; schema?: Record<string, { comment: string; columns: { name: string; type: string }[] }>; error?: string;
}> {
  const res = await fetchWithAuth(`${API_BASE}/connections/discover-schema`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  return res.json();
}

export async function createUserSpace(name: string, connectionId: string): Promise<{ id: string; name: string }> {
  const res = await fetchWithAuth(`${API_BASE}/spaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, connection_id: connectionId }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `创建空间失败 (${res.status})`);
  }
  return res.json();
}

// ======== Chat ========

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface DiagnosisStreamOptions {
  seedQuery?: {
    columns?: string[];
    rows?: Record<string, unknown>[];
    rows_count?: number;
    sql?: string;
  };
  exportFormats?: string[];
}

export function streamDiagnosis(
  question: string,
  spaceId: string,
  sessionId: string,
  onEvent: (event: SSEEvent) => void,
  onComplete: (data: Record<string, unknown>) => void,
  onError: (error: Error) => void,
  options?: DiagnosisStreamOptions,
) {
  const controller = new AbortController();
  const body: Record<string, unknown> = {
    question,
    space_id: spaceId,
    session_id: sessionId,
  };
  if (options?.seedQuery) body.seed_query = options.seedQuery;
  if (options?.exportFormats?.length) body.export_formats = options.exportFormats;
  fetch(`${API_BASE}/diagnosis/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then((res) => {
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      processDiagnosisStream(res, onEvent, onComplete, onError);
    })
    .catch(onError);
  return { close: () => controller.abort() };
}

/** Authenticated download of an ExportFile artifact. */
export async function downloadExport(
  artifactId: string,
  sessionId: string,
  spaceId: string,
  fileName?: string,
): Promise<void> {
  const qs = new URLSearchParams({ session_id: sessionId, space_id: spaceId });
  const res = await fetch(`${API_BASE}/exports/${artifactId}/download?${qs}`, {
    headers: { ...authHeaders() },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `下载失败 HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName || `${artifactId}.bin`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
function processDiagnosisStream(res: Response, onEvent: (event:SSEEvent)=>void, onComplete:(data:Record<string,unknown>)=>void, onError:(error:Error)=>void) {
  const reader=res.body!.getReader(), decoder=new TextDecoder(); let buffer='', event='', data='';
  (async()=>{ while(true) { const {done,value}=await reader.read(); if(done) break; buffer+=decoder.decode(value,{stream:true}); const lines=buffer.split('\n'); buffer=lines.pop()||''; for(const line of lines){ if(line.startsWith('event: ')) event=line.slice(7).trim(); else if(line.startsWith('data: ')) data=line.slice(6); else if(!line && event && data){ const parsed=JSON.parse(data); if(event==='complete') onComplete(parsed); else onEvent({event,data:parsed}); event=''; data=''; } } } })().catch(onError);
}

export function streamChat(
  question: string,
  spaceId: string,
  sessionId: string | null,
  onEvent: (event: SSEEvent) => void,
  onComplete: (response: ChatResponse) => void,
  onError: (error: Error) => void,
  selectedMetric?: string,
  selectedQueryType?: string,
  onAnswerChunk?: (text: string) => void,
): { close: () => void } {
  const controller = new AbortController();

  const body: Record<string, string> = { question, space_id: spaceId };
  if (sessionId) body.session_id = sessionId;
  if (selectedMetric) body.selected_metric = selectedMetric;
  if (selectedQueryType) body.selected_query_type = selectedQueryType;

  fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (res.status === 401) {
        try {
          await refreshAuth();
          const retryRes = await fetch(`${API_BASE}/chat/stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify(body),
            signal: controller.signal,
          });
          if (!retryRes.ok || !retryRes.body) {
            onError(new Error(`HTTP ${retryRes.status}`));
            return;
          }
          processSSEStream(retryRes, onEvent, onComplete, onError, onAnswerChunk);
        } catch {
          localStorage.removeItem('dp_token');
          localStorage.removeItem('dp_refresh_token');
          localStorage.removeItem('dp_user');
          window.location.reload();
        }
        return;
      }
      if (!res.ok || !res.body) {
        onError(new Error(`HTTP ${res.status}`));
        return;
      }
      processSSEStream(res, onEvent, onComplete, onError, onAnswerChunk);
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err);
      }
    });

  return { close: () => controller.abort() };
}

function processSSEStream(
  res: Response,
  onEvent: (event: SSEEvent) => void,
  onComplete: (response: ChatResponse) => void,
  onError: (error: Error) => void,
  onAnswerChunk?: (text: string) => void,
) {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = '';
  let currentData = '';

  (async () => {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          currentData = line.slice(6);
        } else if (line === '' && currentEvent && currentData) {
          try {
            const parsed = JSON.parse(currentData);
            if (currentEvent === 'complete') {
              onComplete(parsed as ChatResponse);
            } else if (currentEvent === 'answer_chunk' && onAnswerChunk) {
              onAnswerChunk(parsed.text || '');
            } else {
              onEvent({ event: currentEvent, data: parsed });
            }
          } catch {
            // ignore parse errors
          }
          currentEvent = '';
          currentData = '';
        }
      }
    }
  })().catch(onError);
}
