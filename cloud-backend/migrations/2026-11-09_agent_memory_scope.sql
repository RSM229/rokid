-- 2026-11-09 Agent 记忆分层：项目记忆 + 个人记忆，作用域（项目/用户）隔离。
-- 以 schema-owner 账号执行一次；重复执行时 ADD COLUMN 会报错，请按需跳过。
-- 应用启动时 db._reconcile_missing_columns 会自动补列，但本文件用于共享只读库
-- 账号部署（应用无权 ALTER）以及需要显式索引/回填的正式环境。

-- ---------------------------------------------------------------------------
-- 1) 项目记忆（总监维护、全项目共享）—— 与个人记忆物理分表
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_project_memories (
  id INT NOT NULL AUTO_INCREMENT,
  project_id INT NOT NULL,
  content VARCHAR(500) NOT NULL,
  kind VARCHAR(16) NOT NULL DEFAULT 'rule',
  category VARCHAR(32) NOT NULL DEFAULT 'general',
  source VARCHAR(24) NOT NULL DEFAULT 'chief_declared',
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  active TINYINT(1) NOT NULL DEFAULT 1,
  pinned TINYINT(1) NOT NULL DEFAULT 0,
  author_id INT NULL,
  approved_by INT NULL,
  origin_preference_id INT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  KEY ix_agent_project_memories_project_id (project_id),
  KEY ix_agent_project_memories_project_status (project_id, status),
  KEY ix_agent_project_memories_project_active (project_id, active),
  KEY ix_agent_project_memories_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 2) 会话 / 草稿加项目隔离键（NULL = 老数据/无项目上下文）
-- ---------------------------------------------------------------------------
ALTER TABLE agent_conversation_messages
  ADD COLUMN project_id INT NULL,
  ADD KEY ix_agent_conversation_messages_user_project (user_id, project_id);

ALTER TABLE agent_conversation_memory
  ADD COLUMN project_id INT NULL,
  ADD KEY ix_agent_conversation_memory_user_project (user_id, project_id);

ALTER TABLE agent_conversation_drafts
  ADD COLUMN project_id INT NULL,
  ADD KEY ix_agent_conversation_drafts_project_id (project_id);

ALTER TABLE agent_drafts
  ADD COLUMN project_id INT NULL,
  ADD KEY ix_agent_drafts_project_id (project_id);

-- 个人记忆：NULL = 跟人走（全局生效）；非空 = 仅该项目生效。
ALTER TABLE agent_user_preferences
  ADD COLUMN project_id INT NULL,
  ADD KEY ix_agent_user_preferences_project_id (project_id);

-- ---------------------------------------------------------------------------
-- 3) 老数据回填：把历史会话/草稿挂到用户“当前项目”
--    个人记忆刻意不回填：NULL 表示全局个人记忆。
-- ---------------------------------------------------------------------------
UPDATE agent_conversation_messages
SET project_id = (SELECT users.project_id FROM users WHERE users.id = agent_conversation_messages.user_id)
WHERE project_id IS NULL
  AND (SELECT users.project_id FROM users WHERE users.id = agent_conversation_messages.user_id) IS NOT NULL;

UPDATE agent_conversation_memory
SET project_id = (SELECT users.project_id FROM users WHERE users.id = agent_conversation_memory.user_id)
WHERE project_id IS NULL
  AND (SELECT users.project_id FROM users WHERE users.id = agent_conversation_memory.user_id) IS NOT NULL;

UPDATE agent_conversation_drafts
SET project_id = (SELECT users.project_id FROM users WHERE users.id = agent_conversation_drafts.user_id)
WHERE project_id IS NULL
  AND (SELECT users.project_id FROM users WHERE users.id = agent_conversation_drafts.user_id) IS NOT NULL;

UPDATE agent_drafts
SET project_id = (SELECT users.project_id FROM users WHERE users.id = agent_drafts.user_id)
WHERE project_id IS NULL
  AND (SELECT users.project_id FROM users WHERE users.id = agent_drafts.user_id) IS NOT NULL;
