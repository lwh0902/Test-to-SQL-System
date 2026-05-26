import { Table, Typography } from 'antd';

interface Props {
  columns: string[];
  rows: Record<string, unknown>[];
}

export default function ResultTable({ columns, rows }: Props) {
  if (!rows.length) return null;

  const tableColumns = columns.map((col) => ({
    title: col,
    dataIndex: col,
    key: col,
    ellipsis: true,
  }));

  return (
    <div style={{ marginTop: 16 }}>
      <Typography.Text type="secondary" style={{ marginBottom: 8, display: 'block' }}>
        查询结果（{rows.length} 行）
      </Typography.Text>
      <Table
        columns={tableColumns}
        dataSource={rows.map((r, i) => ({ ...r, key: i }))}
        size="small"
        scroll={{ x: true }}
        pagination={rows.length > 20 ? { pageSize: 20 } : false}
      />
    </div>
  );
}
