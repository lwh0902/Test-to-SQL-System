import { Typography } from 'antd';

interface Props {
  sql: string | null;
}

export default function SqlDisplay({ sql }: Props) {
  if (!sql) return null;

  return (
    <div style={{ marginTop: 16 }}>
      <Typography.Text type="secondary" style={{ marginBottom: 8, display: 'block' }}>
        生成的 SQL
      </Typography.Text>
      <pre
        style={{
          background: '#06030F',
          color: '#DCD8E8',
          padding: 12,
          borderRadius: 6,
          fontSize: 13,
          overflow: 'auto',
          maxHeight: 200,
          border: '1px solid rgba(255, 255, 255, 0.1)',
        }}
      >
        {sql}
      </pre>
    </div>
  );
}
