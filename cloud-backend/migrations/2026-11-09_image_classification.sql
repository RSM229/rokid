-- 2026-11-09 影像记录新增《工程影像资料分类标准》归类字段
--
-- 用途：承载 8 类互斥归类（P/W/Y/M/X/H/C/O）、X 二级类、标签、归档目录/文件名、
--       关联书面资料编号与归类状态。归类逻辑见 app/services/image_classification.py。
--
-- 适用：生产 MySQL（需用户明确点头后执行）。本地 SQLite 由 init_db 的
--       _reconcile_missing_columns 自动补列，无需本迁移。
-- 幂等：每列独立检查，列已存在则 SELECT 1 跳过，可重复执行。
-- 安全：全部 NULL 允许，不改存量数据，不影响历史影像。
--
-- 注意：本文件只是迁移脚本，未部署、未在任何数据库执行过。

SET @db = DATABASE();

-- category_code：一级类别码
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'category_code');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN category_code VARCHAR(8) NULL COMMENT ''一级类别码 P/W/Y/M/X/H/C/O'' AFTER created_at', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- subcategory：X 二级类
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'subcategory');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN subcategory VARCHAR(8) NULL COMMENT ''巡视二级类 X1~X5'' AFTER category_code', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- labels：标签（多值，不参与目录归属）
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'labels');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN labels VARCHAR(255) NULL COMMENT ''标签，顿号分隔'' AFTER subcategory', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- archive_dir：归档目录
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'archive_dir');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN archive_dir VARCHAR(64) NULL COMMENT ''归档目录 01_旁站..08_其他'' AFTER labels', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- archive_filename：按命名规范重命名后的文件名
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'archive_filename');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN archive_filename VARCHAR(255) NULL COMMENT ''YYYYMMDD_类别码序号_单位工程_部位_工序_NN.jpg'' AFTER archive_dir', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- related_doc_no：关联书面资料编号（就近对应原则）
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'related_doc_no');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN related_doc_no VARCHAR(64) NULL COMMENT ''旁站/见证/验收/报验/巡视记录编号'' AFTER archive_filename', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- classification_status：归类状态
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'classification_status');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN classification_status VARCHAR(16) NULL COMMENT ''pending/confirmed/needs_review'' AFTER related_doc_no', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- classification_note：归类理由 / 待确认说明
SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND COLUMN_NAME = 'classification_note');
SET @ddl = IF(@has_col = 0,
    'ALTER TABLE image_records ADD COLUMN classification_note TEXT NULL COMMENT ''归类理由/待确认说明'' AFTER classification_status', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 按类别统计是高频操作，补普通索引（幂等）
SET @has_idx = (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'image_records' AND INDEX_NAME = 'idx_image_category');
SET @ddl = IF(@has_idx = 0,
    'ALTER TABLE image_records ADD INDEX idx_image_category (category_code)', 'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
