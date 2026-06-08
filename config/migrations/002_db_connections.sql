-- Phase 3: 用户数据库连接
-- 用户自建数据库连接管理

-- 数据库连接表
CREATE TABLE IF NOT EXISTS db_connections (
    id VARCHAR(64) PRIMARY KEY COMMENT '连接 ID',
    user_id BIGINT NOT NULL COMMENT '所属用户',
    name VARCHAR(128) NOT NULL COMMENT '连接名称',
    db_type ENUM('mysql') NOT NULL DEFAULT 'mysql' COMMENT '数据库类型',
    host VARCHAR(255) NOT NULL COMMENT '主机地址',
    port INT NOT NULL DEFAULT 3306 COMMENT '端口',
    db_name VARCHAR(128) NOT NULL COMMENT '数据库名',
    db_user VARCHAR(128) NOT NULL COMMENT '数据库用户名',
    db_password_encrypted TEXT NOT NULL COMMENT 'AES-256 加密后的密码',
    status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
    last_tested_at DATETIME DEFAULT NULL COMMENT '最近测试时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_status (status)
) ENGINE=InnoDB COMMENT='用户数据库连接';

-- 扩展分析空间表
ALTER TABLE analysis_spaces
  ADD COLUMN user_id BIGINT DEFAULT NULL COMMENT '所属用户 (NULL=系统空间)' AFTER id,
  ADD COLUMN connection_id VARCHAR(64) DEFAULT NULL COMMENT '关联数据库连接' AFTER dataset_id,
  ADD COLUMN db_schema JSON DEFAULT NULL COMMENT '自动发现的表结构' AFTER connection_id,
  ADD INDEX idx_user (user_id),
  ADD INDEX idx_connection (connection_id);
