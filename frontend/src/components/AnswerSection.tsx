import { Tag, Typography } from 'antd';
import type { ChatResponse, MetricCandidate } from '../types';

interface Props {
  response: ChatResponse;
  onCandidateClick: (candidate: MetricCandidate) => void;
}

export default function AnswerSection({ response, onCandidateClick }: Props) {
  if (response.type === 'clarification') {
    return (
      <div>
        <Typography.Text>{response.message}</Typography.Text>
        {response.candidates.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary" style={{ fontSize: 13, display: 'block', marginBottom: 8 }}>
              点击选择一个指标：
            </Typography.Text>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {response.candidates.map((c) => (
                <Tag
                  key={c.key}
                  color="blue"
                  style={{ cursor: 'pointer', padding: '4px 12px', fontSize: 14 }}
                  onClick={() => onCandidateClick(c)}
                >
                  {c.name}
                </Tag>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (response.type === 'error') {
    return <Typography.Text type="danger">{response.message}</Typography.Text>;
  }

  return <Typography.Text>{response.answer}</Typography.Text>;
}
