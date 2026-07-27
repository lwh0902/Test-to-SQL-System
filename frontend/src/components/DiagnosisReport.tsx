import { Card, Tag, Alert, Button, Space, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';

export interface ReportSection {
  title: string;
  content: string;
  evidence_ids: string[];
}

export interface ExportLinkItem {
  artifactId: string;
  format: string;
  fileName?: string;
}

export default function DiagnosisReport({
  sections,
  approved,
  reasons = [],
  exports = [],
  onDownload,
}: {
  sections: ReportSection[];
  approved: boolean;
  reasons?: string[];
  exports?: ExportLinkItem[];
  onDownload?: (item: ExportLinkItem) => Promise<void> | void;
}) {
  // 审核拒绝且无章节时，仍展示拒绝原因
  if (!sections.length && !reasons.length) return null;

  const handleDownload = async (item: ExportLinkItem) => {
    if (!onDownload) return;
    try {
      await onDownload(item);
    } catch (e) {
      message.error(e instanceof Error ? e.message : '下载失败');
    }
  };

  return (
    <Card
      title={
        <>
          深度诊断报告{' '}
          <Tag color={approved ? 'green' : 'orange'}>
            {approved ? '审核通过' : '审核未通过 / 待补充证据'}
          </Tag>
        </>
      }
      style={{ marginTop: 12 }}
      extra={
        approved && exports.length > 0 ? (
          <Space size={4} wrap>
            {exports.map((item) => (
              <Button
                key={item.artifactId + item.format}
                type="link"
                size="small"
                icon={<DownloadOutlined />}
                onClick={() => handleDownload(item)}
              >
                {item.format.toUpperCase()}
              </Button>
            ))}
          </Space>
        ) : null
      }
    >
      {!approved && reasons.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="审查未通过"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          }
        />
      )}

      {sections.map((section) => (
        <section key={section.title} style={{ marginBottom: 16 }}>
          <strong>{section.title}</strong>
          <div style={{ whiteSpace: 'pre-wrap', marginTop: 6 }}>{section.content}</div>
          <small style={{ opacity: 0.55 }}>
            证据：{(section.evidence_ids || []).join('、') || '（无）'}
          </small>
        </section>
      ))}

      {approved && exports.length === 0 && (
        <Space style={{ marginTop: 8 }}>
          <Tag>导出将在审批后生成（Export Agent）</Tag>
        </Space>
      )}
    </Card>
  );
}
