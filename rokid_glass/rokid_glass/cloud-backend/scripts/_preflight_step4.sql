SELECT '--- invite_codes 列 ---' AS s;
SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='invite_codes';
SELECT '--- users 审批/组织列 ---' AS s;
SELECT COLUMN_NAME, IS_NULLABLE, IFNULL(COLUMN_DEFAULT,'(none)') AS dflt
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'
  AND COLUMN_NAME IN ('audit_status','approved_by','approved_at','reject_reason','org_id','org_role');
SELECT '--- audit_status 分布 ---' AS s;
SELECT audit_status, COUNT(*) AS n FROM users GROUP BY audit_status;
SELECT '--- org_role 分布 ---' AS s;
SELECT IFNULL(org_role,'NULL') AS org_role, COUNT(*) AS n FROM users GROUP BY org_role;
SELECT '--- invite_codes 现状 ---' AS s;
SELECT COUNT(*) AS total FROM invite_codes;
SELECT '--- 索引 ix_invite_codes_org_id ---' AS s;
SELECT COUNT(*) AS idx_exists FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='invite_codes' AND INDEX_NAME='ix_invite_codes_org_id';
