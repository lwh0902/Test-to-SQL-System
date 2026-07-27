-- A2A outbox lease columns for multi-worker safe claim/retry
-- Idempotent: only ADD COLUMN if missing (MySQL 8 lacks IF NOT EXISTS for columns → procedure-style guards via information_schema optional; keep simple ADD and ignore duplicate errors in runner).

ALTER TABLE a2a_messages
  ADD COLUMN locked_by VARCHAR(64) NULL COMMENT 'worker id holding lease' AFTER status,
  ADD COLUMN lease_until DATETIME NULL COMMENT 'lease expiry UTC' AFTER locked_by,
  ADD COLUMN attempt_count INT NOT NULL DEFAULT 0 COMMENT 'delivery attempts' AFTER lease_until,
  ADD COLUMN last_error VARCHAR(512) NULL COMMENT 'last failure message' AFTER attempt_count,
  ADD COLUMN result_payload JSON NULL COMMENT 'cached completed result' AFTER artifact_ids;

CREATE INDEX idx_a2a_claim ON a2a_messages (status, lease_until, updated_at);
