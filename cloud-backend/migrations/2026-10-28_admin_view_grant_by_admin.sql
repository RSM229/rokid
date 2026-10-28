-- 2026-10-28 跨项目查看授权升级：由「按项目授权」改为「按管理员授权」
--
-- 背景：原授权粒度是 (被授权管理员, 项目)；现改为 (被授权管理员, 源管理员)，
--       即授权某管理员查看另一个管理员名下的全部项目。模型列 project_id 改名
--       grantor_admin_id，唯一约束随之变为 (grantee_user_id, grantor_admin_id)。
--
-- 适用：生产 MySQL（该表已随 2026-10-20 多项目视图上线）。
-- 本地开发库（SQLite）若不存在旧表，init_db 会直接按新模型建表，无需本迁移；
--   若本地库已存在旧表，DROP TABLE project_view_grants 后由 init_db 重建即可
--   （本地为开发数据，可丢弃）。
--
-- 幂等说明：本迁移对「已是新结构（含 grantor_admin_id）」的情况自动跳过转换；
--   对「仍是旧结构（含 project_id）」才执行转换。可重复执行而不报错。

SET @db = DATABASE();

-- 探测当前结构
SET @has_project  = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'project_view_grants' AND COLUMN_NAME = 'project_id'
);
SET @has_grantor = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'project_view_grants' AND COLUMN_NAME = 'grantor_admin_id'
);

-- ===== 旧结构 -> 新结构 转换（仅当 project_id 存在且 grantor_admin_id 不存在）=====

-- 1) 删除指向 projects 的旧外键（若有），避免 CHANGE 后留下语义错误的外键
SET @drop_old_fk = IF(
    @has_project = 1 AND @has_grantor = 0,
    IFNULL(
        (SELECT GROUP_CONCAT(
                    CONCAT('ALTER TABLE project_view_grants DROP FOREIGN KEY ', CONSTRAINT_NAME)
                    SEPARATOR ' | '
                )
         FROM information_schema.KEY_COLUMN_USAGE
         WHERE TABLE_NAME = 'project_view_grants'
           AND REFERENCED_TABLE_NAME = 'projects'
           AND CONSTRAINT_SCHEMA = @db),
        'SELECT 1'
    ),
    'SELECT 1'
);
PREPARE stmt FROM @drop_old_fk;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2) 清空旧授权数据：旧 (grantee, project) 记录在新语义下无效（project_id 值并非管理员 id），
--    必须清除后由业务在新界面重新按管理员授权。
SET @del_sql = IF(@has_project = 1 AND @has_grantor = 0,
                  'DELETE FROM project_view_grants',
                  'SELECT 1');
PREPARE stmt FROM @del_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3) 改列名：project_id -> grantor_admin_id（唯一索引 uq_view_grant 的列会随之自动更新）
SET @chg_sql = IF(@has_project = 1 AND @has_grantor = 0,
                  'ALTER TABLE project_view_grants CHANGE project_id grantor_admin_id INT NOT NULL COMMENT \'被共享其项目的管理员（源管理员）\'',
                  'SELECT 1');
PREPARE stmt FROM @chg_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 4) 确保指向 users 的外键存在（覆盖「旧转新」与「已是新结构但缺 FK」两种情况）
SET @fk_exists = (
    SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_NAME = 'project_view_grants'
      AND CONSTRAINT_NAME = 'fk_view_grant_grantor_admin'
      AND CONSTRAINT_SCHEMA = @db
);
SET @add_fk = IF(@fk_exists = 0,
                 'ALTER TABLE project_view_grants ADD CONSTRAINT fk_view_grant_grantor_admin FOREIGN KEY (grantor_admin_id) REFERENCES users (id)',
                 'SELECT 1');
PREPARE stmt FROM @add_fk;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ===== projects.owner_id（创建 / 负责的管理员）=====
-- 严格口径："某管理员名下的项目" = 其创建 / 负责的项目（projects.owner_id），
-- 而非其作为成员的全部项目。该列可空；若已存在则跳过。
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'projects' AND COLUMN_NAME = 'owner_id'
);
SET @add_sql = IF(@col_exists = 0,
                  'ALTER TABLE projects ADD COLUMN owner_id INT NULL COMMENT \'创建 / 负责的管理员\'',
                  'SELECT 1');
PREPARE stmt FROM @add_sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 存量项目回填 owner_id（最佳近似）
-- 代码创建项目时创建者即首个 project_members 记录，故取"最早加入的活跃成员"
-- 作为创建 / 负责人。对历史导入项目同样取最早活跃成员。
-- ⚠️ 这是近似回填，上线后请业务侧核对：若某项目实际负责人非最早成员，需手工修正。
UPDATE projects p
SET p.owner_id = (
    SELECT pm.user_id FROM project_members pm
    WHERE pm.project_id = p.id AND pm.left_at IS NULL
    ORDER BY pm.joined_at ASC LIMIT 1
)
WHERE p.owner_id IS NULL;
