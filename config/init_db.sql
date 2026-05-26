-- DataCheck 业务数据库建表脚本
-- 数据库：datacheck

CREATE DATABASE IF NOT EXISTS datacheck
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE datacheck;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(64) NOT NULL COMMENT '姓名',
    phone VARCHAR(20) NOT NULL COMMENT '手机号（加密存储）',
    id_card VARCHAR(64) DEFAULT NULL COMMENT '身份证号（加密存储）',
    role ENUM('admin', 'tester', 'product', 'developer') NOT NULL DEFAULT 'tester',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default' COMMENT '工作空间',
    status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_role (role),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB COMMENT='用户表';

-- 扫描记录表
CREATE TABLE IF NOT EXISTS scan_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '关联用户',
    scan_type ENUM('id_card', 'bank_card', 'face', 'ocr') NOT NULL COMMENT '扫描类型',
    status ENUM('success', 'failed') NOT NULL COMMENT '扫描结果',
    error_type VARCHAR(64) DEFAULT NULL COMMENT '错误类型：OCR_TIMEOUT, LOW_QUALITY, FACE_MISMATCH 等',
    device_type ENUM('ios', 'android', 'web') DEFAULT NULL COMMENT '设备类型',
    device_os_version VARCHAR(32) DEFAULT NULL COMMENT '系统版本',
    app_version VARCHAR(32) DEFAULT NULL COMMENT 'App 版本',
    duration_ms INT DEFAULT NULL COMMENT '耗时（毫秒）',
    datasource_id VARCHAR(64) NOT NULL DEFAULT 'default' COMMENT '数据源',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_scan_type (scan_type),
    INDEX idx_status (status),
    INDEX idx_device_type (device_type),
    INDEX idx_created_at (created_at),
    INDEX idx_workspace (workspace_id),
    INDEX idx_error_type (error_type)
) ENGINE=InnoDB COMMENT='扫描记录表';

-- 特征事件表
CREATE TABLE IF NOT EXISTS feature_events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '关联用户',
    event_type VARCHAR(64) NOT NULL COMMENT '事件类型：liveness_detected, id_uploaded, ocr_completed 等',
    feature_name VARCHAR(64) NOT NULL COMMENT '功能名称',
    is_success TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否成功',
    error_code VARCHAR(32) DEFAULT NULL COMMENT '错误码',
    datasource_id VARCHAR(64) NOT NULL DEFAULT 'default',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_event_type (event_type),
    INDEX idx_feature_name (feature_name),
    INDEX idx_created_at (created_at),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='特征事件表';

-- API 调用日志表
CREATE TABLE IF NOT EXISTS api_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL COMMENT '关联用户',
    api_name VARCHAR(128) NOT NULL COMMENT 'API 名称',
    method ENUM('GET', 'POST', 'PUT', 'DELETE') NOT NULL DEFAULT 'POST',
    status_code INT NOT NULL COMMENT 'HTTP 状态码',
    response_time_ms INT DEFAULT NULL COMMENT '响应时间（毫秒）',
    is_error TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否异常',
    error_message TEXT DEFAULT NULL COMMENT '错误信息',
    client_ip VARCHAR(64) DEFAULT NULL COMMENT '客户端 IP',
    datasource_id VARCHAR(64) NOT NULL DEFAULT 'default',
    workspace_id VARCHAR(64) NOT NULL DEFAULT 'default',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_api_name (api_name),
    INDEX idx_status_code (status_code),
    INDEX idx_is_error (is_error),
    INDEX idx_created_at (created_at),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB COMMENT='API 调用日志表';
