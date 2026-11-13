-- 2026-11-13 Agent 异步任务状态（「说一句就走」）+ 服务侧质量守护结果。
-- 新表由 db.init_db 的 create_all 直接建，本文件供「应用账号无权 ALTER」的
-- 共享只读库环境显式执行；重复执行用 IF NOT EXISTS，安全。
-- 结果登记复用本表（kind=missing_summary / smoke_check），不额外建表。

CREATE TABLE IF NOT EXISTS agent_async_tasks (
  id INT NOT NULL AUTO_INCREMENT,
  task_key VARCHAR(40) NOT NULL,
  user_id INT NOT NULL,
  project_id INT NULL,
  conversation_id VARCHAR(64) NULL,
  kind VARCHAR(32) NOT NULL DEFAULT 'agent_chat',
  status VARCHAR(20) NOT NULL DEFAULT 'queued',
  request_json TEXT NOT NULL,
  result_json TEXT NULL,
  error VARCHAR(500) NULL,
  retry_count INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  lease_until DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_agent_async_tasks_task_key (task_key),
  KEY ix_agent_async_tasks_sweep (status, lease_until),
  KEY ix_agent_async_tasks_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
