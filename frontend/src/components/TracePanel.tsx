import { Typography, Tag } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import type { TraceStep } from '../types';

const NODE_LABELS: Record<string, string> = {
  planner: '理解问题',
  clarification: '意图澄清',
  metric_resolver: '匹配指标',
  metric_resolved: '匹配指标',
  permission_guard: '权限校验',
  guard_check: '安全校验',
  sql_generator: '生成 SQL',
  sql_generated: '生成 SQL',
  sql_guard: 'SQL 安全校验',
  query_executor: '执行查询',
  query_done: '执行查询',
};

interface Props {
  trace: TraceStep[];
  loading: boolean;
}

export default function TracePanel({ trace, loading }: Props) {
  if (!trace.length && !loading) return null;

  return (
    <div>
      <Typography.Text type="secondary" style={{ marginBottom: 8, display: 'block', fontSize: 13 }}>
        执行步骤
      </Typography.Text>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {trace.map((step, i) => {
          const label = NODE_LABELS[step.node] || step.node;
          const isDone = step.status === 'done' || step.status === 'passed';
          const isDenied = step.status === 'denied';

          const outputEntries = Object.entries(step.output || {});
          const hasOutput = outputEntries.length > 0;
          const deniedMessage = typeof step.output?.message === 'string' ? step.output.message : '';

          return (
            <div key={i}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {isDone ? (
                  <CheckCircleOutlined style={{ color: '#52c41a' }} />
                ) : isDenied ? (
                  <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
                ) : (
                  <LoadingOutlined style={{ color: '#C079FF' }} />
                )}
                <span>{label}</span>
                {isDone && <Tag color="success" style={{ marginLeft: 4 }}>通过</Tag>}
                {isDenied && <Tag color="error" style={{ marginLeft: 4 }}>拒绝</Tag>}
              </div>
              {isDenied && deniedMessage && (
                <div style={{ marginLeft: 24, marginTop: 2, fontSize: 12, color: '#ff4d4f' }}>
                  {deniedMessage}
                </div>
              )}
              {hasOutput && !isDenied && (
                <details style={{ marginLeft: 24, marginTop: 2 }}>
                  <summary style={{ fontSize: 12, color: '#9B97AD', cursor: 'pointer' }}>详情</summary>
                  <pre style={{ fontSize: 11, color: '#6E6A82', margin: '4px 0', whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(step.output, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          );
        })}
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#9B97AD' }}>
            <LoadingOutlined />
            <span>处理中...</span>
          </div>
        )}
      </div>
    </div>
  );
}
