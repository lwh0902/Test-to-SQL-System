-- Phase 2: 手机号注册
-- auth_users 表新增 phone 列，支持手机号登录

ALTER TABLE auth_users
  ADD COLUMN phone VARCHAR(20) DEFAULT NULL COMMENT '手机号' AFTER username,
  ADD UNIQUE INDEX idx_phone (phone);

-- 允许 username 为空（新用户只用手机号）
ALTER TABLE auth_users
  MODIFY COLUMN username VARCHAR(64) DEFAULT NULL COMMENT '登录用户名';
