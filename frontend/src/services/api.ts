import type { ChatResponse } from '../types';

const API_BASE = '/api';

// ======== Auth ========

export interface LoginRequest {
  username: string;
  password: string;
}

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: string;
}

export interface TokenResponse {
  access_token: string;
  user: AuthUser;
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

export async function listSpaces(): Promise<Space[]> {
  const res = await fetch(`${API_BASE}/spaces`);
  const data = await res.json();
  return data.spaces;
}

export async function getSpace(spaceId: string): Promise<Space> {
  const res = await fetch(`${API_BASE}/spaces/${spaceId}`);
  return res.json();
}

// ======== Sessions ========

export interface Session {
  id: string;
  user_id: number;
  space_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages?: Message[];
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  meta?: Record<string, unknown>;
  created_at: string;
}

export async function createSession(spaceId: string, title = '新对话'): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ space_id: spaceId, title }),
  });
  return res.json();
}

export async function listSessions(spaceId?: string): Promise<Session[]> {
  const params = new URLSearchParams();
  if (spaceId) params.set('space_id', spaceId);
  const res = await fetch(`${API_BASE}/sessions?${params}`);
  const data = await res.json();
  return data.sessions;
}

export async function getSession(sessionId: string): Promise<Session> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/sessions/${sessionId}`, { method: 'DELETE' });
}

// ======== Chat ========

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
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
  userRole?: string,
  onAnswerChunk?: (text: string) => void,
): { close: () => void } {
  const controller = new AbortController();

  const body: Record<string, string> = { question, space_id: spaceId };
  if (sessionId) body.session_id = sessionId;
  if (selectedMetric) body.selected_metric = selectedMetric;
  if (selectedQueryType) body.selected_query_type = selectedQueryType;
  if (userRole) body.user_role = userRole;

  fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok || !res.body) {
        onError(new Error(`HTTP ${res.status}`));
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let currentData = '';

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
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err);
      }
    });

  return { close: () => controller.abort() };
}
