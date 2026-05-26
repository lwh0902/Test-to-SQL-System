import { useState, useRef, useEffect, useCallback } from 'react';
import { Input, Button, Layout, Typography, Space, Tag, Spin, Tooltip, Drawer } from 'antd';
import {
  SendOutlined,
  PlusOutlined,
  MessageOutlined,
  BugOutlined,
  ShoppingCartOutlined,
  BarChartOutlined,
  DeleteOutlined,
  LoginOutlined,
  MenuOutlined,
  RobotOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { streamChat } from './services/api';
import type { SSEEvent } from './services/api';
import type { ChatResponse, MetricCandidate } from './types';
import {
  listSpaces,
  listSessions,
  createSession,
  deleteSession,
  login,
} from './services/api';
import type { Space as SpaceInfo, Session } from './services/api';

import ChatMessage from './components/ChatMessage';
import LoginModal from './components/LoginModal';

const { Sider, Content } = Layout;
const { Text } = Typography;

const SPACE_META: Record<string, { icon: React.ReactNode; color: string }> = {
  tech_quality: { icon: <BugOutlined />, color: '#722ed1' },
  ecommerce: { icon: <ShoppingCartOutlined />, color: '#fa8c16' },
};

function App() {
  const [token, setToken] = useState<string | null>(localStorage.getItem('dp_token'));
  const [user, setUser] = useState<{ display_name: string; role: string } | null>(() => {
    const saved = localStorage.getItem('dp_user');
    return saved ? JSON.parse(saved) : null;
  });
  const [loginOpen, setLoginOpen] = useState(false);

  const [spaces, setSpaces] = useState<SpaceInfo[]>([]);
  const [activeSpaceId, setActiveSpaceId] = useState<string>('tech_quality');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);

  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [sseSteps, setSseSteps] = useState<{ node: string; status: string }[]>([]);
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [messages, setMessages] = useState<
    { role: 'user' | 'assistant'; content: string; data?: ChatResponse }[]
  >([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // 移动端侧边栏
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => { listSpaces().then(setSpaces); }, []);

  const loadSessions = useCallback(async (spaceId: string) => {
    const list = await listSessions(spaceId);
    setSessions(list);
  }, []);

  useEffect(() => { loadSessions(activeSpaceId); }, [activeSpaceId, loadSessions]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, sseSteps]);

  const handleLogin = async (username: string, password: string) => {
    const res = await login({ username, password });
    setToken(res.access_token);
    setUser(res.user);
    localStorage.setItem('dp_token', res.access_token);
    localStorage.setItem('dp_user', JSON.stringify(res.user));
    setLoginOpen(false);
  };

  const handleLogout = () => {
    setToken(null); setUser(null);
    localStorage.removeItem('dp_token');
    localStorage.removeItem('dp_user');
  };

  const handleNewChat = async () => {
    const session = await createSession(activeSpaceId, '新对话');
    setActiveSessionId(session.id);
    setMessages([]); setSseSteps([]);
    loadSessions(activeSpaceId);
    setDrawerOpen(false);
  };

  const handleSelectSession = async (sessionId: string) => {
    setActiveSessionId(sessionId);
    const sess = await import('./services/api').then((m) => m.getSession(sessionId));
    const msgs: typeof messages = [];
    for (const m of sess.messages || []) {
      if (m.role === 'user') msgs.push({ role: 'user', content: m.content });
      else if (m.meta) msgs.push({ role: 'assistant', content: m.content, data: m.meta as ChatResponse });
    }
    setMessages(msgs); setSseSteps([]);
    setDrawerOpen(false);
  };

  const handleDeleteSession = async (sessionId: string) => {
    await deleteSession(sessionId);
    if (activeSessionId === sessionId) { setActiveSessionId(null); setMessages([]); }
    loadSessions(activeSpaceId);
  };

  const handleSend = (question?: string, candidate?: MetricCandidate) => {
    const q = question || inputValue.trim();
    if (!q || loading) return;
    setInputValue(''); setLoading(true); setSseSteps([]); setStreamingAnswer('');
    setMessages((prev) => [...prev, { role: 'user', content: q }]);
    streamChat(q, activeSpaceId, activeSessionId,
      (event: SSEEvent) => {
        const status = event.data?.status === 'passed' || event.data?.status === 'done' ? 'done'
          : event.data?.status === 'denied' ? 'denied' : 'process';
        setSseSteps((prev) => [...prev, { node: event.event, status }]);
      },
      (res: ChatResponse) => {
        setMessages((prev) => [...prev, { role: 'assistant', content: res.answer || res.message || '', data: res }]);
        setLoading(false); setSseSteps([]); setStreamingAnswer(''); loadSessions(activeSpaceId);
      },
      (err: Error) => {
        setMessages((prev) => [...prev, { role: 'assistant', content: `请求失败: ${err.message}`, data: { type: 'error', trace_id: '', message: err.message, columns: [], rows: [], trace: [], candidates: [] } }]);
        setLoading(false); setSseSteps([]); setStreamingAnswer('');
      },
      candidate?.key, candidate?.default_query_type,
      user?.role,
      (chunk: string) => { setStreamingAnswer((prev) => prev + chunk); },
    );
  };

  const handleCandidateClick = (c: MetricCandidate) => handleSend(`${c.name}趋势`, c);
  const activeSpace = spaces.find((s) => s.id === activeSpaceId);
  const spaceMeta = SPACE_META[activeSpaceId] || { icon: <BarChartOutlined />, color: '#1890ff' };

  // ---- 侧边栏内容（PC 和移动端共用） ----
  const sidebarContent = (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Brand */}
      <div style={{ padding: '20px 20px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: '#6F42C1',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <RobotOutlined style={{ color: '#fff', fontSize: 18 }} />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15, lineHeight: '20px', color: '#212529' }}>DataPilot</div>
            <div style={{ fontSize: 11, color: '#6C757D' }}>AI 数据分析工作台</div>
          </div>
        </div>
      </div>

      {/* 新建会话按钮 */}
      <div style={{ padding: '0 16px 16px' }}>
        <div
          onClick={handleNewChat}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            height: 40, borderRadius: 10, cursor: 'pointer',
            background: '#6F42C1', color: '#fff',
            fontSize: 14, fontWeight: 600,
            transition: 'all 0.2s',
            boxShadow: '0 2px 8px rgba(111,66,193,0.25)',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = '#5A35A6'; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = '#6F42C1'; }}
        >
          <PlusOutlined style={{ fontSize: 14 }} />
          新建分析
        </div>
      </div>

      <div style={{ height: 1, background: '#E9ECEF', margin: '0 16px' }} />

      {/* Space Switcher - 列表式 */}
      <div style={{ padding: '12px 8px 4px' }}>
        <div style={{ fontSize: 11, color: '#6C757D', fontWeight: 600, marginBottom: 6, paddingLeft: 8, letterSpacing: '0.5px' }}>
          分析空间
        </div>
        {spaces.map((s) => {
          const meta = SPACE_META[s.id] || { color: '#1890ff' };
          const isActive = activeSpaceId === s.id;
          return (
            <div
              key={s.id}
              onClick={() => { setActiveSpaceId(s.id); setActiveSessionId(null); setMessages([]); setDrawerOpen(false); }}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '10px 12px', borderRadius: 6, cursor: 'pointer',
                background: isActive ? '#F3EEFA' : 'transparent',
                borderLeft: isActive ? '3px solid #6F42C1' : '3px solid transparent',
                transition: 'all 0.15s',
                marginBottom: 2,
              }}
              onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = '#F0F0F2'; }}
              onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
            >
              <span style={{ fontSize: 16, color: isActive ? '#6F42C1' : '#999' }}>{SPACE_META[s.id]?.icon}</span>
              <span style={{ fontSize: 13, color: isActive ? '#212529' : '#6C757D', fontWeight: isActive ? 600 : 400 }}>
                {s.name}
              </span>
            </div>
          );
        })}
      </div>

      {/* Session List */}
      <div style={{ padding: '12px 8px 6px' }}>
        <div style={{ fontSize: 11, color: '#6C757D', fontWeight: 600, letterSpacing: '0.5px', paddingLeft: 8 }}>
          历史会话
        </div>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0 8px 8px' }}>
        {sessions.length === 0 && (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#ccc', fontSize: 13 }}>暂无会话</div>
        )}
        {sessions.map((s) => (
          <div
            key={s.id}
            onClick={() => handleSelectSession(s.id)}
            style={{
              padding: '10px 12px', borderRadius: 6, cursor: 'pointer', marginBottom: 2,
              background: activeSessionId === s.id ? '#F3EEFA' : 'transparent',
              borderLeft: activeSessionId === s.id ? '3px solid #6F42C1' : '3px solid transparent',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              transition: 'all 0.15s',
            }}
            onMouseEnter={(e) => { if (activeSessionId !== s.id) e.currentTarget.style.background = '#F0F0F2'; }}
            onMouseLeave={(e) => {
              if (activeSessionId !== s.id) {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.borderLeft = '3px solid transparent';
              }
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden', flex: 1, minWidth: 0 }}>
              <MessageOutlined style={{ color: activeSessionId === s.id ? '#6F42C1' : '#999', fontSize: 13, flexShrink: 0 }} />
              <div style={{ overflow: 'hidden' }}>
                <div style={{
                  fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  color: activeSessionId === s.id ? '#212529' : '#555',
                  fontWeight: activeSessionId === s.id ? 500 : 400,
                }}>{s.title}</div>
                <div style={{ fontSize: 11, color: '#bbb' }}>{s.updated_at?.slice(5, 10)}</div>
              </div>
            </div>
            <DeleteOutlined style={{ color: '#ddd', fontSize: 12, flexShrink: 0 }}
              onClick={(e) => { e.stopPropagation(); handleDeleteSession(s.id); }} />
          </div>
        ))}
      </div>

      {/* User */}
      <div style={{ padding: '12px 16px', borderTop: '1px solid #E9ECEF' }}>
        {user ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, overflow: 'hidden' }}>
              <div style={{
                width: 32, height: 32, borderRadius: '50%', background: '#6F42C1',
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: '#fff' }}>{user.display_name[0]}</span>
              </div>
              <div style={{ overflow: 'hidden' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#212529', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{user.display_name}</div>
                <div style={{ fontSize: 11, color: '#6C757D' }}>{user.role}</div>
              </div>
            </div>
            <SettingOutlined style={{ color: '#999', fontSize: 16, cursor: 'pointer' }}
              onClick={handleLogout} />
          </div>
        ) : (
          <div
            onClick={() => setLoginOpen(true)}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              height: 38, borderRadius: 8, cursor: 'pointer',
              background: '#F3EEFA', color: '#6F42C1',
              fontSize: 13, fontWeight: 500, transition: 'all 0.2s',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = '#6F42C1'; e.currentTarget.style.color = '#fff'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = '#F3EEFA'; e.currentTarget.style.color = '#6F42C1'; }}
          >
            <LoginOutlined style={{ fontSize: 14 }} />
            登录工作台
          </div>
        )}
      </div>
    </div>
  );

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden', background: '#fff' }}>
      {/* PC 侧边栏 */}
      <Sider
        width={260}
        breakpoint="lg"
        collapsedWidth={0}
        onBreakpoint={(broken) => { if (!broken) setDrawerOpen(false); }}
        style={{ background: '#F8F9FA', borderRight: '1px solid #E9ECEF', overflow: 'hidden', padding: 0 }}
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
        styles={{ body: { padding: 0, background: '#F8F9FA' } }}
        className="mobile-drawer"
      >
        {sidebarContent}
      </Drawer>

      {/* 主区域 */}
      <Layout style={{ background: '#fff' }}>
        {/* 顶栏 */}
        <div style={{
          height: 52, borderBottom: '1px solid #E9ECEF', display: 'flex',
          alignItems: 'center', padding: '0 16px', gap: 12,
        }}>
          <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}
            className="mobile-menu-btn" style={{ display: 'none' }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
            <span style={{ fontSize: 16, color: spaceMeta.color }}>{spaceMeta.icon}</span>
            <Text strong ellipsis style={{ fontSize: 15 }}>{activeSpace?.name}</Text>
            <Text type="secondary" style={{ fontSize: 12 }} className="hide-mobile">
              · {activeSpace?.description}
            </Text>
          </div>
          {user && (
            <div className="mobile-user-avatar" style={{ display: 'none' }}>
              <div style={{
                width: 28, height: 28, borderRadius: '50%', background: '#f0edf8',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#6F42C1' }}>{user.display_name[0]}</span>
              </div>
            </div>
          )}
        </div>

        {/* 对话区 */}
        <Content style={{ flex: 1, overflow: 'auto', display: 'flex', justifyContent: 'center' }}>
          <div style={{ maxWidth: 860, width: '100%', padding: '24px 20px' }}>
            {messages.length === 0 && !loading && (
              <div style={{ textAlign: 'center', padding: '120px 0 40px' }}>
                <div style={{
                  width: 80, height: 80, borderRadius: 20, margin: '0 auto 24px',
                  background: '#6F42C1',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  boxShadow: '0 4px 16px rgba(111,66,193,0.25)',
                }}>
                  <RobotOutlined style={{ color: '#fff', fontSize: 36 }} />
                </div>
                <div style={{ fontSize: 24, fontWeight: 700, color: '#212529', marginBottom: 8 }}>
                  Hello, I am DataPilot Agent
                </div>
                <div style={{ fontSize: 15, color: '#6C757D', marginBottom: 40, lineHeight: '22px' }}>
                  AI 驱动的数据分析助手，帮你快速洞察数据
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
                  {activeSpaceId === 'ecommerce' && (
                    [
                      { text: '最近7天销售额趋势', q: '最近7天销售额趋势怎么样' },
                      { text: '商品销量排行', q: '商品销量排行top10' },
                      { text: '为什么最近销售额下降', q: '为什么最近销售额下降' },
                      { text: '订单数环比对比', q: '订单数环比对比' },
                    ].map((item) => (
                      <div key={item.text} onClick={() => handleSend(item.q)}
                        style={{
                          padding: '10px 20px', borderRadius: 24, cursor: 'pointer',
                          border: '1px solid #6F42C1', fontSize: 13, color: '#6F42C1',
                          transition: 'all 0.2s', background: '#fff',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = '#6F42C1'; e.currentTarget.style.color = '#fff'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = '#fff'; e.currentTarget.style.color = '#6F42C1'; }}
                      >
                        {item.text}
                      </div>
                    ))
                  )}
                  {activeSpaceId === 'tech_quality' && (
                    [
                      { text: '扫描成功率趋势', q: '最近7天扫描成功率趋势' },
                      { text: '异常拆解分析', q: '为什么最近成功率下降' },
                      { text: '错误分布', q: '最近7天错误分布' },
                      { text: 'API响应时间', q: '最近7天API响应时间趋势' },
                    ].map((item) => (
                      <div key={item.text} onClick={() => handleSend(item.q)}
                        style={{
                          padding: '10px 20px', borderRadius: 24, cursor: 'pointer',
                          border: '1px solid #6F42C1', fontSize: 13, color: '#6F42C1',
                          transition: 'all 0.2s', background: '#fff',
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.background = '#6F42C1'; e.currentTarget.style.color = '#fff'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = '#fff'; e.currentTarget.style.color = '#6F42C1'; }}
                      >
                        {item.text}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <ChatMessage key={i} role={msg.role} content={msg.content} data={msg.data}
                onCandidateClick={handleCandidateClick} />
            ))}

            {/* SSE 进度 */}
            {(sseSteps.length > 0 || loading) && (
              <div style={{ display: 'flex', gap: 8, padding: '12px 0', alignItems: 'center', flexWrap: 'wrap' }}>
                {sseSteps.map((step, i) => (
                  <span key={i} style={{
                    fontSize: 12, padding: '2px 8px', borderRadius: 4,
                    background: step.status === 'done' ? '#f6ffed' : step.status === 'denied' ? '#fff2f0' : '#f0edf8',
                    color: step.status === 'done' ? '#52c41a' : step.status === 'denied' ? '#ff4d4f' : '#6F42C1',
                  }}>
                    {step.status === 'done' ? '✓' : '○'} {step.node}
                  </span>
                ))}
                {loading && sseSteps.length > 0 && <Spin size="small" />}
              </div>
            )}
            {loading && sseSteps.length === 0 && (
              <div style={{ padding: '32px 0', textAlign: 'center' }}>
                <Spin /> <span style={{ marginLeft: 8, color: '#999', fontSize: 13 }}>思考中...</span>
              </div>
            )}

            {/* 流式回答逐字显示 */}
            {streamingAnswer && (
              <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
                <div style={{
                  width: 34, height: 34, borderRadius: 10, flexShrink: 0,
                  background: '#6F42C1',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <RobotOutlined style={{ color: '#fff', fontSize: 16 }} />
                </div>
                <div style={{
                  flex: 1, fontSize: 14, lineHeight: '22px', color: '#333',
                  whiteSpace: 'pre-wrap',
                }}>
                  {streamingAnswer}
                  <span style={{
                    display: 'inline-block', width: 2, height: 16,
                    background: '#6F42C1', marginLeft: 2,
                    animation: 'blink 1s step-end infinite',
                  }} />
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </Content>

        {/* 输入区 */}
        <div style={{ background: '#fff', padding: '20px 24px' }}>
          <div style={{ maxWidth: 800, margin: '0 auto' }}>
            <div style={{
              display: 'flex', gap: 8, alignItems: 'center',
              background: '#fff', borderRadius: 28, padding: '6px 6px 6px 20px',
              border: '1px solid #DEE2E6', transition: 'border-color 0.2s',
              boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
            }}>
              <input
                style={{
                  flex: 1, border: 'none', outline: 'none', background: 'transparent',
                  fontSize: 14, padding: '8px 0', minWidth: 0, color: '#343A40',
                }}
                placeholder={`Ask DataPilot Agent...`}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                disabled={loading}
              />
              <Button type="primary" shape="circle" icon={<SendOutlined />}
                onClick={() => handleSend()} loading={loading}
                style={{ width: 40, height: 40, flexShrink: 0, background: '#6F42C1', borderColor: '#6F42C1', boxShadow: '0 2px 8px rgba(111,66,193,0.3)' }} />
            </div>
            <div style={{ textAlign: 'center', marginTop: 8, fontSize: 11, color: '#ADB5BD' }}>
              DataPilot 可能会犯错，请核实重要数据
            </div>
          </div>
        </div>
      </Layout>

      <LoginModal open={loginOpen} onLogin={handleLogin} onClose={() => setLoginOpen(false)} />
    </Layout>
  );
}

export default App;
