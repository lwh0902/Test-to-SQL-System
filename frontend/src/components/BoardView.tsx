import { useEffect, useState } from 'react';
import { Button, Spin, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import ChartView from './ChartView';
import {
  getBoard,
  openTileChat,
  refreshBoardTile,
  type BoardTile,
  type BoardTileData,
} from '../services/api';

const BRAND_PANEL = 'rgba(8, 6, 16, 0.72)';
const BRAND_BORDER = 'rgba(255, 255, 255, 0.08)';
const BRAND_MUTED = '#9B97AD';
const BRAND_TEXT = '#F4F4F8';

interface Props {
  spaceId: string;
  onOpenChat: (sessionId: string, title: string) => void;
}

export default function BoardView({ spaceId, onOpenChat }: Props) {
  const [title, setTitle] = useState('经营看板');
  const [tiles, setTiles] = useState<BoardTile[]>([]);
  const [dataByTile, setDataByTile] = useState<Record<string, BoardTileData>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState<Record<string, boolean>>({});

  const loadAndRefresh = async () => {
    setLoading(true);
    setErrors({});
    try {
      const board = await getBoard(spaceId);
      setTitle(board.title);
      setTiles(board.tiles || []);
      const nextData: Record<string, BoardTileData> = {};
      const nextErrors: Record<string, string> = {};
      for (const tile of board.tiles || []) {
        try {
          nextData[tile.tile_id] = await refreshBoardTile(spaceId, tile.tile_id);
        } catch (err) {
          nextErrors[tile.tile_id] = err instanceof Error ? err.message : '刷新失败';
        }
      }
      setDataByTile(nextData);
      setErrors(nextErrors);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '无法加载看板');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAndRefresh();
  }, [spaceId]);

  const handleRefreshOne = async (tileId: string) => {
    setRefreshing((prev) => ({ ...prev, [tileId]: true }));
    try {
      const data = await refreshBoardTile(spaceId, tileId);
      setDataByTile((prev) => ({ ...prev, [tileId]: data }));
      setErrors((prev) => {
        const next = { ...prev };
        delete next[tileId];
        return next;
      });
    } catch (err) {
      setErrors((prev) => ({
        ...prev,
        [tileId]: err instanceof Error ? err.message : '刷新失败',
      }));
    } finally {
      setRefreshing((prev) => ({ ...prev, [tileId]: false }));
    }
  };

  const handleOpen = async (tile: BoardTile) => {
    try {
      const opened = await openTileChat(spaceId, tile.tile_id);
      onOpenChat(opened.session_id || opened.id, opened.title || tile.title);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '无法进入对话');
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '80px 0', color: BRAND_MUTED }}>
        <Spin /> <span style={{ marginLeft: 8 }}>正在刷新看板</span>
      </div>
    );
  }

  return (
    <div style={{ padding: '8px 0 32px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
        <div>
          <div style={{ fontSize: 24, fontWeight: 600, color: BRAND_TEXT }}>{title}</div>
          <div style={{ fontSize: 12, color: BRAND_MUTED, marginTop: 4 }}>
            磁贴钉的是查询快照。刷新直接查数，不会重新理解自然语言。
          </div>
        </div>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => void loadAndRefresh()}
          style={{ color: BRAND_TEXT, borderColor: BRAND_BORDER, background: 'transparent' }}
        >
          全部刷新
        </Button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14 }}>
        {tiles.map((tile) => {
          const data = dataByTile[tile.tile_id];
          const err = errors[tile.tile_id];
          return (
            <div
              key={tile.tile_id}
              style={{
                background: BRAND_PANEL,
                border: `1px solid ${BRAND_BORDER}`,
                borderRadius: 14,
                padding: 16,
                backdropFilter: 'blur(16px)',
                minHeight: 220,
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                <div>
                  <div style={{ color: BRAND_TEXT, fontWeight: 600, fontSize: 15 }}>{tile.title}</div>
                  <div style={{ color: BRAND_MUTED, fontSize: 11, marginTop: 4 }}>
                    {(tile.metrics || []).join('、') || '查询'}
                    {tile.dimensions?.length ? ` · ${tile.dimensions.join('、')}` : ''}
                  </div>
                </div>
                <Button
                  size="small"
                  type="text"
                  loading={Boolean(refreshing[tile.tile_id])}
                  onClick={() => void handleRefreshOne(tile.tile_id)}
                  style={{ color: BRAND_MUTED }}
                >
                  刷新
                </Button>
              </div>
              <div style={{ flex: 1, minHeight: 120 }}>
                {err && (
                  <div style={{ color: '#ff7875', fontSize: 13, lineHeight: '20px' }}>{err}</div>
                )}
                {!err && data?.chart && data.rows?.length ? (
                  <ChartView chart={data.chart} columns={data.columns} rows={data.rows} />
                ) : null}
                {!err && !data?.chart && data?.rows?.length ? (
                  <div style={{ color: BRAND_TEXT, fontSize: 28, fontWeight: 700, padding: '28px 0 8px' }}>
                    {String(Object.values(data.rows[0] || {})[0] ?? '—')}
                  </div>
                ) : null}
              </div>
              <Button
                size="small"
                onClick={() => void handleOpen(tile)}
                style={{
                  marginTop: 10,
                  color: BRAND_TEXT,
                  borderColor: BRAND_BORDER,
                  background: 'rgba(255,255,255,0.04)',
                }}
              >
                带着这块查询继续问
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
