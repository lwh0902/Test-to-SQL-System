-- Stage 3: boards pin semantic query_id, not SQL text.
CREATE TABLE IF NOT EXISTS boards (
  board_id VARCHAR(64) PRIMARY KEY,
  space_id VARCHAR(64) NOT NULL,
  user_id BIGINT NOT NULL,
  title VARCHAR(128) NOT NULL DEFAULT '经营看板',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_board_owner (space_id, user_id)
) ENGINE=InnoDB COMMENT='分析空间看板';

CREATE TABLE IF NOT EXISTS board_tiles (
  tile_id VARCHAR(64) PRIMARY KEY,
  board_id VARCHAR(64) NOT NULL,
  query_id VARCHAR(64) NOT NULL,
  title VARCHAR(128) NOT NULL,
  seed_key VARCHAR(64) NOT NULL DEFAULT '',
  position INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_board_tiles (board_id, position),
  INDEX idx_tile_query (query_id),
  UNIQUE KEY uk_board_tile_seed (board_id, seed_key)
) ENGINE=InnoDB COMMENT='看板磁贴，指向 session_queries.query_id';
