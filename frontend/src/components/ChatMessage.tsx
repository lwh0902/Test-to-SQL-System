import { Collapse, Table, Button } from 'antd';
import { LineChartOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import type { ChatResponse, MetricCandidate, ChartConfig, PlanResult } from '../types';
import type { DataMapQuestion } from '../services/api';

const BRAND_INK = '#FFFFFF';
const BRAND_CARD = 'rgba(255, 255, 255, 0.05)';
const BRAND_CHART = '#A8A4B8';
const BRAND_BORDER = 'rgba(255, 255, 255, 0.1)';
const BRAND_MUTED = '#9B97AD';
const BRAND_TEXT = '#F4F4F8';

/** 数据块级玻璃卡片（结论文字裸排，只有数据/图/SQL/Trace 上卡片） */
const GLASS_CARD: React.CSSProperties = {
  background: 'rgba(10, 8, 16, 0.5)',
  border: `1px solid ${BRAND_BORDER}`,
  borderRadius: 12,
  backdropFilter: 'blur(14px)',
  WebkitBackdropFilter: 'blur(14px)',
};

interface Props {
  role: 'user' | 'assistant';
  content: string;
  data?: ChatResponse;
  onCandidateClick?: (candidate: MetricCandidate) => void;
  onDataMapQuestionClick?: (question: DataMapQuestion) => void;
  onOpenTrace?: (data: ChatResponse) => void;
  /** 基于当前查询结果触发深度诊断（条件编排入口） */
  onGenerateDiagnosis?: (question: string, data: ChatResponse) => void;
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
            borderColor: 'rgba(255, 255, 255, 0.1)',
            textStyle: { fontSize: 12, color: BRAND_TEXT },
            formatter: (params: { seriesName: string; value: number }[]) =>
              params.map((p) => `${p.seriesName}: ${formatValue(p.value, value_format)}`).join('<br/>'),
          },
          xAxis: {
            type: 'category' as const, data: xData, boundaryGap: false,
            axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
            axisLabel: { color: BRAND_MUTED, fontSize: 11 },
          },
          yAxis: {
            type: 'value' as const,
            splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.06)' } },
            axisLabel: { color: BRAND_MUTED, fontSize: 11 },
          },
          series,
          grid: baseGrid,
          color: [BRAND_CHART, '#9B97AD', '#C4C0D4'],
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
          tooltip: { trigger: 'axis' as const, backgroundColor: BRAND_CARD, borderColor: 'rgba(255, 255, 255, 0.1)', textStyle: { fontSize: 12, color: BRAND_TEXT } },
          xAxis: {
            type: 'category' as const,
            data: data.map((d) => d.name),
            axisLabel: { rotate: data.length > 6 ? 30 : 0, color: BRAND_MUTED, fontSize: 11 },
            axisLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.1)' } },
          },
          yAxis: {
            type: 'value' as const,
            splitLine: { lineStyle: { color: 'rgba(255, 255, 255, 0.06)' } },
            axisLabel: { color: BRAND_MUTED, fontSize: 11 },
          },
          series: [{
            name: labels?.[yField] || yField,
            data: data.map((d) => d.value),
            type: 'bar' as const,
            barWidth: data.length > 10 ? 16 : 24,
            itemStyle: { borderRadius: [4, 4, 0, 0], color: BRAND_CHART },
          }],
          grid: { ...baseGrid, bottom: data.length > 6 ? 50 : 30 },
          color: [BRAND_CHART],
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

export default function ChatMessage({ role, content, data, onCandidateClick, onOpenTrace, onGenerateDiagnosis }: Props) {
  if (role === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 20 }}>
        <div style={{ maxWidth: 'min(75%, 520px)' }}>
          <div style={{
            background: 'rgba(255, 255, 255, 0.1)',
            border: '1px solid rgba(255, 255, 255, 0.14)',
            color: BRAND_INK, padding: '10px 18px', borderRadius: '16px 16px 4px 16px',
            fontSize: 14, lineHeight: '22px',
            backdropFilter: 'blur(14px)', WebkitBackdropFilter: 'blur(14px)',
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
  const isDiagnosis = data?.type === 'deep_diagnosis';
  const hasData = data && (data.rows?.length > 0 || data.chart || data.sql);
  const stopReason = data?.stop_reason || '';
  const term = data?.terminal_status || '';
  const hints = data?.ux_hints || [];
  const nextActions = data?.next_actions || [];
  const slots = data?.clarify_slots || [];
  const agentsCalled = Array.isArray(data?.evidence?.agents_called)
    ? (data?.evidence?.agents_called as string[])
    : [];

  const statusTone = (() => {
    if (isError || stopReason === 'catalog_not_ready' || stopReason === 'catalog_blocked') {
      return { bg: 'rgba(255,77,79,0.10)', bd: 'rgba(255,77,79,0.35)', fg: '#ffccc7', title: '需要先处理' };
    }
    if (isClarification || stopReason === 'admission_denied' || stopReason === 'clarify') {
      return { bg: 'rgba(250,173,20,0.10)', bd: 'rgba(250,173,20,0.35)', fg: '#ffe7ba', title: '还差一点信息' };
    }
    if (term === 'SQL_REJECTED' || term === 'PERMISSION_DENIED') {
      return { bg: 'rgba(255,77,79,0.10)', bd: 'rgba(255,77,79,0.35)', fg: '#ffccc7', title: '请求被安全策略拦截' };
    }
    return null;
  })();

  const chipClick = (id: string, label: string) => {
    // Settings / profile CTAs are guidance only (no bogus chat turn).
    if (id === 'open_space_settings' || id === 'run_catalog_profile') {
      return;
    }
    onCandidateClick?.({
      key: id.startsWith('ex_') || id.startsWith('hint_') ? id : `hint_${id}`,
      name: label,
      description: label,
      default_query_type: 'metric',
      default_time_range: '',
    });
  };

  return (
    <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
      {/* Avatar */}
      <div style={{
        width: 34, height: 34, borderRadius: 10, flexShrink: 0,
        background: 'rgba(255,255,255,0.06)',
        border: `1px solid ${BRAND_BORDER}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: BRAND_MUTED, fontSize: 12, fontWeight: 700,
      }}>
        DP
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Structured failure / guidance card */}
        {(isError || isClarification || stopReason === 'admission_denied') && statusTone && (
          <div style={{
            padding: '14px 16px', borderRadius: 12,
            background: statusTone.bg, border: `1px solid ${statusTone.bd}`,
            marginBottom: hasData ? 12 : 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: statusTone.fg, letterSpacing: 0.2 }}>
                {statusTone.title}
              </span>
              {(term || stopReason) && (
                <span style={{
                  fontSize: 11, color: BRAND_MUTED,
                  padding: '1px 8px', borderRadius: 999,
                  border: `1px solid ${BRAND_BORDER}`,
                }}>
                  {[term, stopReason].filter(Boolean).join(' · ')}
                </span>
              )}
            </div>
            <div style={{ fontSize: 14, lineHeight: '22px', color: BRAND_TEXT, whiteSpace: 'pre-line' }}>
              {data?.message || content}
            </div>

            {slots.length > 0 && (
              <div style={{ marginTop: 10, fontSize: 12, color: BRAND_MUTED }}>
                待补充：{slots.map((s) => (
                  <span key={s} style={{
                    display: 'inline-block', marginRight: 6, marginTop: 4,
                    padding: '2px 8px', borderRadius: 6,
                    border: `1px solid ${BRAND_BORDER}`, color: BRAND_TEXT,
                  }}>{s}</span>
                ))}
              </div>
            )}

            {hints.length > 0 && (
              <ul style={{ margin: '10px 0 0', paddingLeft: 18, color: BRAND_MUTED, fontSize: 12, lineHeight: '20px' }}>
                {hints.slice(0, 4).map((h) => <li key={h}>{h}</li>)}
              </ul>
            )}

            {/* next action chips */}
            {(nextActions.length > 0 || (data?.candidates && data.candidates.length > 0)) && (
              <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {nextActions.map((a) => {
                  const isNav = a.id === 'open_space_settings' || a.id === 'run_catalog_profile';
                  return (
                  <button
                    key={a.id}
                    type="button"
                    title={isNav ? '请在左侧空间/数据源面板完成配置与建档' : a.label}
                    onClick={() => chipClick(a.id, a.label)}
                    style={{
                      padding: '6px 12px', borderRadius: 8, cursor: isNav ? 'help' : 'pointer', fontSize: 12,
                      background: 'rgba(255,255,255,0.06)', border: `1px solid ${BRAND_BORDER}`,
                      color: BRAND_TEXT, opacity: isNav ? 0.75 : 1,
                    }}
                  >
                    {a.label}
                  </button>
                  );
                })}
                {data?.candidates?.map((c) => (
                  <button
                    key={c.key}
                    type="button"
                    onClick={() => onCandidateClick?.(c)}
                    style={{
                      padding: '6px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 12,
                      background: 'rgba(255,255,255,0.06)', border: `1px solid ${BRAND_BORDER}`,
                      color: BRAND_TEXT,
                    }}
                  >
                    {c.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Diagnosis meta strip */}
        {isDiagnosis && !isError && stopReason !== 'admission_denied' && (
          <div style={{
            marginBottom: 10, padding: '8px 12px', borderRadius: 10,
            background: 'rgba(255,255,255,0.04)', border: `1px solid ${BRAND_BORDER}`,
            fontSize: 12, color: BRAND_MUTED,
          }}>
            深度诊断
            {term ? ` · ${term}` : ''}
            {stopReason ? ` · ${stopReason}` : ''}
            {agentsCalled.length > 0 ? ` · agents: ${agentsCalled.join(' → ')}` : ''}
            {data?.task_id ? ` · task ${String(data.task_id).slice(0, 14)}` : ''}
          </div>
        )}

        {/* Normal answer（含 data_map 导语） */}
        {!isError && !isClarification && (
          <div style={{ fontSize: 14, lineHeight: '22px', color: BRAND_TEXT, whiteSpace: 'pre-line' }}>{content}</div>
        )}

        {/* Data Map：表职责卡片（OP 可读） */}
        {isDataMap && data?.data_map?.tables && data.data_map.tables.length > 0 && (
          <div style={{
            marginTop: 12, display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: 10,
          }}>
            {data.data_map.tables.slice(0, 12).map((t) => (
              <div
                key={t.name}
                style={{
                  ...GLASS_CARD,
                  padding: '12px 14px',
                  borderRadius: 12,
                  border: `1px solid ${BRAND_BORDER}`,
                }}
              >
                <div style={{ fontSize: 14, fontWeight: 700, color: BRAND_TEXT, marginBottom: 4 }}>
                  {t.title || t.name}
                </div>
                <div style={{ fontSize: 11, color: BRAND_MUTED, marginBottom: 6, fontFamily: 'ui-monospace, monospace' }}>
                  {t.name}
                </div>
                <div style={{ fontSize: 12, lineHeight: '18px', color: 'rgba(244,244,248,0.85)' }}>
                  {t.description || '业务数据表'}
                </div>
              </div>
            ))}
          </div>
        )}

        {isDataMap && data?.db_identity?.connection && (
          <div style={{
            marginTop: 10, display: 'inline-flex', alignItems: 'center', gap: 8,
            padding: '6px 12px', borderRadius: 8,
            background: 'rgba(255, 255, 255, 0.04)', border: `1px solid ${BRAND_BORDER}`,
            fontSize: 12, color: BRAND_MUTED,
          }}>
            <span>{data.db_identity.connection.db_type?.toUpperCase?.() || data.db_identity.connection.db_type}</span>
            <span style={{ color: BRAND_TEXT }}>{data.db_identity.connection.db_name}</span>
            <span>{data.db_identity.connection.host_masked}:{data.db_identity.connection.port}</span>
          </div>
        )}

        {/* Plan-and-Execute multi-step results */}
        {data?.plan_results && data.plan_results.length > 1 && (
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 16 }}>
            {data.plan_results.map((pr: PlanResult, idx: number) => (
              <div key={idx} style={{
                ...GLASS_CARD, padding: 16,
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
                ...GLASS_CARD, padding: 16, marginBottom: 10,
              }}>
                <ChartBlock chart={data!.chart} columns={data!.columns} rows={data!.rows} />
              </div>
            )}

            {/* 基于当前结果生成深度诊断报告（条件编排入口） */}
            {data!.rows && data!.rows.length > 0 && onGenerateDiagnosis && (
              <div style={{ marginBottom: 10 }}>
                <Button
                  size="small"
                  type="default"
                  onClick={() => {
                    const q =
                      (data!.intent && `${data!.intent.metric || ''} ${data!.intent.query_type || ''}`.trim()) ||
                      content ||
                      '基于当前查询结果生成深度诊断报告';
                    onGenerateDiagnosis(q, data!);
                  }}
                  style={{
                    borderColor: BRAND_BORDER,
                    color: BRAND_TEXT,
                    background: 'rgba(255, 255, 255, 0.04)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                >
                  基于当前结果生成深度诊断报告
                </Button>
              </div>
            )}

            <div style={{ ...GLASS_CARD, padding: '2px 12px' }}>
            <Collapse
              size="small"
              style={{ background: 'transparent', border: 'none' }}
              items={[
                ...(data!.rows && data!.rows.length > 0
                  ? [{
                      key: 'data',
                      label: <span style={{ fontSize: 12, color: '#9B97AD' }}>数据表（{data!.rows.length} 行）</span>,
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
                      label: <span style={{ fontSize: 12, color: '#9B97AD' }}>SQL</span>,
                      children: (
                        <pre style={{
                          background: '#06030F', color: '#C4C0D4',
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
                      label: <span style={{ fontSize: 12, color: '#9B97AD' }}>Trace（{data!.trace.length} 步）</span>,
                      children: (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          {data!.trace.map((step, i) => (
                            <div key={i} style={{
                              display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                              padding: '4px 8px', borderRadius: 4,
                              background: step.status === 'done' || step.status === 'passed' ? 'rgba(255, 255, 255, 0.08)' : 'rgba(255,77,79,0.1)',
                            }}>
                              <span style={{ color: step.status === 'done' || step.status === 'passed' ? '#52c41a' : '#ff4d4f' }}>
                                {step.status === 'done' || step.status === 'passed' ? '✓' : '✗'}
                              </span>
                              <span style={{ color: '#B5B1C6' }}>{step.node}</span>
                            </div>
                          ))}
                          <Button type="link" size="small" icon={<LineChartOutlined />}
                            onClick={() => onOpenTrace?.(data!)}
                            style={{ padding: 0, fontSize: 12, color: BRAND_MUTED }}>
                            查看完整 Trace 详情
                          </Button>
                        </div>
                      ),
                    }]
                  : []),
              ]}
            />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
