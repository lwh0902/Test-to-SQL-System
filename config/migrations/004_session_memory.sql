-- 会话隔离长期记忆。消息读取使用 created_at + id 排序，不依赖未声明的 seq 列。
CREATE TABLE IF NOT EXISTS session_memories (
    session_id VARCHAR(64) PRIMARY KEY COMMENT '仅所属会话可读取',
    user_id BIGINT NOT NULL,
    space_id VARCHAR(64) NOT NULL,
    summary TEXT NOT NULL COMMENT '经压缩的会话摘要，不含原始明细',
    source_turns INT NOT NULL DEFAULT 0 COMMENT '已压缩的用户轮次',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_session_owner (session_id, user_id, space_id)
) ENGINE=InnoDB COMMENT='会话隔离长期记忆';
