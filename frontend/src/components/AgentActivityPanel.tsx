import { Badge, Card, Spin, Tag } from 'antd';

export interface AgentActivity {
  agent: string;
  status: string;
  artifact_type?: string;
  artifact_id?: string;
  error?: string;
  step?: string;
  summary?: string;
  procedure?: string;
  mode?: string;
  format?: string;
}

const labels: Record<string, string> = {
  supervisor: '主管 Agent',
  query: '查询 Agent',
  insight: '洞察 Agent',
  report: '报告 Agent',
  review: '审核 Agent',
  export: '导出 Agent',
};

const statusText: Record<string, string> = {
  queued: '排队',
  running: '进行中',
  completed: '完成',
  failed: '失败',
  rejected: '未通过',
  retryable: '可重试',
  reused: '复用',
};

const stepLabels: Record<string, string> = {
  observe: '观察',
  extract: '提取',
  verify: '核对',
  label: '标注',
  commit: '落盘',
  outline: '大纲',
  draft: '起草',
  cite_check: '引用检查',
  revise: '修订',
  rules_gate: '规则门禁',
  critic: '质量审查',
  render: '渲染',
  run: '执行',
};

function statusBadge(status: string) {
  if (status === 'running') return <Spin size="small" />;
  if (status === 'completed' || status === 'reused') return <Badge status="success" />;
  if (status === 'failed' || status === 'rejected') return <Badge status="error" />;
  if (status === 'retryable') return <Badge status="warning" />;
  return <Badge status="processing" />;
}

export default function AgentActivityPanel({ activities }: { activities: AgentActivity[] }) {
  if (!activities.length) return null;

  const current = [...activities].reverse().find((a) => a.status === 'running');
  const failed = activities.filter((a) => a.status === 'failed' || a.status === 'rejected');
  const done = activities.filter((a) => a.status === 'completed' || a.status === 'reused').length;

  return (
    <Card
      size="small"
      title={
        <>
          深度诊断协作
          <Tag style={{ marginLeft: 8, background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.12)', color: '#D8D8E0' }}>
            {done}/{activities.length}
          </Tag>
          {current ? (
            <Tag style={{ marginLeft: 8, background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.12)', color: '#D8D8E0' }}>
              当前：{labels[current.agent] || current.agent}
              {current.step ? ` · ${stepLabels[current.step] || current.step}` : ''}
            </Tag>
          ) : null}
          {failed.length > 0 ? (
            <Tag color="error" style={{ marginLeft: 8 }}>{failed.length} 项异常</Tag>
          ) : null}
        </>
      }
      style={{ marginBottom: 12, background: 'rgba(255,255,255,.04)' }}
    >
      {activities.map((item, index) => {
        const stepText = item.step ? stepLabels[item.step] || item.step : '';
        const detail =
          item.summary ||
          item.artifact_type ||
          (stepText ? `${stepText} · ${item.status}` : item.status);
        return (
          <div
            key={`${item.agent}-${item.step || ''}-${index}`}
            style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '4px 0' }}
          >
            {statusBadge(item.status)}
            <span>{labels[item.agent] || item.agent}</span>
            {stepText ? (
              <Tag style={{ marginInlineEnd: 0 }} color="blue">
                {stepText}
              </Tag>
            ) : null}
            <span style={{ opacity: 0.65, fontSize: 12 }}>
              {statusText[item.status] || item.status}
              {detail ? ` · ${detail}` : ''}
              {item.artifact_id ? ` · ${String(item.artifact_id).slice(0, 12)}…` : ''}
              {item.format ? ` · ${item.format}` : ''}
              {item.error ? ` · ${item.error}` : ''}
            </span>
          </div>
        );
      })}
    </Card>
  );
}
