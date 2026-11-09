-- 2026-11-09 项目记忆：使用统计 + 变更/使用审计
-- 在 2026-11-09_agent_memory_scope.sql 之后执行；应用启动的 create_all
-- 也会自动建表/补列，本文件用于共享只读库账号与正式环境显式部署。

ALTER TABLE agent_project_memories
  ADD COLUMN use_count INT NOT NULL DEFAULT 0 COMMENT '被注入到对话上下文的次数',
  ADD COLUMN last_used_at DATETIME NULL COMMENT '最近一次被注入的时间',
  ADD KEY ix_agent_project_memories_last_used_at (last_used_at);

CREATE TABLE IF NOT EXISTS agent_project_memory_audit (
  id INT NOT NULL AUTO_INCREMENT,
  project_id INT NOT NULL,
  memory_id INT NULL,
  action VARCHAR(24) NOT NULL,
  actor_id INT NULL,
  detail_json TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  KEY ix_agent_project_memory_audit_project_id (project_id),
  KEY ix_agent_project_memory_audit_memory_id (memory_id),
  KEY ix_agent_project_memory_audit_created_at (created_at),
  KEY ix_agent_project_memory_audit_project_created (project_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
