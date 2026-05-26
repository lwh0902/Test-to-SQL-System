import ReactECharts from 'echarts-for-react';

interface ChartConfig {
  type: string;
  x_field: string;
  y_fields: string[];
  labels: Record<string, string>;
  value_format: string | null;
}

interface Props {
  chart: ChartConfig | null;
  columns: string[];
  rows: Record<string, unknown>[];
}

function formatValue(val: unknown, format: string | null): string {
  if (format === 'percent') return `${Number(val).toFixed(2)}%`;
  if (format === 'duration_ms') return `${Number(val).toFixed(0)}ms`;
  return `${Number(val).toFixed(0)}`;
}

export default function ChartView({ chart, columns, rows }: Props) {
  if (!chart || !rows.length) return null;

  const { type, x_field, y_fields, labels, value_format } = chart;

  if (type === 'line' && x_field && columns.includes(x_field)) {
    const xData = rows.map((r) => String(r[x_field] || ''));
    const series = y_fields
      .filter((f) => columns.includes(f))
      .map((field) => ({
        name: labels?.[field] || field,
        data: rows.map((r) => Number(r[field])),
        type: 'line' as const,
        smooth: true,
        areaStyle: { opacity: 0.1 },
      }));

    const option = {
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: { name: string; seriesName: string; value: number }[]) => {
          return params
            .map((p) => `${p.seriesName}: ${formatValue(p.value, value_format)}`)
            .join('<br/>');
        },
      },
      legend: series.length > 1 ? {} : undefined,
      xAxis: { type: 'category' as const, data: xData, boundaryGap: false },
      yAxis: { type: 'value' as const },
      series,
      grid: { left: 60, right: 20, bottom: 30, top: series.length > 1 ? 40 : 20 },
    };

    return (
      <div style={{ marginTop: 16 }}>
        <ReactECharts option={option} style={{ height: 320 }} />
      </div>
    );
  }

  if (type === 'bar' && x_field && columns.includes(x_field)) {
    const yField = y_fields.find((f) => columns.includes(f));
    if (!yField) return null;

    const data = rows.map((r) => ({
      name: String(r[x_field] || ''),
      value: Number(r[yField]) || 0,
    }));

    const option = {
      tooltip: { trigger: 'axis' as const },
      xAxis: {
        type: 'category' as const,
        data: data.map((d) => d.name),
        axisLabel: { rotate: data.length > 6 ? 30 : 0 },
      },
      yAxis: { type: 'value' as const },
      series: [
        {
          name: labels?.[yField] || yField,
          data: data.map((d) => d.value),
          type: 'bar' as const,
          itemStyle: { borderRadius: [4, 4, 0, 0] },
        },
      ],
      grid: { left: 60, right: 20, bottom: data.length > 6 ? 60 : 30, top: 20 },
    };

    return (
      <div style={{ marginTop: 16 }}>
        <ReactECharts option={option} style={{ height: 320 }} />
      </div>
    );
  }

  return null;
}
