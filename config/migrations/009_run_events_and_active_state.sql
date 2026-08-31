-- V2 execution facts. The event table is append-only and carries only UI-safe payloads.
CREATE TABLE IF NOT EXISTS analysis_runs (
  run_id VARCHAR(64) PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  user_id BIGINT NOT NULL,
  space_id VARCHAR(64) NOT NULL,
  question TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  terminal_status VARCHAR(64) NULL,
  next_seq INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  INDEX idx_analysis_runs_scope (session_id, user_id, space_id, created_at),
  INDEX idx_analysis_runs_user (user_id, created_at)
) ENGINE=InnoDB COMMENT='V2 analysis run facts';

CREATE TABLE IF NOT EXISTS run_events (
  run_id VARCHAR(64) NOT NULL,
  seq INT NOT NULL,
  kind VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  agent VARCHAR(64) NULL,
  step VARCHAR(64) NULL,
  artifact_id VARCHAR(64) NULL,
  public_payload JSON NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, seq),
  INDEX idx_run_events_created (created_at),
  CONSTRAINT fk_run_events_run FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Committed V2 lifecycle facts';

CREATE TABLE IF NOT EXISTS active_analysis_states (
  session_id VARCHAR(64) NOT NULL,
  user_id BIGINT NOT NULL,
  space_id VARCHAR(64) NOT NULL,
  version INT NOT NULL,
  catalog_fingerprint VARCHAR(128) NOT NULL DEFAULT '',
  catalog_version VARCHAR(64) NOT NULL DEFAULT '',
  valid_until DOUBLE NOT NULL DEFAULT 0,
  state_payload JSON NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (session_id, user_id, space_id),
  INDEX idx_active_analysis_valid (valid_until)
) ENGINE=InnoDB COMMENT='Session scoped active analysis state';
