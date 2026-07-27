CREATE TABLE IF NOT EXISTS analysis_tasks (
  id VARCHAR(64) PRIMARY KEY, session_id VARCHAR(64) NOT NULL, user_id BIGINT NOT NULL, space_id VARCHAR(64) NOT NULL,
  question TEXT NOT NULL, status VARCHAR(32) NOT NULL, current_agent VARCHAR(64), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_task_scope (session_id, user_id, space_id)
) ENGINE=InnoDB COMMENT='多Agent分析任务';

CREATE TABLE IF NOT EXISTS agent_artifacts (
  id VARCHAR(64) PRIMARY KEY, task_id VARCHAR(64) NOT NULL, session_id VARCHAR(64) NOT NULL, user_id BIGINT NOT NULL, space_id VARCHAR(64) NOT NULL,
  artifact_type VARCHAR(64) NOT NULL, source_agent VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, payload JSON NOT NULL,
  schema_version VARCHAR(16) NOT NULL DEFAULT '1.0', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_artifact_scope (task_id, session_id, user_id, space_id)
) ENGINE=InnoDB COMMENT='受证据约束的Agent工件';

CREATE TABLE IF NOT EXISTS agent_working_memories (
  task_id VARCHAR(64) NOT NULL, agent_name VARCHAR(64) NOT NULL, session_id VARCHAR(64) NOT NULL, user_id BIGINT NOT NULL, space_id VARCHAR(64) NOT NULL,
  memory JSON NOT NULL, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (task_id, agent_name), INDEX idx_working_scope (session_id, user_id, space_id)
) ENGINE=InnoDB COMMENT='Agent私有任务记忆';

CREATE TABLE IF NOT EXISTS agent_experience_memories (
  agent_name VARCHAR(64) NOT NULL, space_id VARCHAR(64) NOT NULL, memory JSON NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (agent_name, space_id)
) ENGINE=InnoDB COMMENT='Agent按空间隔离经验记忆';

CREATE TABLE IF NOT EXISTS a2a_messages (
  id VARCHAR(96) PRIMARY KEY, correlation_id VARCHAR(64) NOT NULL, task_id VARCHAR(64) NOT NULL, session_id VARCHAR(64) NOT NULL, user_id BIGINT NOT NULL, space_id VARCHAR(64) NOT NULL,
  source_agent VARCHAR(64) NOT NULL, target_agent VARCHAR(64) NOT NULL, idempotency_key VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL,
  payload JSON NOT NULL, artifact_ids JSON NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_a2a_idempotency (idempotency_key), INDEX idx_a2a_scope (task_id,session_id,user_id,space_id)
) ENGINE=InnoDB COMMENT='A2A持久化消息Outbox';
