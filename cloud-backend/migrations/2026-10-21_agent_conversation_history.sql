-- Account-scoped agent chat history. Safe to run repeatedly.
-- Apply with a database account that owns schema/DDL privileges.
CREATE TABLE IF NOT EXISTS agent_conversation_messages (
  id INT NOT NULL AUTO_INCREMENT,
  user_id INT NOT NULL,
  conversation_id VARCHAR(64) NOT NULL,
  `role` VARCHAR(16) NOT NULL,
  content_json TEXT NOT NULL,
  created_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  KEY ix_agent_conversation_messages_user_id (user_id),
  KEY ix_agent_conversation_messages_conversation_id (conversation_id),
  KEY ix_agent_conversation_messages_created_at (created_at),
  KEY ix_agent_conversation_messages_user_session_id (user_id, conversation_id, id),
  CONSTRAINT fk_agent_conversation_messages_user
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
