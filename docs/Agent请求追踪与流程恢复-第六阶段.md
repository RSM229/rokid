# Agent 请求追踪与流程恢复：第六阶段

## 1. 阶段目标

前五个阶段已经形成“请求计划 -> 事实与事项 -> 聊天内草稿 -> 用户确认 -> 批量写入”的业务主链路。本阶段补齐两个生产可靠性能力：

1. 以 `request_id` 为主键，记录一次业务请求经过规划、保存、确认和执行时的状态、耗时、结果与错误。
2. 以 `execution_id` 为幂等键，把正式写入建模成可跨进程、跨重启恢复的执行实例，确保超时或断线后不会重复建档。

本阶段不改变业务规则，也不替代权限、字段校验、草稿版本和用户确认。追踪表不能成为业务事实来源；正式结果仍以工作记录、隐患记录和业务流程表为准。

## 2. 责任边界

### 模型

- 只输出受控的动作、记录类型、事实和事项候选。
- 不生成 `request_id`、权限、正式记录 ID 或执行结果。
- 模型失败时由服务端降级，不阻断追踪表的独立写入。

### 服务端

- 创建并延续稳定的 `request_id`。
- 为正式写入生成稳定 `execution_id`，校验用户、项目、业务范围和修订号。
- 在正式业务写入前独立提交 `RUNNING` 执行实例。
- 将正式业务行、草稿终态和 `COMMITTED` 放在同一数据库事务中提交。
- 对已提交的执行回放原结果，不再执行业务写入。
- 对中断后仍为 `RUNNING` 的执行允许安全重试。

### 小程序

- 在一次用户操作开始时生成稳定操作 ID，并在网络重试时复用。
- 正常请求直接消费服务端结果。
- 超时、断网或服务端 5xx 时按 `execution_id` 查询执行结果。
- 不根据本地缓存自行宣称“已建档”；只有服务端返回 `COMMITTED` 或草稿已执行时才更新界面。

## 3. 请求追踪模型

表：`agent_request_traces`

核心字段：

| 字段 | 含义 |
| --- | --- |
| `request_id` | 一次业务操作链路的稳定 ID，全局唯一 |
| `user_id` / `project_id` | 追踪所有权和项目范围 |
| `conversation_id` | 关联的智能体会话 |
| `endpoint` | 最近进入的处理端点 |
| `status` | `running`、`completed`、`failed` |
| `current_stage` | 当前或最后完成的处理阶段 |
| `message_excerpt` | 用户输入摘要，限制长度，避免记录无边界正文 |
| `provider` / `model` | 实际选择或返回的模型信息 |
| `request_plan_json` | 结构化请求计划快照 |
| `stages_json` | 各阶段状态、开始/结束时间和耗时 |
| `result_json` | 业务结果摘要 |
| `error_stage` / `error_message` | 失败阶段和可诊断错误 |
| `review_plan_id` | 关联整理计划 |
| `created_record_refs_json` | 正式创建的工作记录或隐患记录引用 |

追踪写入使用独立数据库会话。追踪表未迁移、临时不可写或写入异常时，只记录服务端日志，不回滚已经合法的业务请求。数据库短暂不可用采用 30 秒熔断，之后自动重试，不会在进程整个生命周期内永久关闭追踪。

## 4. 已接入端点

```text
POST /api/agent/request-plan
POST /api/agent/chat
POST /api/agent/review-plans
PUT  /api/agent/review-plans/{id}
POST /api/agent/review-plans/{id}/confirm
POST /api/agent/review-plans/{id}/execute
POST /api/hazard/commit-candidates
```

阶段名称：

```text
request_plan
agent_chat
review_plan_save
review_confirm
review_execute
hazard_batch
```

查询接口：

```text
GET /api/agent/requests?limit=20
GET /api/agent/requests/{request_id}
GET /api/agent/executions/{execution_id}
```

请求追踪按当前账户隔离；设置项目时再按当前项目过滤。执行实例只能由创建它的同一用户、同一项目和同一业务范围读取。

## 5. 执行实例状态机

表：`agent_flow_instances`

```text
prepare
  -> RUNNING
       -> COMMITTED
       -> FAILED

RUNNING retry
  -> RUNNING
       -> COMMITTED
       -> FAILED
```

- `RUNNING`：执行意图已持久化，但正式业务事务尚未确认提交。进程退出后可以安全重试。
- `COMMITTED`：正式业务行和执行结果已经提交。后续同键请求只回放结果。
- `FAILED`：最近一次尝试已明确失败，业务事务已回滚。同键请求可以重新尝试。

`attempts` 记录尝试次数。重启恢复不依赖内存任务、WebSocket 或小程序页面状态。

## 6. 事务与幂等边界

### 工作记录批量执行

幂等键：

```text
review-plan:{plan_id}:revision:{revision}
```

事务顺序：

```text
开始请求追踪
  -> 校验用户、项目和整理计划
  -> 独立提交 RUNNING 执行实例
  -> 锁定执行实例和整理计划
  -> 校验修订号、权限、媒体归属和重复挂接
  -> 创建全部 WorkRecord
  -> 更新整理计划为 executed
  -> 更新执行实例为 COMMITTED
  -> 同一事务提交
```

任一业务步骤失败时整批回滚；随后由独立事务把执行实例标记为 `FAILED`。如果进程在业务事务提交前退出，只留下 `RUNNING`，重试不会产生半批记录。如果进程在提交后、响应前退出，数据库里已有完整业务行和 `COMMITTED`，客户端查询后直接恢复结果。

### 隐患批量执行

幂等键由小程序在隐患候选生成时创建，并在重试中复用。服务端同时计算候选内容摘要：

```text
execution_id = 客户端稳定执行 ID
scope_id      = hazard-batch:{规范化候选 SHA-256}
```

同名执行 ID 只能被同一用户、项目、流程类型、候选内容和用户读取。内容变化后复用旧 ID 会被拒绝，防止“用同一个执行键提交另一批隐患”。

## 7. 断线与重启恢复

正常流程：

```text
客户端提交确认
  -> 服务端执行并 COMMITTED
  -> 返回正式记录引用
  -> 客户端展示结果
```

响应丢失：

```text
客户端超时/断网
  -> 查询 GET /agent/executions/{execution_id}
  -> COMMITTED：读取并展示原结果
  -> FAILED：提示本批已回滚，需要重新确认
  -> RUNNING：提示结果确认中，稍后重新进入查看，不再次生成
```

服务端重启：

- 已提交实例不依赖内存恢复。
- 仍为 `RUNNING` 的实例可在用户重试时重新进入受控执行。
- 整理计划和会话草稿继续由原有数据库表恢复。
- 已提交实例再次收到同键请求时只回放原结果。

## 8. 并发控制

- 执行实例使用唯一索引保证全局一个 `execution_id` 只对应一条流程。
- 正式执行前使用数据库行锁锁定执行实例。
- 加锁查询强制刷新当前会话中的实体状态，防止第二个并发恢复请求读取旧的 `RUNNING` 快照后重复执行。
- 整理计划使用 `revision` 和 `confirmed_revision` 校验，防止旧版本确认覆盖新内容。
- 媒体行使用数据库锁并校验 `business_id`，防止同一图片挂接到多条正式记录。

## 9. 部署与迁移

迁移文件：

```text
cloud-backend/migrations/2026-10-21_agent_runtime.sql
```

应用数据库账号需要 DDL 权限。生产环境应由数据库管理员执行迁移；应用启动时会尝试创建缺失的 Agent 历史/运行态表，但只读应用账号无法执行 DDL 时，不能让整个 API 启动失败。

上线顺序：

1. 执行运行态表迁移。
2. 部署后端。
3. 验证 `/health` 和三个追踪查询接口。
4. 部署小程序。
5. 使用测试项目验证一次“确认 -> 断网 -> 查询恢复”的完整流程。

## 10. 验证范围

本阶段自动测试覆盖：

- 请求追踪跨多个阶段追加并保存最终计划、结果和记录引用。
- 追踪库不可用时业务调用继续执行，不被追踪异常反向阻断。
- `RUNNING` 实例中断后可以恢复并转为 `COMMITTED`。
- 已提交实例再次请求时只返回原结果，不新增执行实例。
- 不同用户或不同候选内容不能复用同一个执行 ID。
- 第二个并发恢复请求必须读取数据库中的最新 `COMMITTED` 状态，不能使用会话内旧状态重复写入。
- 小程序 75 项测试和 TypeScript 类型检查通过。
- 后端 39 项 Agent 纯函数回归和 5 项运行态测试通过。

本阶段不替用户在共享正式业务库中创建真实工作记录或隐患记录。正式落库联调应由用户使用专门测试项目完成，或明确指定测试项目和测试数据范围。
