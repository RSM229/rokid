-- 2026-10-28 项目新增「工程类型」字段 eng_type（房建 / 市政 / 其他，NULL=未指定）
--
-- 用途：举牌验收要点匹配时消歧（房建/市政存在同名工序）。Agent 优先读该字段，
--       为空再回退按项目名/描述关键词推断（app/services/agent_tools.py::_project_kind_from）。
--
-- 适用：生产 MySQL。本地 SQLite 由 init_db 的 _reconcile_missing_columns 自动补列，
--       无需本迁移。
-- 幂等：列已存在时跳过，可重复执行。

SET @db = DATABASE();
SET @has_col = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'projects' AND COLUMN_NAME = 'eng_type'
);
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE projects ADD COLUMN eng_type VARCHAR(16) NULL COMMENT ''工程类型: 房建|市政|其他'' AFTER description',
    'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 存量项目可人工回填（示例，按业务核对后执行）：
-- UPDATE projects SET eng_type = '房建' WHERE name LIKE '%住宅%' AND eng_type IS NULL;
-- UPDATE projects SET eng_type = '市政' WHERE (name LIKE '%道路%' OR name LIKE '%桥梁%' OR name LIKE '%管网%') AND eng_type IS NULL;
