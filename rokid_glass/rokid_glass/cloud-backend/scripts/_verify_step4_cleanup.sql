-- Step 4 验收后清理校验（只读）
SELECT '=== users 全量（应无 step4t*）===' AS section;
SELECT id, username, role, org_role, org_id, project_id, audit_status, is_active
FROM users ORDER BY id;

SELECT '=== 残留测试账号数（应为 0）===' AS section;
SELECT COUNT(*) AS step4test_left FROM users WHERE username LIKE 'step4t%';

SELECT '=== 残留测试邀请码（应为 0）===' AS section;
SELECT COUNT(*) AS code_left FROM invite_codes WHERE code = 'STEP4TEST';

SELECT '=== 邀请码现状 ===' AS section;
SELECT id, code, level, role, org_id, used_count, max_uses, is_active FROM invite_codes;

SELECT '=== Step4 新增列确认 ===' AS section;
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND ((TABLE_NAME='users' AND COLUMN_NAME IN ('approved_by','approved_at','reject_reason'))
    OR (TABLE_NAME='invite_codes' AND COLUMN_NAME='org_id'));

SELECT '=== project_members 现状 ===' AS section;
SELECT project_id, user_id, role, extra_roles, left_at FROM project_members ORDER BY project_id, user_id;
