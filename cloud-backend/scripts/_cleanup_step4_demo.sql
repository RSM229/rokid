-- 清理 Step 4 UI 验证演示数据（demo01/demo02 + STEP4DEMO 邀请码）
DELETE FROM project_members WHERE user_id IN
  (SELECT id FROM users WHERE username IN ('demo01','demo02'));
DELETE FROM security_audit WHERE detail LIKE '%demo0%';
DELETE FROM users WHERE username IN ('demo01','demo02');
DELETE FROM invite_codes WHERE code = 'STEP4DEMO';

SELECT '=== 清理后 users ===' AS s;
SELECT id, username, role, org_role, audit_status FROM users ORDER BY id;
SELECT '=== 残留（均应为 0）===' AS s;
SELECT
  (SELECT COUNT(*) FROM users WHERE username IN ('demo01','demo02')) AS users_left,
  (SELECT COUNT(*) FROM invite_codes WHERE code='STEP4DEMO') AS code_left;
SELECT '=== 邀请码现状 ===' AS s;
SELECT id, code, role, org_id, is_active FROM invite_codes;
