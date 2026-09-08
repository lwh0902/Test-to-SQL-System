-- Stage 2: query snapshot as main table + children. System DB only.
CREATE TABLE IF NOT EXISTS session_queries (
  query_id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  user_id BIGINT NOT NULL,
  space_id VARCHAR(64) NOT NULL,
  version INT NOT NULL,
  entity VARCHAR(64) NOT NULL DEFAULT '',
  time_start VARCHAR(32) NOT NULL DEFAULT '',
  time_end VARCHAR(32) NOT NULL DEFAULT '',
  time_field VARCHAR(64) NOT NULL DEFAULT '',
  time_grain VARCHAR(16) NULL,
  query_limit INT NULL,
  model_version VARCHAR(80) NOT NULL DEFAULT '',
  catalog_fingerprint VARCHAR(128) NOT NULL DEFAULT '',
  sql_text TEXT NULL,
  chart_type VARCHAR(32) NULL,
  is_current TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_session_query_current (session_id, user_id, space_id, is_current),
  INDEX idx_session_query_version (session_id, user_id, space_id, version)
) ENGINE=InnoDB COMMENT='会话语义查询快照主表';

CREATE TABLE IF NOT EXISTS session_query_metrics (
  query_id VARCHAR(64) NOT NULL,
  position INT NOT NULL,
  metric_id VARCHAR(64) NOT NULL,
  PRIMARY KEY (query_id, position)
) ENGINE=InnoDB COMMENT='查询快照指标子表';

CREATE TABLE IF NOT EXISTS session_query_dimensions (
  query_id VARCHAR(64) NOT NULL,
  position INT NOT NULL,
  dimension_id VARCHAR(64) NOT NULL,
  PRIMARY KEY (query_id, position)
) ENGINE=InnoDB COMMENT='查询快照维度子表';

CREATE TABLE IF NOT EXISTS session_query_filters (
  query_id VARCHAR(64) NOT NULL,
  position INT NOT NULL,
  field_name VARCHAR(64) NOT NULL,
  op VARCHAR(16) NOT NULL DEFAULT '=',
  value_json JSON NULL,
  PRIMARY KEY (query_id, position)
) ENGINE=InnoDB COMMENT='查询快照筛选子表';

CREATE TABLE IF NOT EXISTS semantic_entities (
  space_id VARCHAR(64) NOT NULL,
  version VARCHAR(80) NOT NULL,
  entity_id VARCHAR(64) NOT NULL,
  table_name VARCHAR(128) NOT NULL,
  grain VARCHAR(255) NOT NULL DEFAULT '',
  time_field VARCHAR(64) NOT NULL DEFAULT '',
  description VARCHAR(512) NOT NULL DEFAULT '',
  aliases_json JSON NULL,
  PRIMARY KEY (space_id, version, entity_id)
) ENGINE=InnoDB COMMENT='语义实体行';

CREATE TABLE IF NOT EXISTS semantic_metrics (
  space_id VARCHAR(64) NOT NULL,
  version VARCHAR(80) NOT NULL,
  metric_id VARCHAR(64) NOT NULL,
  entity_id VARCHAR(64) NOT NULL,
  aggregation VARCHAR(32) NOT NULL,
  field_name VARCHAR(64) NOT NULL DEFAULT '',
  description VARCHAR(512) NOT NULL DEFAULT '',
  aliases_json JSON NULL,
  allowed_dimensions_json JSON NULL,
  default_filters_json JSON NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
  PRIMARY KEY (space_id, version, metric_id)
) ENGINE=InnoDB COMMENT='语义指标行';

CREATE TABLE IF NOT EXISTS semantic_dimensions (
  space_id VARCHAR(64) NOT NULL,
  version VARCHAR(80) NOT NULL,
  dimension_id VARCHAR(64) NOT NULL,
  entity_id VARCHAR(64) NOT NULL,
  field_name VARCHAR(64) NOT NULL,
  description VARCHAR(512) NOT NULL DEFAULT '',
  aliases_json JSON NULL,
  PRIMARY KEY (space_id, version, dimension_id)
) ENGINE=InnoDB COMMENT='语义维度行';

ALTER TABLE chat_messages
  ADD COLUMN query_id VARCHAR(64) NULL,
  ADD COLUMN sql_text TEXT NULL,
  ADD COLUMN chart_type VARCHAR(32) NULL;
