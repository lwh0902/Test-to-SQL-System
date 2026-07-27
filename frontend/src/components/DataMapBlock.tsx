import { Tag } from 'antd';
import { DatabaseOutlined } from '@ant-design/icons';
import type { DataMap, DataMapQuestion } from '../services/api';
import type { DbIdentity } from '../types';

const BRAND_PANEL = 'rgba(255, 255, 255, 0.03)';
const BRAND_CARD = 'rgba(255, 255, 255, 0.05)';
const BRAND_BORDER = 'rgba(255, 255, 255, 0.1)';
const BRAND_MUTED = '#9B97AD';
const BRAND_TEXT = '#F4F4F8';

interface Props {
  dataMap: DataMap;
  dbIdentity?: DbIdentity;
  compact?: boolean;
  onQuestionClick?: (question: DataMapQuestion) => void;
}

function getColumnLabel(table: DataMap['tables'][number], columnName: string): string {
  const column = table.columns.find((item) => item.name === columnName);
  return column?.comment || columnName;
}

export default function DataMapBlock({ dataMap, dbIdentity, compact = false, onQuestionClick }: Props) {
  const questionLimit = compact ? 3 : 6;
  const tableLimit = compact ? 4 : 8;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: compact ? 12 : 16 }}>
      {dbIdentity?.connection && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px',
          background: BRAND_PANEL, borderRadius: 8, border: `1px solid ${BRAND_BORDER}`,
          fontSize: 12, color: BRAND_MUTED, flexWrap: 'wrap',
        }}>
          <DatabaseOutlined style={{ color: BRAND_MUTED }} />
          <span>{dbIdentity.connection.db_type.toUpperCase()}</span>
          <span style={{ color: BRAND_TEXT }}>{dbIdentity.connection.db_name}</span>
          <span>{dbIdentity.connection.host_masked}:{dbIdentity.connection.port}</span>
          {dbIdentity.is_preset && (
            <Tag color="blue" style={{ marginLeft: 4, fontSize: 10, lineHeight: '16px' }}>预设</Tag>
          )}
        </div>
      )}

      {dataMap.recommended_questions.length > 0 && (
        <div className="starter-question-grid" style={{
          display: 'grid',
          gridTemplateColumns: compact ? 'repeat(2, minmax(0, 1fr))' : 'repeat(2, minmax(0, 1fr))',
          gap: compact ? 8 : 12,
        }}>
          {dataMap.recommended_questions.slice(0, questionLimit).map((q, idx) => (
            <button
              type="button"
              key={`${q.source}-${q.metric || q.table || idx}-${q.text}`}
              onClick={() => onQuestionClick?.(q)}
              disabled={!onQuestionClick}
              style={{
                textAlign: 'left',
                padding: compact ? '10px 12px' : '16px 18px',
                minHeight: compact ? 42 : 74,
                borderRadius: compact ? 10 : 14,
                cursor: onQuestionClick ? 'pointer' : 'default',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                color: BRAND_TEXT,
                background: idx === 0 ? 'rgba(255, 255, 255, 0.08)' : BRAND_CARD,
                transition: 'all 0.2s',
                fontSize: compact ? 12 : 15,
                fontWeight: 700,
                lineHeight: compact ? '18px' : '22px',
              }}
            >
              {q.text}
            </button>
          ))}
        </div>
      )}

      {dataMap.tables.length > 0 && (
        <div className="data-map-tables" style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: compact ? 10 : 12 }}>
          {dataMap.tables.slice(0, tableLimit).map((table) => (
            <div key={table.name} style={{
              background: BRAND_CARD, border: '1px solid rgba(255, 255, 255, 0.1)', borderRadius: compact ? 10 : 12,
              padding: compact ? 12 : 14, minHeight: compact ? undefined : 124,
            }}>
              <div style={{ color: BRAND_TEXT, fontSize: compact ? 14 : 16, fontWeight: 800, marginBottom: 8 }}>{table.title}</div>
              <div style={{ color: '#9B97AD', fontSize: compact ? 11 : 12, lineHeight: compact ? '16px' : '18px', marginBottom: 10 }}>
                {table.description}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {table.key_columns.slice(0, compact ? 3 : 5).map((col) => (
                  <span key={col} title={col} style={{
                    color: '#DCD8E8', background: 'rgba(255, 255, 255, 0.08)', border: '1px solid rgba(255, 255, 255, 0.1)', borderRadius: 999,
                    padding: compact ? '2px 7px' : '3px 8px', fontSize: compact ? 10 : 11,
                  }}>
                    {getColumnLabel(table, col)}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
