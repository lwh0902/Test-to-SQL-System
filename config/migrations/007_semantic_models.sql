-- DataPilot system DB only. Never runs against a customer's business database.
CREATE TABLE IF NOT EXISTS semantic_model_versions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    space_id VARCHAR(64) NOT NULL,
    version VARCHAR(80) NOT NULL,
    schema_fingerprint VARCHAR(128) NOT NULL,
    model_json JSON NOT NULL,
    summary_md TEXT NOT NULL,
    provenance VARCHAR(64) NOT NULL,
    status ENUM('draft','published','archived') NOT NULL DEFAULT 'draft',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_space_semantic_version (space_id, version),
    INDEX idx_space_semantic_status (space_id, status)
) ENGINE=InnoDB COMMENT='分析空间的版本化业务语义模型';

CREATE TABLE IF NOT EXISTS space_semantic_models (
    space_id VARCHAR(64) PRIMARY KEY,
    published_version_id BIGINT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='每个分析空间当前发布的语义模型指针';

CREATE TABLE IF NOT EXISTS semantic_entity_values (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    space_id VARCHAR(64) NOT NULL,
    dimension_id VARCHAR(128) NOT NULL,
    canonical_value VARCHAR(255) NOT NULL,
    aliases JSON DEFAULT NULL,
    source_table VARCHAR(128) NOT NULL,
    last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_space_dimension_value (space_id, dimension_id, canonical_value),
    INDEX idx_space_dimension (space_id, dimension_id)
) ENGINE=InnoDB COMMENT='自动同步的业务实体名称字典，不存订单明细';
