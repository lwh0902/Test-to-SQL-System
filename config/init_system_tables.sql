-- DataPilot 系统表
-- 用户认证、分析空间、会话、消息、Trace、指标管理

USE datacheck;

-- ============================================================
-- 1. 用户认证表（替代原有 users 表，增加登录字段）
-- ============================================================
CREATE TABLE IF NOT EXISTS auth_users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE COMMENT '登录用户名',
    password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希 (bcrypt)',
    display_name VARCHAR(64) NOT NULL COMMENT '显示名称',
    role ENUM('admin', 'tester', 'product', 'developer', 'operator') NOT NULL DEFAULT 'tester' COMMENT '角色',
    status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_role (role),
    INDEX idx_status (status)
) ENGINE=InnoDB COMMENT='用户认证表';

-- ============================================================
-- 2. 分析空间
-- ============================================================
CREATE TABLE IF NOT EXISTS analysis_spaces (
    id VARCHAR(64) PRIMARY KEY COMMENT '空间标识: tech_quality, ecommerce',
    name VARCHAR(128) NOT NULL COMMENT '空间名称',
    description VARCHAR(512) DEFAULT '' COMMENT '空间描述',
    icon VARCHAR(64) DEFAULT NULL COMMENT '图标标识',
    dataset_id VARCHAR(64) NOT NULL COMMENT '关联数据集',
    status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
    sort_order INT NOT NULL DEFAULT 0 COMMENT '排序权重',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status)
) ENGINE=InnoDB COMMENT='分析空间';

-- ============================================================
-- 3. 指标模板
-- ============================================================
CREATE TABLE IF NOT EXISTS metric_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    space_id VARCHAR(64) NOT NULL COMMENT '所属空间',
    metric_key VARCHAR(128) NOT NULL COMMENT '指标 key',
    name VARCHAR(128) NOT NULL COMMENT '指标名称',
    description VARCHAR(512) DEFAULT '' COMMENT '指标描述',
    config JSON NOT NULL COMMENT '完整指标配置 (allowed_dimensions, sql_templates, chart, etc)',
    sort_order INT NOT NULL DEFAULT 0,
    status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_space_metric (space_id, metric_key),
    INDEX idx_space (space_id),
    INDEX idx_status (status)
) ENGINE=InnoDB COMMENT='指标模板';

-- ============================================================
-- 4. 会话
-- ============================================================
CREATE TABLE IF NOT EXISTS chat_sessions (
    id VARCHAR(64) PRIMARY KEY COMMENT '会话 ID',
    user_id BIGINT NOT NULL COMMENT '用户 ID',
    space_id VARCHAR(64) NOT NULL COMMENT '分析空间',
    title VARCHAR(255) NOT NULL DEFAULT '新对话' COMMENT '会话标题（取首条消息）',
    working_memory JSON DEFAULT NULL COMMENT '工作记忆（上次查询的指标/时间范围等）',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_space (space_id),
    INDEX idx_updated (updated_at DESC)
) ENGINE=InnoDB COMMENT='会话';

-- ============================================================
-- 5. 消息
-- ============================================================
CREATE TABLE IF NOT EXISTS chat_messages (
    id VARCHAR(64) PRIMARY KEY COMMENT '消息 ID',
    session_id VARCHAR(64) NOT NULL COMMENT '会话 ID',
    role ENUM('user', 'assistant') NOT NULL COMMENT '角色',
    content TEXT NOT NULL COMMENT '消息内容（用户问题或 AI 回答摘要）',
    meta JSON DEFAULT NULL COMMENT '结构化数据 (intent, sql, chart, candidates 等)',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB COMMENT='消息';

-- ============================================================
-- 6. Trace 记录
-- ============================================================
CREATE TABLE IF NOT EXISTS traces (
    trace_id VARCHAR(64) PRIMARY KEY COMMENT 'Trace ID',
    session_id VARCHAR(64) DEFAULT NULL COMMENT '关联会话',
    user_id BIGINT DEFAULT NULL COMMENT '用户 ID',
    space_id VARCHAR(64) DEFAULT NULL COMMENT '分析空间',
    question VARCHAR(1024) DEFAULT NULL COMMENT '原始问题',
    intent JSON DEFAULT NULL COMMENT '解析后的 QueryIntent',
    sql_text TEXT DEFAULT NULL COMMENT '最终 SQL',
    rows_count INT DEFAULT 0 COMMENT '结果行数',
    elapsed_ms INT DEFAULT 0 COMMENT '总耗时',
    status ENUM('success', 'error', 'clarification') NOT NULL DEFAULT 'success',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_user (user_id),
    INDEX idx_created (created_at DESC)
) ENGINE=InnoDB COMMENT='Trace 记录';

-- ============================================================
-- 7. Trace 步骤
-- ============================================================
CREATE TABLE IF NOT EXISTS trace_steps (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL COMMENT 'Trace ID',
    node VARCHAR(64) NOT NULL COMMENT '节点名',
    status VARCHAR(32) NOT NULL DEFAULT 'done' COMMENT '步骤状态',
    input_data JSON DEFAULT NULL COMMENT '输入',
    output_data JSON DEFAULT NULL COMMENT '输出',
    elapsed_ms INT DEFAULT 0 COMMENT '耗时',
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trace (trace_id)
) ENGINE=InnoDB COMMENT='Trace 步骤';
