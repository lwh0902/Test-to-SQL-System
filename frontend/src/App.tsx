import { useState, useRef, useEffect, useCallback } from 'react';
import { Input, Button, Layout, Typography, Spin, Tooltip, Drawer, Modal, message } from 'antd';
import {
  SendOutlined,
  PlusOutlined,
  MessageOutlined,
  BugOutlined,
  ShoppingCartOutlined,
  BarChartOutlined,
  DeleteOutlined,
  LoginOutlined,
  LogoutOutlined,
  MenuOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import { streamChat } from './services/api';
import type { SSEEvent } from './services/api';
import type { ChatResponse, MetricCandidate, TraceStep, PlanProgress } from './types';
import {
  listSpaces,
  listSessions,
  createSession,
  deleteSession,
  login,
  register,
  logout as apiLogout,
  getDataMap,
} from './services/api';
import type { DataMap, DataMapQuestion, Space as SpaceInfo, Session } from './services/api';

import ChatMessage from './components/ChatMessage';
import DataMapBlock from './components/DataMapBlock';
import TraceDetailModal from './components/TraceDetailModal';
import SpaceCreateModal from './components/SpaceCreateModal';
import Ferrofluid from '@/components/ui/ferrofluid';
import GooeyNav from '@/components/ui/gooey-nav';
import LoginPage from './pages/LoginPage';
import datapilotAppIcon from './assets/datapilot-app-icon.png';

const { Sider, Content } = Layout;
const { Text } = Typography;

const BRAND_INK_TEXT = '#FFFFFF';
const BRAND_PANEL = 'rgba(8, 6, 16, 0.72)';
const BRAND_CARD = 'rgba(255, 255, 255, 0.04)';
const BRAND_DEEP = '#1A0F30';
const BRAND_LIME = '#C079FF';
const BRAND_BORDER = 'rgba(255, 255, 255, 0.08)';
const BRAND_MUTED = '#9B97AD';
const BRAND_TEXT = '#F4F4F8';
const BRAND_SOFT = 'rgba(255, 255, 255, 0.06)';
const GLASS_BLUR = 'blur(16px)';

const SPACE_META: Record<string, { icon: React.ReactNode; color: string }> = {
  tech_quality: { icon: <BugOutlined />, color: BRAND_LIME },
  ecommerce: { icon: <ShoppingCartOutlined />, color: '#6E6A82' },
};

const EVENT_LABELS: Record<string, string> = {
  run_started: '开始分析你的问题',
  route_done: '识别问题类型',
  planner_started: '正在理解查询意图',
  planner: '已理解查询意图',
  metric_resolving: '正在匹配业务指标',
  metric_resolved: '已匹配指标口径',
  permission_checking: '正在校验访问权限',
  guard_check: '权限校验完成',
  sql_generating: '正在生成查询语句',
  sql_generated: '已生成查询语句',
  sql_checking: '正在执行 SQL 安全校验',
  query_running: '正在查询数据库',
  query_done: '数据查询完成',
  answer_generating: '正在生成分析结论',
  context_resolver: '正在解析上下文',
  diagnosis_started: '正在诊断查询结果',
  diagnosis_done: '诊断完成',
  table_query: '正在查询表数据',
};

function App() {
  const [token, setToken] = useState<string | null>(localStorage.getItem('dp_token'));
  const [user, setUser] = useState<{ display_name: string; role: string } | null>(() => {
    const saved = localStorage.getItem('dp_user');
    return saved ? JSON.parse(saved) : null;
  });
  const [, setLoginOpen] = useState(false);
  const [spaceCreateOpen, setSpaceCreateOpen] = useState(false);

  const [spaces, setSpaces] = useState<SpaceInfo[]>([]);
  const [activeSpaceId, setActiveSpaceId] = useState<string>(() =>
    localStorage.getItem('dp_space') || 'tech_quality');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(() =>
    localStorage.getItem('dp_session'));

  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [sseSteps, setSseSteps] = useState<{ node: string; status: string }[]>([]);
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [messages, setMessages] = useState<
    { role: 'user' | 'assistant'; content: string; data?: ChatResponse }[]
  >([]);
  const [dataMap, setDataMap] = useState<DataMap | null>(null);
  const [dataMapLoading, setDataMapLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 移动端侧边栏
  const [drawerOpen, setDrawerOpen] = useState(false);

  // 停止生成
  const streamRef = useRef<{ close: () => void } | null>(null);

  // Trace 详情弹窗
  const [traceOpen, setTraceOpen] = useState(false);
  const [traceData, setTraceData] = useState<{ traceId: string; steps: TraceStep[]; question?: string; sql?: string }>({
    traceId: '', steps: [],
  });

  // 会话切换加载
  const [sessionLoading, setSessionLoading] = useState(false);

  // Plan-and-Execute 进度
  const [planProgress, setPlanProgress] = useState<PlanProgress | null>(null);

  useEffect(() => {
    if (!token) return;
    listSpaces().then(setSpaces).catch(() => {});
  }, [token]);

  useEffect(() => {
    if (!token || !activeSpaceId) {
      setDataMap(null);
      return;
    }
    setDataMapLoading(true);
    getDataMap(activeSpaceId)
      .then(setDataMap)
      .catch(() => setDataMap(null))
      .finally(() => setDataMapLoading(false));
  }, [activeSpaceId, token]);

  const loadSessions = useCallback(async (spaceId: string) => {
    try {
      const list = await listSessions(spaceId);
      setSessions(list);
      return list;
    } catch {
      message.error('加载会话列表失败');
      return [];
    }
  }, []);

  useEffect(() => {
    if (!token) { setSessions([]); return; }
    loadSessions(activeSpaceId).then((list) => {
      // 恢复或自动选择 — 使用内部函数避免依赖 handleSelectSession
      const savedId = localStorage.getItem('dp_session');
      const targetId = savedId && list.some((s) => s.id === savedId) ? savedId
        : list.length > 0 ? list[0].id : null;
      if (targetId) {
        setActiveSessionId(targetId);
        localStorage.setItem('dp_session', targetId);
        setSessionLoading(true);
        import('./services/api').then((m) => m.getSession(targetId)).then((sess) => {
          const msgs: typeof messages = [];
          for (const m of sess.messages || []) {
            if (m.role === 'user') msgs.push({ role: 'user', content: m.content });
            else if (m.meta) msgs.push({ role: 'assistant', content: m.content, data: m.meta as unknown as ChatResponse });
          }
          setMessages(msgs); setSseSteps([]);
        }).catch(() => {}).finally(() => setSessionLoading(false));
      }
    });
  }, [activeSpaceId, loadSessions, token]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, sseSteps]);

  const handleLogin = async (phone: string, password: string) => {
    const res = await login({ phone, password });
    setToken(res.access_token);
    setUser(res.user);
    localStorage.setItem('dp_token', res.access_token);
    localStorage.setItem('dp_refresh_token', res.refresh_token);
    localStorage.setItem('dp_user', JSON.stringify(res.user));
    setLoginOpen(false);
    listSpaces().then(setSpaces).catch(() => {});
  };

  const handleRegister = async (phone: string, password: string, displayName: string) => {
    const res = await register({ phone, password, display_name: displayName });
    setToken(res.access_token);
    setUser(res.user);
    localStorage.setItem('dp_token', res.access_token);
    localStorage.setItem('dp_refresh_token', res.refresh_token);
    localStorage.setItem('dp_user', JSON.stringify(res.user));
    setLoginOpen(false);
    listSpaces().then(setSpaces).catch(() => {});
  };

  const handleLogout = () => {
    apiLogout().catch(() => {});
    setToken(null); setUser(null);
    localStorage.removeItem('dp_token');
    localStorage.removeItem('dp_refresh_token');
    localStorage.removeItem('dp_user');
    localStorage.removeItem('dp_session');
    localStorage.removeItem('dp_space');
    setSpaces([]); setSessions([]); setMessages([]);
    setActiveSessionId(null);
  };

  const handleNewChat = async () => {
    try {
      const session = await createSession(activeSpaceId, '新对话');
      const sid = session.id;
      setActiveSessionId(sid);
      localStorage.setItem('dp_session', sid);
      setMessages([]); setSseSteps([]);
      loadSessions(activeSpaceId);
    } catch {
      message.error('创建会话失败');
    }
    setDrawerOpen(false);
  };

  const handleSelectSession = async (sessionId: string) => {
    setActiveSessionId(sessionId);
    localStorage.setItem('dp_session', sessionId);
    setSessionLoading(true);
    try {
      const sess = await import('./services/api').then((m) => m.getSession(sessionId));
      const msgs: typeof messages = [];
      for (const m of sess.messages || []) {
        if (m.role === 'user') msgs.push({ role: 'user', content: m.content });
        else if (m.meta) msgs.push({ role: 'assistant', content: m.content, data: m.meta as unknown as ChatResponse });
      }
      setMessages(msgs); setSseSteps([]);
    } catch {
      message.error('加载会话失败');
    }
    setSessionLoading(false);
    setDrawerOpen(false);
  };

  const handleDeleteSession = (sessionId: string) => {
    Modal.confirm({
      title: '删除会话',
      content: '确定要删除这个会话吗？删除后不可恢复。',
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteSession(sessionId);
          if (activeSessionId === sessionId) {
            setActiveSessionId(null);
            localStorage.removeItem('dp_session');
            setMessages([]);
          }
          loadSessions(activeSpaceId);
        } catch {
          message.error('删除会话失败');
        }
      },
    });
  };

  const handleStopGeneration = () => {
    streamRef.current?.close();
    streamRef.current = null;
    setLoading(false);
  };

  const handleOpenTrace = (data: ChatResponse) => {
    setTraceData({
      traceId: data.trace_id || '',
      steps: data.trace || [],
      question: data.intent ? `${data.intent.metric} - ${data.intent.query_type}` : undefined,
      sql: data.sql || undefined,
    });
    setTraceOpen(true);
  };

  const handleSend = async (question?: string, candidate?: MetricCandidate) => {
    const q = question || inputValue.trim();
    if (!q || loading) return;

    // 如果没有 session，先自动创建一个
    let sessionId = activeSessionId;
    if (!sessionId) {
      try {
        const sess = await createSession(activeSpaceId, '新对话');
        sessionId = sess.id;
        setActiveSessionId(sessionId);
        localStorage.setItem('dp_session', sessionId);
        loadSessions(activeSpaceId);
      } catch {
        message.error('创建会话失败');
        return;
      }
    }

    setInputValue(''); setLoading(true); setSseSteps([]); setStreamingAnswer(''); setPlanProgress(null);
    setMessages((prev) => [...prev, { role: 'user', content: q }]);
    const stream = streamChat(q, activeSpaceId, sessionId,
      (event: SSEEvent) => {
        // Plan-and-Execute 事件处理
        if (event.event === 'plan_started') {
          setPlanProgress({ goal: '', steps: [], phase: 'planning' });
          return;
        }
        if (event.event === 'plan_generated') {
          const goal = (event.data?.goal as string) || '';
          const rawSteps = (event.data?.steps as Array<{ id: string; title: string }>) || [];
          setPlanProgress({
            goal,
            steps: rawSteps.map((s) => ({ id: s.id, title: s.title, status: 'pending' as const })),
            phase: 'executing',
          });
          return;
        }
        if (event.event === 'plan_validated') {
          return;
        }
        if (event.event === 'step_started') {
          const stepId = event.data?.step_id as string;
          const stepNum = event.data?.step_number as number;
          setPlanProgress((prev) => prev ? {
            ...prev,
            steps: prev.steps.map((s) => s.id === stepId
              ? { ...s, status: 'running' as const }
              : s),
          } : prev);
          // Also add to sseSteps for the unified progress view
          setSseSteps((prev) => [...prev, { node: `step_${stepNum}`, status: 'running' }]);
          return;
        }
        if (event.event === 'step_completed') {
          const stepId = event.data?.step_id as string;
          const rows = event.data?.rows as number;
          const stepNum = event.data?.step_number as number;
          setPlanProgress((prev) => prev ? {
            ...prev,
            steps: prev.steps.map((s) => s.id === stepId
              ? { ...s, status: 'done' as const, rows }
              : s),
          } : prev);
          setSseSteps((prev) => [
            ...prev.filter((s) => s.node !== `step_${stepNum}`),
            { node: `step_${stepNum}`, status: 'done' },
          ]);
          return;
        }
        if (event.event === 'summary_started') {
          setPlanProgress((prev) => prev ? { ...prev, phase: 'summarizing' } : prev);
          setSseSteps((prev) => [...prev, { node: 'summary', status: 'running' }]);
          return;
        }

        // route_done — use label from payload
        if (event.event === 'route_done') {
          const label = (event.data?.label as string) || '识别问题类型';
          setSseSteps((prev) => [...prev, { node: `route:${label}`, status: 'done' }]);
          return;
        }

        // answer_chunk is handled by streaming callback, don't add to steps
        if (event.event === 'answer_chunk' || event.event === 'summary_done') {
          return;
        }

        // 通用 SSE 进度 — use Chinese labels
        const label = EVENT_LABELS[event.event] || event.event;
        const status = event.data?.status === 'passed' || event.data?.status === 'done' ? 'done'
          : event.data?.status === 'denied' ? 'denied' : 'process';
        setSseSteps((prev) => [...prev, { node: label, status }]);
      },
      (res: ChatResponse) => {
        setMessages((prev) => [...prev, { role: 'assistant', content: res.answer || res.message || '', data: res }]);
        setLoading(false); setSseSteps([]); setStreamingAnswer(''); setPlanProgress(null); loadSessions(activeSpaceId);
        streamRef.current = null;
      },
      (err: Error) => {
        setMessages((prev) => [...prev, {
          role: 'assistant',
          content: `请求失败: ${err.message}`,
          data: {
            type: 'error',
            trace_id: '',
            answer: null,
            intent: null,
            sql: null,
            chart: null,
            message: err.message,
            columns: [],
            rows: [],
            trace: [],
            candidates: [],
          },
        }]);
        setLoading(false); setSseSteps([]); setStreamingAnswer('');
        streamRef.current = null;
      },
      candidate?.key, candidate?.default_query_type,
      (chunk: string) => { setStreamingAnswer((prev) => prev + chunk); },
    );
    streamRef.current = stream;
  };

  const handleCandidateClick = (c: MetricCandidate) => handleSend(`${c.name}趋势`, c);
  const handleQuestionClick = (q: DataMapQuestion) => {
    handleSend(q.text, q.metric ? {
      key: q.metric,
      name: q.text,
      description: '',
      default_query_type: q.query_type || 'fact',
      default_time_range: 'last_7_days',
    } : undefined);
  };
  const activeSpace = spaces.find((s) => s.id === activeSpaceId);
  const spaceMeta = SPACE_META[activeSpaceId] || { icon: <BarChartOutlined />, color: '#C079FF' };

  // ---- 侧边栏内容（PC 和移动端共用） ----
  const sidebarContent = (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Brand */}
      <div style={{ padding: '24px 20px 16px' }}>
        <div style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.01em', color: '#F4F4F8' }}>
          DataPilot Agent
        </div>
        <div style={{ fontSize: 11, color: '#9B97AD', marginTop: 4, letterSpacing: '0.05em' }}>
          AI 数据分析工作台
        </div>
      </div>

      {/* 新建会话按钮 */}
      <div style={{ padding: '0 16px 8px', display: 'flex', gap: 8 }}>
        <div
          onClick={handleNewChat}
          style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            height: 40, borderRadius: 10, cursor: 'pointer',
            background: BRAND_LIME, color: BRAND_INK_TEXT,
            fontSize: 14, fontWeight: 600,
            transition: 'all 0.2s',
            boxShadow: '0 8px 22px rgba(255, 255, 255, 0.1)',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = '#C079FF'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = BRAND_LIME; }}
        >
          <PlusOutlined style={{ fontSize: 14 }} />
          新建分析
        </div>
        <div
          onClick={() => setSpaceCreateOpen(true)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: 40, height: 40, borderRadius: 10, cursor: 'pointer',
            background: BRAND_CARD, color: '#9B97AD',
            fontSize: 16, transition: 'all 0.2s',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255, 255, 255, 0.1)'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = BRAND_CARD; }}
          title="创建空间（连接你的数据库）"
        >
          <DatabaseOutlined />
        </div>
      </div>

      <div style={{ height: 1, background: BRAND_BORDER, margin: '0 16px' }} />

      {/* Space Switcher - GooeyNav */}
      <div style={{ padding: '12px 12px 4px' }}>
        <div style={{ fontSize: 11, color: BRAND_MUTED, fontWeight: 600, marginBottom: 8, paddingLeft: 4, letterSpacing: '0.5px' }}>
          分析空间
        </div>
        {spaces.length > 0 && (
          <GooeyNav
            orientation="vertical"
            items={spaces.map((s) => ({
              label: s.name,
              value: s.id,
              icon: <span style={{ fontSize: 15 }}>{SPACE_META[s.id]?.icon ?? <BarChartOutlined />}</span>,
            }))}
            initialActiveIndex={Math.max(0, spaces.findIndex((s) => s.id === activeSpaceId))}
            particleCount={12}
            particleDistances={[60, 6]}
            particleR={80}
            animationTime={500}
            timeVariance={250}
            colors={[1, 2, 3, 1, 2, 4]}
            onSelect={(item) => {
              if (!item.value || item.value === activeSpaceId) return;
              setActiveSpaceId(item.value);
              localStorage.setItem('dp_space', item.value);
              localStorage.removeItem('dp_session');
              setActiveSessionId(null);
              setMessages([]);
              setDrawerOpen(false);
            }}
          />
        )}
      </div>

      {/* Session List */}
      <div style={{ padding: '12px 8px 6px' }}>
        <div style={{ fontSize: 11, color: BRAND_MUTED, fontWeight: 600, letterSpacing: '0.5px', paddingLeft: 8 }}>
          历史会话
        </div>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0 8px 8px' }}>
        {sessions.length === 0 && (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#6E6A82', fontSize: 13 }}>暂无会话</div>
        )}
        {sessions.map((s) => (
          <div
            key={s.id}
            onClick={() => handleSelectSession(s.id)}
            style={{
              padding: '10px 12px', borderRadius: 6, cursor: 'pointer', marginBottom: 2,
              background: activeSessionId === s.id ? BRAND_SOFT : 'transparent',
              borderLeft: activeSessionId === s.id ? `3px solid ${BRAND_LIME}` : '3px solid transparent',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => { if (activeSessionId !== s.id) e.currentTarget.style.background = 'rgba(255, 255, 255, 0.05)'; }}
            onMouseLeave={(e) => {
              if (activeSessionId !== s.id) {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.borderLeft = '3px solid transparent';
              }
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden', flex: 1, minWidth: 0 }}>
              <MessageOutlined style={{ color: activeSessionId === s.id ? BRAND_LIME : '#6E6A82', fontSize: 13, flexShrink: 0 }} />
              <div style={{ overflow: 'hidden' }}>
                <div style={{
                  fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  color: activeSessionId === s.id ? BRAND_TEXT : '#9B97AD',
                  fontWeight: activeSessionId === s.id ? 500 : 400,
                }}>{s.title}</div>
                <div style={{ fontSize: 11, color: '#6E6A82' }}>{s.updated_at?.slice(5, 10)}</div>
              </div>
            </div>
            <DeleteOutlined style={{ color: '#48455A', fontSize: 12, flexShrink: 0 }}
              onClick={(e) => { e.stopPropagation(); handleDeleteSession(s.id); }} />
          </div>
        ))}
      </div>

      {/* User */}
      <div style={{ padding: '12px 16px', borderTop: `1px solid ${BRAND_BORDER}` }}>
        {user ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden' }}>
              <div style={{
                width: 32, height: 32, borderRadius: '50%', background: BRAND_LIME,
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: BRAND_INK_TEXT }}>{user.display_name[0]}</span>
              </div>
              <div style={{ overflow: 'hidden' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: BRAND_TEXT, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{user.display_name}</div>
                <div style={{ fontSize: 11, color: BRAND_MUTED }}>{user.role}</div>
              </div>
            </div>
            <Tooltip title="退出登录">
              <LogoutOutlined style={{ color: '#9B97AD', fontSize: 16, cursor: 'pointer' }}
                onClick={handleLogout} />
            </Tooltip>
          </div>
        ) : (
          <div
            onClick={() => setLoginOpen(true)}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              height: 38, borderRadius: 8, cursor: 'pointer',
              background: BRAND_CARD, color: BRAND_TEXT,
              fontSize: 13, fontWeight: 500, transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = BRAND_LIME; e.currentTarget.style.color = BRAND_INK_TEXT; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = BRAND_CARD; e.currentTarget.style.color = BRAND_TEXT; }}
          >
            <LoginOutlined style={{ fontSize: 14 }} />
            登录工作台
          </div>
        )}
      </div>
    </div>
  );

  if (!token) {
    return <LoginPage onLogin={handleLogin} onRegister={handleRegister} />;
  }

  return (
    <>
      <div className="pointer-events-none absolute inset-0 overflow-hidden" style={{ zIndex: 0, background: '#03010A' }}>
        <Ferrofluid
          colors={['#ffffff', '#DCD8E8', '#ffffff']}
          speed={0.32}
          scale={1.6}
          turbulence={0.85}
          fluidity={0.12}
          rimWidth={0.2}
          sharpness={3}
          shimmer={1.1}
          glow={1.4}
          flowDirection="down"
          opacity={0.55}
          mouseInteraction={false}
          mouseStrength={1}
          mouseRadius={0.3}
          dpr={Math.min(window.devicePixelRatio || 1, 1.5)}
        />
      </div>
      <Layout style={{ height: '100vh', overflow: 'hidden', background: 'transparent', position: 'relative', zIndex: 1 }}>
      {/* PC 侧边栏 */}
      <Sider
        width={260}
        breakpoint="lg"
        collapsedWidth={0}
        onBreakpoint={(broken) => { if (!broken) setDrawerOpen(false); }}
        style={{ background: BRAND_PANEL, borderRight: `1px solid ${BRAND_BORDER}`, overflow: 'hidden', padding: 0, backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR }}
        className="desktop-sider"
      >
        {sidebarContent}
      </Sider>

      {/* 移动端 Drawer */}
      <Drawer
        placement="left"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={280}
        styles={{ body: { padding: 0, background: BRAND_PANEL, backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR } }}
        className="mobile-drawer"
      >
        {sidebarContent}
      </Drawer>

      {/* 主区域 */}
      <Layout style={{ background: 'transparent' }}>
        {/* 顶栏 */}
        <div style={{
          height: 52, borderBottom: `1px solid ${BRAND_BORDER}`, display: 'flex',
          alignItems: 'center', padding: '0 16px', gap: 12,
          background: 'rgba(7, 17, 19, 0.55)', backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR,
        }}>
          <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}
            className="mobile-menu-btn" style={{ display: 'none', color: BRAND_TEXT }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
            <span style={{ fontSize: 16, color: spaceMeta.color }}>{spaceMeta.icon}</span>
            <Text strong ellipsis style={{ fontSize: 15, color: BRAND_TEXT }}>{activeSpace?.name}</Text>
            <Text style={{ fontSize: 12, color: BRAND_MUTED }} className="hide-mobile">
              · {activeSpace?.description}
            </Text>
          </div>
          {user && (
            <div className="mobile-user-avatar" style={{ display: 'none' }}>
              <div style={{
                width: 28, height: 28, borderRadius: '50%', background: BRAND_LIME,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: BRAND_INK_TEXT }}>{user.display_name[0]}</span>
              </div>
            </div>
          )}
        </div>

        {/* 对话区 */}
        <Content style={{ flex: 1, overflow: 'auto', display: 'flex', justifyContent: 'center', background: 'transparent' }}>
          <div style={{ maxWidth: 860, width: '100%', padding: '24px 20px' }}>
            {sessionLoading && (
              <div style={{ textAlign: 'center', padding: '60px 0' }}>
                <Spin /> <span style={{ marginLeft: 8, color: BRAND_MUTED, fontSize: 13 }}>加载中...</span>
              </div>
            )}

            {!sessionLoading && messages.length === 0 && !loading && (
              <div className="relative" style={{ padding: '24px 0 24px' }}>
                <div style={{ marginBottom: 20, zIndex: 1 }}>
                  <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.02em', color: '#F4F4F8', marginBottom: 6, lineHeight: 1.2 }}>
                    {activeSpace?.name || 'DataPilot 工作台'}
                  </div>
                  <div style={{ fontSize: 13, color: '#9B97AD', lineHeight: '20px' }}>
                    我已识别这个空间的数据，下面这些问题可以直接开始分析。
                  </div>
                </div>

                <div className="relative" style={{ zIndex: 1 }}>
                {dataMapLoading && (
                  <div style={{
                    border: `1px solid ${BRAND_BORDER}`, background: BRAND_PANEL, borderRadius: 12,
                    padding: 16, color: BRAND_MUTED, display: 'flex', alignItems: 'center', gap: 10,
                    backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR,
                  }}>
                    <Spin size="small" /> 正在读取数据地图...
                  </div>
                )}

                {!dataMapLoading && dataMap && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <div style={{
                      background: BRAND_PANEL, border: `1px solid ${BRAND_BORDER}`, borderRadius: 12,
                      padding: 14,
                      backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR,
                    }}>
                      <div style={{ marginBottom: 10 }}>
                        <div style={{ color: BRAND_TEXT, fontSize: 14, fontWeight: 700, marginBottom: 4 }}>先从这些问题开始</div>
                        <div style={{ color: BRAND_MUTED, fontSize: 12 }}>
                          不需要知道表名或字段名，点一个问题就能开始分析。
                        </div>
                      </div>
                      <DataMapBlock dataMap={{ ...dataMap, tables: [] }} compact onQuestionClick={handleQuestionClick} />
                    </div>

                    <div style={{
                      background: BRAND_PANEL, border: `1px solid ${BRAND_BORDER}`, borderRadius: 12,
                      padding: 14,
                      backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR,
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
                        <div>
                          <div style={{ color: BRAND_TEXT, fontSize: 14, fontWeight: 700, marginBottom: 4 }}>已识别的业务数据</div>
                          <div style={{ color: BRAND_MUTED, fontSize: 12 }}>
                            共 {dataMap.summary.table_count} 类数据、{dataMap.summary.metric_count} 个指标
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleSend('现在数据库有什么表')}
                          style={{
                            border: `1px solid rgba(255, 255, 255, 0.1)`, background: 'transparent', color: '#DCD8E8',
                            borderRadius: 14, padding: '5px 11px', cursor: 'pointer', fontSize: 11, flexShrink: 0,
                          }}
                        >
                          让 AI 说明
                        </button>
                      </div>
                      <DataMapBlock dataMap={{ ...dataMap, recommended_questions: [] }} compact onQuestionClick={handleQuestionClick} />
                    </div>
                  </div>
                )}

                {!dataMapLoading && !dataMap && (
                  <div style={{
                    border: `1px solid ${BRAND_BORDER}`, background: BRAND_PANEL, borderRadius: 16,
                    padding: 24, color: BRAND_MUTED,
                    backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR,
                  }}>
                    暂时无法读取数据地图。你仍然可以在下方直接提问，或重新创建空间刷新数据结构。
                  </div>
                )}
                </div>
              </div>
            )}

            {!sessionLoading && messages.map((msg, i) => (
              <ChatMessage key={i} role={msg.role} content={msg.content} data={msg.data}
                onCandidateClick={handleCandidateClick}
                onDataMapQuestionClick={handleQuestionClick}
                onOpenTrace={handleOpenTrace} />
            ))}

            {/* ===== Assistant Pending Bubble — 所有进度统一在这里 ===== */}
            {(loading || streamingAnswer) && (
              <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
                <div style={{
                  width: 34, height: 34, borderRadius: 10, flexShrink: 0,
                  background: BRAND_DEEP,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <img
                    src={datapilotAppIcon}
                    alt=""
                    style={{ width: 34, height: 34, borderRadius: 10, display: 'block' }}
                  />
                </div>
                <div style={{ flex: 1, fontSize: 14, lineHeight: '22px', color: BRAND_TEXT }}>

                  {/* 普通查询进度 / 多意图进度步骤 */}
                  {sseSteps.length > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      {sseSteps.map((step, i) => {
                        // route:xxx 格式特殊处理
                        const isRoute = step.node.startsWith('route:');
                        const routeLabel = isRoute ? step.node.slice(6) : null;
                        const isStep = step.node.startsWith('step_');
                        const isSummary = step.node === 'summary';

                        return (
                          <div key={i} style={{
                            padding: '3px 0', fontSize: 13, display: 'flex', alignItems: 'center', gap: 6,
                            color: step.status === 'done' ? '#52c41a'
                              : step.status === 'denied' ? '#ff4d4f'
                              : BRAND_LIME,
                          }}>
                            {step.status === 'done' && <span>✓</span>}
                            {step.status === 'running' && <Spin size="small" />}
                            {step.status === 'process' && <Spin size="small" />}
                            {step.status === 'denied' && <span>✗</span>}
                            {isRoute && routeLabel}
                            {isStep && step.status === 'running' && `正在执行第 ${step.node.slice(5)} 步...`}
                            {isStep && step.status === 'done' && `第 ${step.node.slice(5)} 步完成`}
                            {isSummary && step.status === 'running' && '正在生成分析总结...'}
                            {!isRoute && !isStep && !isSummary && step.node}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {/* Plan 进度（计划 + 步骤列表） */}
                  {planProgress && (
                    <div style={{ marginBottom: 12 }}>
                      {planProgress.phase === 'planning' && (
                        <div style={{ color: BRAND_LIME, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                          <Spin size="small" /> 正在制定分析计划...
                        </div>
                      )}
                      {planProgress.phase !== 'planning' && planProgress.goal && (
                        <div style={{ marginBottom: 8 }}>
                          <span style={{ color: '#52c41a' }}>✓</span>
                          <span style={{ color: BRAND_TEXT, fontWeight: 500 }}> 我会分 {planProgress.steps.length} 步分析：</span>
                        </div>
                      )}
                      {planProgress.phase !== 'planning' && planProgress.steps.map((step, idx) => (
                        <div key={step.id} style={{ padding: '3px 0', fontSize: 13, color: '#B5B1C6', display: 'flex', alignItems: 'center', gap: 6 }}>
                          {step.status === 'done' && <span style={{ color: '#52c41a' }}>✓</span>}
                          {step.status === 'running' && <span style={{ color: BRAND_LIME }}><Spin size="small" /></span>}
                          {step.status === 'pending' && <span style={{ color: '#6E6A82' }}>{idx + 1}.</span>}
                          {step.status === 'done' && <span>{idx + 1}.</span>}
                          {' '}{step.title}
                          {step.status === 'done' && step.rows !== undefined && (
                            <span style={{ color: BRAND_MUTED, fontSize: 12 }}>— {step.rows} 条数据</span>
                          )}
                          {step.status === 'running' && (
                            <span style={{ color: BRAND_MUTED, fontSize: 12 }}>查询中...</span>
                          )}
                        </div>
                      ))}
                      {planProgress.phase === 'summarizing' && !streamingAnswer && (
                        <div style={{ color: BRAND_LIME, marginTop: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                          <Spin size="small" /> 正在生成分析总结...
                        </div>
                      )}
                    </div>
                  )}

                  {/* 流式回答逐字显示 */}
                  {streamingAnswer && (
                    <div style={{ whiteSpace: 'pre-wrap' }}>
                      {streamingAnswer}
                      <span style={{
                        display: 'inline-block', width: 2, height: 16,
                        background: BRAND_LIME, marginLeft: 2,
                        animation: 'blink 1s step-end infinite',
                      }} />
                    </div>
                  )}

                  {/* 纯 loading（无事件也无流式） */}
                  {!streamingAnswer && sseSteps.length === 0 && !planProgress && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: BRAND_MUTED, fontSize: 13 }}>
                      <Spin size="small" /> 思考中...
                    </div>
                  )}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </Content>

        {/* 输入区 */}
        <div style={{ background: 'transparent', padding: '20px 24px' }}>
          <div style={{ maxWidth: 800, margin: '0 auto' }}>
            <div style={{
              display: 'flex', gap: 8, alignItems: 'flex-end',
              background: BRAND_PANEL, borderRadius: 24, padding: '6px 6px 6px 20px',
              border: '1px solid rgba(255, 255, 255, 0.1)', transition: 'border-color 0.2s',
              boxShadow: '0 18px 44px rgba(0,0,0,0.22)',
              backdropFilter: GLASS_BLUR, WebkitBackdropFilter: GLASS_BLUR,
            }}>
              <Input.TextArea
                className="datapilot-input"
                style={{
                  flex: 1, border: 'none', outline: 'none', background: 'transparent',
                  fontSize: 14, padding: '8px 0', minWidth: 0, color: BRAND_TEXT,
                  resize: 'none',
                }}
                autoSize={{ minRows: 1, maxRows: 4 }}
                placeholder={`Ask DataPilot Agent...`}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                disabled={loading}
              />
              {loading ? (
                <Button shape="circle" icon={<div style={{ fontSize: 14 }}>■</div>}
                  onClick={handleStopGeneration}
                  style={{ width: 40, height: 40, flexShrink: 0, background: '#ff4d4f', borderColor: '#ff4d4f', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }} />
              ) : (
                <Button type="primary" shape="circle" icon={<SendOutlined />}
                  onClick={() => handleSend()} disabled={!inputValue.trim()}
                  style={{ width: 40, height: 40, flexShrink: 0, background: BRAND_LIME, color: BRAND_INK_TEXT, borderColor: BRAND_LIME, boxShadow: '0 2px 12px rgba(192, 121, 255, 0.32)' }} />
              )}
            </div>
            <div style={{ textAlign: 'center', marginTop: 8, fontSize: 11, color: '#6E6A82' }}>
              DataPilot 可能会犯错，请核实重要数据
            </div>
          </div>
        </div>
      </Layout>

      <SpaceCreateModal
        open={spaceCreateOpen}
        onClose={() => setSpaceCreateOpen(false)}
        onCreated={() => { listSpaces().then(setSpaces).catch(() => {}); }}
      />
      <TraceDetailModal
        open={traceOpen}
        onClose={() => setTraceOpen(false)}
        traceId={traceData.traceId}
        steps={traceData.steps}
        question={traceData.question}
        sql={traceData.sql}
      />
    </Layout>
    </>
  );
}

export default App;
