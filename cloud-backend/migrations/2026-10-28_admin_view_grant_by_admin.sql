-- 2026-10-28 跨项目查看授权升级：由「按项目授权」改为「按管理员授权」
--
-- 背景：原授权粒度是 (被授权管理员, 项目)；现改为 (被授权管理员, 源管理员)，
--       即授权某管理员查看另一个管理员名下的全部项目。模型列 project_id 改名
--       grantor_admin_id，唯一约束随之变为 (grantee_user_id, grantor_admin_id)。
--
-- 适用：生产 MySQL（该表已随 2026-10-20 多项目视图上线）。
-- 本地开发库（SQLite）若不存在旧表，init_db 会直接按新模型建表，无需本迁移；
--   若本地库已存在旧表，DROP TABLE project_view_grants 后由 init_db 重建即可
--   （本地为开发数据，可丢弃；SQLite 的 RENAME COLUMN 会自动更新唯一索引）。

-- 1) 动态删除 project_view_grants 上所有外键（避免硬编码外键名）
SET @db = DATABASE();
SELECT GROUP_CONCAT(
    CONCAT('ALTER TABLE project_view_grants DROP FOREIGN KEY ', CONSTRAINT_NAME)
    SEPARATOR '; '
) INTO @drop_fk_sql
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_NAME = 'project_view_grants'
  AND REFERENCED_TABLE_NAME IS NOT NULL
  AND CONSTRAINT_SCHEMA = @db;

SET @drop_fk_sql = IFNULL(@drop_fk_sql, 'SELECT 1');
PREPARE stmt FROM @drop_fk_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2) 改列名 + 类型/注释（project_id -> grantor_admin_id，NOT NULL 保持与 ORM 一致）
ALTER TABLE project_view_grants
    CHANGE project_id grantor_admin_id INT NOT NULL
    COMMENT '被共享其项目的管理员（源管理员）';

-- 3) 重建唯一约束为 (grantee_user_id, grantor_admin_id)
ALTER TABLE project_view_grants DROP INDEX uq_view_grant;
ALTER TABLE project_view_grants
    ADD UNIQUE KEY uq_view_grant (grantee_user_id, grantor_admin_id);

-- 4) 新建外键 -> users.id（源管理员）
ALTER TABLE project_view_grants
    ADD CONSTRAINT fk_view_grant_grantor_admin
    FOREIGN KEY (grantor_admin_id) REFERENCES users (id);

-- =====================================================================
-- 5) 新增 projects.owner_id（创建 / 负责的管理员）
--    严格口径："某管理员名下的项目" = 其创建 / 负责的项目（projects.owner_id），
--    而非其作为成员的全部项目。该列可空，init_db 的 _reconcile_missing_columns
--    也会自动补齐；此处显式补一次，避免依赖启动逻辑。
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'projects' AND COLUMN_NAME = 'owner_id'
);
SET @add_sql = IF(
    @col_exists = 0,
    'ALTER TABLE projects ADD COLUMN owner_id INT NULL COMMENT \'创建 / 负责的管理员\'',
    'SELECT 1'
);
PREPARE stmt FROM @add_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 6) 存量项目回填 owner_id（最佳近似）
--    代码创建项目时创建者即首个 project_members 记录，故取"最早加入的活跃成员"
--    作为创建 / 负责人。对历史导入项目同样取最早活跃成员。
--    ⚠️ 这是近似回填，上线前请业务侧核对：若某项目实际负责人非最早成员，需手工修正。
UPDATE projects p
SET p.owner_id = (
    SELECT pm.user_id FROM project_members pm
    WHERE pm.project_id = p.id AND pm.left_at IS NULL
    ORDER BY pm.joined_at ASC LIMIT 1
)
WHERE p.owner_id IS NULL;

