import { Collapse, Table, Button } from 'antd';
import { LineChartOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import type { ChatResponse, MetricCandidate, ChartConfig, PlanResult } from '../types';
import type { DataMapQuestion } from '../services/api';
import datapilotAppIcon from '../assets/datapilot-app-icon.png';
import DataMapBlock from './DataMapBlock';

const BRAND_INK = '#071113';
const BRAND_PANEL = '#0B181A';
const BRAND_CARD = '#102326';
const BRAND_LIME = '#9BCB2D';
const BRAND_BORDER = '#183235';
const BRAND_MUTED = '#7D8B8E';
const BRAND_TEXT = '#EEF4F2';

interface Props {
  role: 'user' | 'assistant';
  content: string;
  data?: ChatResponse;
  onCandidateClick?: (candidate: MetricCandidate) => void;
  onDataMapQuestionClick?: (question: DataMapQuestion) => void;
  onOpenTrace?: (data: ChatResponse) => void;
}

function formatValue(val: unknown, format: string | null): string {
  if (format === 'percent') return `${Number(val).toFixed(2)}%`;
  if (format === 'duration_ms') return `${Number(val).toFixed(0)}ms`;
  if (Number(val) >= 10000) return Number(val).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  return `${Number(val).toFixed(2)}`;
}

function ChartBlock({ chart, columns, rows }: { chart: ChartConfig; columns: string[]; rows: Record<string, unknown>[] }) {
  const { type, x_field, y_fields, labels, value_format } = chart;

  const baseGrid = { left: 50, right: 16, bottom: 30, top: 20, containLabel: false };

  if (type === 'line' && x_field && columns.includes(x_field)) {
    const xData = rows.map((r) => String(r[x_field] || ''));
    const series = y_fields
      .filter((f) => columns.includes(f))
      .map((field) => ({
        name: labels?.[field] || field,
        data: rows.map((r) => Number(r[field])),
        type: 'line' as const,
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        lineStyle: { width: 2.5 },
        areaStyle: { opacity: 0.08 },
      }));

    return (
      <ReactECharts
        option={{
          tooltip: {
            trigger: 'axis' as const,
            backgroundColor: BRAND_CARD,
            borderColor: '#294D51',
            textStyle: { fontSize: 12, color: BRAND_TEXT },
            formatter: (params: { seriesName: string; value: number }[]) =>
              params.map((p) => `${p.seriesName}: ${formatValue(p.value, value_format)}`).join('<br/>'),
          },
          xAxis: {
            type: 'category' as const, data: xData, boundaryGap: false,
            axisLine: { lineStyle: { color: '#294D51' } },
            axisLabel: { color: BRAND_MUTED, fontSize: 11 },
          },
          yAxis: {
            type: 'value' as const,
            splitLine: { lineStyle: { color: '#132B2E' } },
            axisLabel: { color: BRAND_MUTED, fontSize: 11 },
          },
          series,
          grid: baseGrid,
          color: [BRAND_LIME, '#5F8D91', '#CDE0DC'],
        }}
        style={{ height: 260 }}
        opts={{ renderer: 'svg' }}
      />
    );
  }

  if (type === 'bar' && x_field && columns.includes(x_field)) {
    const yField = y_fields.find((f) => columns.includes(f));
    if (!yField) return null;
    const data = rows.map((r) => ({ name: String(r[x_field] || ''), value: Number(r[yField]) || 0 }));

    return (
      <ReactECharts
        option={{
          tooltip: { trigger: 'axis' as const, backgroundColor: BRAND_CARD, borderColor: '#294D51', textStyle: { fontSize: 12, color: BRAND_TEXT } },
          xAxis: {
            type: 'category' as const,
            data: data.map((d) => d.name),
            axisLabel: { rotate: data.length > 6 ? 30 : 0, color: BRAND_MUTED, fontSize: 11 },
            axisLine: { lineStyle: { color: '#294D51' } },
          },
          yAxis: {
            type: 'value' as const,
            splitLine: { lineStyle: { color: '#132B2E' } },
            axisLabel: { color: BRAND_MUTED, fontSize: 11 },
          },
          series: [{
            name: labels?.[yField] || yField,
            data: data.map((d) => d.value),
            type: 'bar' as const,
            barWidth: data.length > 10 ? 16 : 24,
            itemStyle: { borderRadius: [4, 4, 0, 0], color: BRAND_LIME },
          }],
          grid: { ...baseGrid, bottom: data.length > 6 ? 50 : 30 },
          color: [BRAND_LIME],
        }}
        style={{ height: 260 }}
        opts={{ renderer: 'svg' }}
      />
    );
  }

  if (type === 'stat' && y_fields.length > 0) {
    const field = y_fields[0];
    const val = rows[0]?.[field];
    const label = labels?.[field] || field;
    return (
      <div style={{ textAlign: 'center', padding: '20px 0' }}>
        <div style={{ fontSize: 36, fontWeight: 700, color: BRAND_TEXT }}>{formatValue(val, value_format)}</div>
        <div style={{ color: BRAND_MUTED, marginTop: 4, fontSize: 13 }}>{label}</div>
      </div>
    );
  }

  return null;
}

export default function ChatMessage({ role, content, data, onCandidateClick, onDataMapQuestionClick, onOpenTrace }: Props) {
  if (role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 20 }}>
        <div style={{ maxWidth: 'min(75%, 520px)' }}>
          <div style={{
            background: BRAND_LIME,
          color: BRAND_INK, padding: '10px 18px', borderRadius: '16px 16px 4px 16px',
          fontSize: 14, lineHeight: '22px', boxShadow: '0 8px 22px rgba(155,203,45,0.16)',
          }}>
            {content}
          </div>
        </div>
      </div>
    );
  }

  const isClarification = data?.type === 'clarification';
  const isError = data?.type === 'error';
  const isDataMap = data?.type === 'data_map';
  const hasData = data && (data.rows?.length > 0 || data.chart || data.sql);

  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
      {/* Avatar */}
      <div style={{
        width: 34, height: 34, borderRadius: 10, flexShrink: 0,
        background: BRAND_CARD,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <img src={datapilotAppIcon} alt="" style={{ width: 34, height: 34, borderRadius: 10, display: 'block' }} />
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Error */}
        {isError && (
          <div style={{
            padding: '12px 16px', borderRadius: 10,
            background: 'rgba(255,77,79,0.1)', border: '1px solid rgba(255,77,79,0.35)',
            fontSize: 13, color: '#cf1322',
          }}>
            {data?.message || content}
          </div>
        )}

        {/* Clarification */}
        {isClarification && (
          <div>
            <div style={{ fontSize: 14, lineHeight: '22px', color: BRAND_TEXT }}>
              {data?.message || content}
            </div>
            {data?.candidates && data.candidates.length > 0 && (
              <div style={{ marginTop: 14 }}>
                <div style={{ fontSize: 12, color: BRAND_MUTED, marginBottom: 8 }}>点击选择指标：</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {data.candidates.map((c) => (
                    <div
                      key={c.key}
                      onClick={() => onCandidateClick?.(c)}
                      style={{
                        padding: '6px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 13,
                        background: BRAND_PANEL, border: `1px solid ${BRAND_BORDER}`, color: BRAND_TEXT,
                        transition: 'all 0.15s',
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.borderColor = BRAND_LIME; e.currentTarget.style.color = BRAND_INK; e.currentTarget.style.background = BRAND_LIME; }}
                      onMouseLeave={(e) => { e.currentTarget.style.borderColor = BRAND_BORDER; e.currentTarget.style.color = BRAND_TEXT; e.currentTarget.style.background = BRAND_PANEL; }}
                    >
                      {c.name}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Normal answer */}
        {!isError && !isClarification && !isDataMap && (
          <div style={{ fontSize: 14, lineHeight: '22px', color: BRAND_TEXT }}>{content}</div>
        )}

        {/* Data Map */}
        {isDataMap && data?.data_map && (
          <div style={{ marginTop: 4 }}>
            <DataMapBlock
              dataMap={data.data_map}
              dbIdentity={data.db_identity}
              compact
              onQuestionClick={onDataMapQuestionClick}
            />
          </div>
        )}

        {/* Plan-and-Execute multi-step results */}
        {data?.plan_results && data.plan_results.length > 1 && (
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 16 }}>
            {data.plan_results.map((pr: PlanResult, idx: number) => (
              <div key={idx} style={{
                background: BRAND_PANEL, borderRadius: 12, padding: 16,
                border: `1px solid ${BRAND_BORDER}`,
              }}>
                <div style={{ fontSize: 12, color: BRAND_MUTED, marginBottom: 8 }}>
                  步骤 {pr.step}：{pr.title || pr.question}
                </div>
                {pr.error ? (
                  <div style={{ fontSize: 13, color: '#cf1322' }}>{pr.error}</div>
                ) : (
                  <>
                    {pr.chart && pr.rows && pr.rows.length > 0 && (
                      <ChartBlock chart={pr.chart} columns={pr.columns || []} rows={pr.rows} />
                    )}
                    {pr.rows && pr.rows.length > 0 && (
                      <div style={{ overflow: 'auto', marginTop: 8 }}>
                        <Table
                          columns={(pr.columns || []).map((col: string) => ({ title: col, dataIndex: col, key: col, ellipsis: true }))}
                          dataSource={pr.rows.map((r, i) => ({ ...r, key: i }))}
                          size="small"
                          scroll={{ x: 'max-content' }}
                          pagination={pr.rows.length > 10 ? { pageSize: 10, size: 'small' } : false}
                        />
                      </div>
                    )}
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Data section */}
        {hasData && (
          <div style={{ marginTop: 14 }}>
            {/* Chart */}
            {data!.chart && data!.rows && data!.rows.length > 0 && (
              <div style={{
                background: BRAND_PANEL, borderRadius: 12, padding: 16, marginBottom: 10,
                border: `1px solid ${BRAND_BORDER}`,
              }}>
                <ChartBlock chart={data!.chart} columns={data!.columns} rows={data!.rows} />
              </div>
            )}

            <Collapse
              size="small"
              style={{ background: 'transparent', border: 'none' }}
              items={[
                ...(data!.rows && data!.rows.length > 0
                  ? [{
                      key: 'data',
                      label: <span style={{ fontSize: 12, color: '#A9B7BA' }}>数据表（{data!.rows.length} 行）</span>,
                      children: (
                        <div style={{ overflow: 'auto' }}>
                          <Table
                            columns={data!.columns.map((col) => ({ title: col, dataIndex: col, key: col, ellipsis: true }))}
                            dataSource={data!.rows.map((r, i) => ({ ...r, key: i }))}
                            size="small"
                            scroll={{ x: 'max-content' }}
                            pagination={data!.rows.length > 20 ? { pageSize: 20, size: 'small' } : false}
                          />
                        </div>
                      ),
                    }]
                  : []),
                ...(data!.sql
                  ? [{
                      key: 'sql',
                      label: <span style={{ fontSize: 12, color: '#A9B7BA' }}>SQL</span>,
                      children: (
                        <pre style={{
                          background: '#050B0C', color: '#CDE0DC',
                          padding: 14, borderRadius: 8, fontSize: 12, overflow: 'auto',
                          lineHeight: '18px', margin: 0,
                        }}>
                          {data!.sql}
                        </pre>
                      ),
                    }]
                  : []),
                ...(data!.trace && data!.trace.length > 0
                  ? [{
                      key: 'trace',
                      label: <span style={{ fontSize: 12, color: '#A9B7BA' }}>Trace（{data!.trace.length} 步）</span>,
                      children: (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          {data!.trace.map((step, i) => (
                            <div key={i} style={{
                              display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                              padding: '4px 8px', borderRadius: 4,
                              background: step.status === 'done' || step.status === 'passed' ? 'rgba(155,203,45,0.12)' : 'rgba(255,77,79,0.1)',
                            }}>
                              <span style={{ color: step.status === 'done' || step.status === 'passed' ? '#52c41a' : '#ff4d4f' }}>
                                {step.status === 'done' || step.status === 'passed' ? '✓' : '✗'}
                              </span>
                              <span style={{ color: '#C4CECC' }}>{step.node}</span>
                            </div>
                          ))}
                          <Button type="link" size="small" icon={<LineChartOutlined />}
                            onClick={() => onOpenTrace?.(data!)}
                            style={{ padding: 0, fontSize: 12, color: BRAND_LIME }}>
                            查看完整 Trace 详情
                          </Button>
                        </div>
                      ),
                    }]
                  : []),
              ]}
            />
          </div>
        )}
      </div>
    </div>
  );
}
