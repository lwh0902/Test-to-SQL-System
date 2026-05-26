import { Modal, Typography, Timeline, Descriptions, Tag } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import type { TraceStep } from '../types';

interface Props {
  open: boolean;
  onClose: () => void;
  traceId: string;
  steps: TraceStep[];
  question?: string;
  sql?: string;
}

const NODE_LABELS: Record<string, string> = {
  planner: '理解问题',
  clarification: '意图澄清',
  metric_resolver: '匹配指标',
  permission_guard: '权限校验',
  sql_generator: '生成 SQL',
  sql_guard: 'SQL 安全校验',
  query_executor: '执行查询',
};

export default function TraceDetailModal({ open, onClose, traceId, steps = [], question, sql }: Props) {
  return (
    <Modal
      title={`Trace 详情`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={680}
    >
      <Descriptions size="small" column={1} bordered style={{ marginBottom: 16 }}>
        <Descriptions.Item label="Trace ID">
          <Typography.Text copyable style={{ fontSize: 12 }}>{traceId}</Typography.Text>
        </Descriptions.Item>
        {question && (
          <Descriptions.Item label="问题">{question}</Descriptions.Item>
        )}
      </Descriptions>

      <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
        执行步骤
      </Typography.Text>
      <Timeline
        items={steps.map((step, i) => {
          const isDone = step.status === 'done' || step.status === 'passed';
          const isDenied = step.status === 'denied';
          const label = NODE_LABELS[step.node] || step.node;

          return {
            color: isDenied ? 'red' : isDone ? 'green' : 'blue',
            dot: isDenied ? (
              <CloseCircleOutlined />
            ) : isDone ? (
              <CheckCircleOutlined />
            ) : (
              <ClockCircleOutlined />
            ),
            children: (
              <div key={i}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontWeight: 500 }}>{label}</span>
                  <Tag color={isDone ? 'success' : isDenied ? 'error' : 'processing'}>
                    {step.status}
                  </Tag>
                </div>
                {Object.keys(step.output || {}).length > 0 && (
                  <pre
                    style={{
                      fontSize: 11,
                      background: '#f5f5f5',
                      padding: 8,
                      borderRadius: 4,
                      marginTop: 4,
                      maxHeight: 120,
                      overflow: 'auto',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {JSON.stringify(step.output, null, 2)}
                  </pre>
                )}
              </div>
            ),
          };
        })}
      />

      {sql && (
        <div style={{ marginTop: 12 }}>
          <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>
            生成的 SQL
          </Typography.Text>
          <pre
            style={{
              fontSize: 12,
              background: '#f5f5f5',
              padding: 10,
              borderRadius: 4,
              maxHeight: 150,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
            }}
          >
            {sql}
          </pre>
        </div>
      )}
    </Modal>
  );
}
