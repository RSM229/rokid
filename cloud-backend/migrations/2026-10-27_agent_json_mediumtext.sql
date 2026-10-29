-- 2026-10-27 智能体 JSON 载荷超出 TEXT 上限
--
-- 现象：POST /api/agent/review-plans 返回 500，后端日志：
--   (pymysql.err.DataError) (1406, "Data too long for column 'plan_json' at row 1")
--
-- 根因：MySQL TEXT 上限 65535 **字节**。一次上传 9 张证据时，plan_json 里
-- 的中文与长图片 URL 一起超过上限（此前最大行 32834 字节，本次约 45k 字符）。
-- 同一批 trace 的 stages_json 已到 50373 字节，离上限只差 15k。
--
-- 处理：把智能体链路里的长 JSON 列统一升到 MEDIUMTEXT（16MB）。
-- 可重复执行（MODIFY COLUMN 幂等），当前表数据量很小，ALTER 为秒级。

ALTER TABLE agent_review_plans
  MODIFY COLUMN plan_json MEDIUMTEXT NOT NULL,
  MODIFY COLUMN execution_result_json MEDIUMTEXT NULL;

ALTER TABLE agent_request_traces
  MODIFY COLUMN request_plan_json MEDIUMTEXT NOT NULL,
  MODIFY COLUMN stages_json MEDIUMTEXT NOT NULL,
  MODIFY COLUMN result_json MEDIUMTEXT NOT NULL;

ALTER TABLE agent_flow_instances
  MODIFY COLUMN result_json MEDIUMTEXT NOT NULL;