-- Platform-managed data sources for public analysis spaces.
-- Only deployment/admin operations may insert or modify these encrypted credentials.

CREATE TABLE IF NOT EXISTS managed_space_connections (
    space_id VARCHAR(64) PRIMARY KEY COMMENT '公开分析空间 ID',
    db_type ENUM('mysql') NOT NULL DEFAULT 'mysql',
    host VARCHAR(255) NOT NULL,
    port INT NOT NULL DEFAULT 3306,
    db_name VARCHAR(128) NOT NULL,
    db_user VARCHAR(128) NOT NULL,
    db_password_encrypted TEXT NOT NULL,
    status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status)
) ENGINE=InnoDB COMMENT='平台托管的公开空间数据源';
