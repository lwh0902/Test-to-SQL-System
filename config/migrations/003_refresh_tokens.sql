-- Refresh tokens 表
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    token_hash VARCHAR(64) NOT NULL COMMENT 'SHA-256 of refresh token',
    expires_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_token_hash (token_hash),
    INDEX idx_expires (expires_at)
) ENGINE=InnoDB COMMENT='Refresh tokens';
