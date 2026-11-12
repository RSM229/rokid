# Agent 记忆模块：项目规约与个人记忆（2026-11-09）

> 2026-11-12 更名：用户可见名称由「项目记忆」改为「项目规约」；
> 表名（`agent_project_memories`）、接口路径、字段名保持不变，只改中文文案与提示词。

> 落地版本。对应代码：
> `cloud-backend/app/services/agent_scope.py`、`agent_project_memories.py`、
> `agent_preferences.py`、`agent_tools.py`、`app/routers/agent.py`，
> 迁移脚本 `cloud-backend/migrations/2026-11-09_agent_memory_scope.sql`。

## 1. 需求与拍板结论

老板要求：每个项目都要有「项目规约」，由项目总监为主维护，全项目成员共享；
每个人再叠加「自己的记忆」。必须做好**项目之间**和**用户之间**的隔离。

本次按以下口径落地：

| 决策点 | 结论 |
|---|---|
| 项目规约内容 | 只共享总监**显式沉淀**的口径/规则，不自动共享总监的私人会话原话 |
| 个人记忆作用域 | 默认全局跟人走（`project_id=NULL`）；API 支持绑定到当前项目 |
| 项目规约写权限 | `chief_supervisor`（总监）为主，`deputy_chief`（总监代表）代管，`super_admin` 兜底 |
| 普通成员 | 只读；可提交「建议」，总监审批后才生效 |
| 注入优先级 | 本轮用户明确指令 > 项目规约 > 个人记忆；项目与个人冲突以项目为准 |
| 存储 | 项目规约独立表 `agent_project_memories`；个人记忆沿用 `agent_user_preferences` 并补 `project_id` |

## 2. 分层架构

```text
/agent/chat
   │
   ├─ agent_scope.resolve_agent_scope（唯一作用域入口）
   │    project_id = users.project_id（当前上下文）
   │    project_members(left_at IS NULL) 校验成员
   │    conversation 必须绑定同一 project_id
   │
   ├─ 项目规约（project_id）  agent_project_memories  总监写 / 全员读
   ├─ 个人记忆（user_id）      agent_user_preferences  本人写 / 本人读
   ├─ 会话原话/聊天记录       agent_conversation_memory / messages
   │    （user_id + project_id）
   ├─ 会话草稿/旧草稿          agent_conversation_drafts / agent_drafts
   │    （project_id；24h TTL）
   └─ 注入顺序：项目规约 → 个人记忆 → 草稿事实 → 历史原话 → 本轮消息
```

## 3. 数据模型

### 3.1 新增项目规约表

```sql
agent_project_memories
  id INT PK
  project_id INT NOT NULL              -- 记忆跟项目走，换总监不丢
  content VARCHAR(500) NOT NULL
  kind VARCHAR(16)  DEFAULT 'rule'     -- rule/fact/template
  category VARCHAR(32) DEFAULT 'general'
  source VARCHAR(24)                   -- chief_declared/deputy_declared/member_suggested/system
  status VARCHAR(16) DEFAULT 'active'  -- pending/active/disabled/rejected
  active TINYINT(1) DEFAULT 1
  pinned TINYINT(1) DEFAULT 0
  author_id INT NULL
  approved_by INT NULL
  origin_preference_id INT NULL        -- 由个人记忆发布时留痕
  created_at / updated_at
  INDEX (project_id, status), (project_id, active)
```

上限：活跃 20 条/项目、待审批 20 条/项目；同内容去重复用，不堆近似重复行。
超限时报错让管理员先停用旧条目，不静默丢弃项目口径。

### 3.2 既有表补 project_id

| 表 | 新列 | 读取方 |
|---|---|---|
| `agent_conversation_messages` | `project_id` | 历史列表 / 详情 / 删除 |
| `agent_conversation_memory` | `project_id` | 模型上下文原话回放 |
| `agent_conversation_drafts` | `project_id` | 草稿恢复 |
| `agent_drafts`（旧版） | `project_id` | 无 conversation_id 的兼容路径 |
| `agent_user_preferences` | `project_id` | NULL=全局；非空=仅该项目 |

`project_id=NULL` 只代表老数据/无项目上下文；用户已有项目时读取按项目精确匹配：
老会话无法判定归属时宁可不展示，也不并入当前项目（fail-closed）。

## 4. 隔离矩阵与权限

| 场景 | 行为 |
|---|---|
| 同一用户在 P1 / P2 | 项目规约、会话、草稿、历史互不可见；个人全局记忆仍生效 |
| 同项目不同成员 | 项目规约共享；个人记忆/私聊互不可见 |
| 总监 vs 成员 | 总监维护项目规约；成员的私人会话不会自动变成项目规约 |
| 成员提建议 | 落 `pending`，不注入；总监通过后全项目生效 |
| A 项目总监调到 B 项目做监理员 | 写权限按 `project_members` 当前项目岗位判定，B 项目无写权限 |
| 成员离开项目 | 立即失去项目规约/历史读取 |
| 客户端伪造 project_id/user_id | 服务端只从登录态推导；跨项目会话返回 409，越权 403/404 |

总监识别**不读全局 `users.role`**，而是：

```python
select(ProjectMember).where(
    ProjectMember.project_id == project_id,
    ProjectMember.left_at.is_(None),
    ProjectMember.role == "chief_supervisor",
)
```

## 5. 注入与优先级

两块独立 system message（不合并进 `_AGENT_SYSTEM`，保持跑分模板字节稳定）：

1. `本项目规约（由项目总监维护，全项目统一遵守；与「我的记忆」冲突时以本条为准；与用户本轮明确指令冲突时先提示用户并由总监确认，不要静默覆盖）`
2. `我的记忆（本用户长期习惯，跨会话有效；与「本项目规约」冲突时以项目规约为准；除非本轮用户明确指令冲突，否则请遵守）`

预算：项目规约 ≤20 条、个人记忆 ≤20 条；每条 ≤500 字；`pinned` 优先保留。
`load_context` 进度事件会带 `project_memory_count` / `personal_memory_count`，便于排查
「AI 为什么这么说」。

## 6. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/agent/project-memories` | 成员读 active；维护人额外看到 disabled + pending + 维护人列表 + `usage` 使用统计 + `audit` 最近变更/使用审计 |
| POST | `/api/agent/project-memories` | 维护人直接生效；成员写入 pending 建议 |
| PUT/PATCH/POST | `/api/agent/project-memories/{id}` | 编辑/启停/置顶（维护人） |
| DELETE | `/api/agent/project-memories/{id}` | 删除（维护人） |
| POST | `/api/agent/project-memories/{id}/approve` | `{approve: true/false}` 审批成员建议 |
| GET | `/api/agent/preferences` | 个人记忆；返回 `can_manage_project_memory`、`project_name`、`project_id` |
| POST | `/api/agent/preferences` | 新增个人记忆；`project_id` 可绑定当前项目 |
| POST | `/api/agent/preferences/{id}/scope` | `{scope: "global"\|"project"}` 切换个人记忆作用域 |
| POST | `/api/agent/preferences/{id}/publish-to-project` | 总监把个人记忆复制为项目规约 |
| GET/DELETE | `/api/agent/conversations/*` | 自动按当前项目过滤；跨项目 404 |
| GET/POST | `/api/agent/drafts/*` | 自动按当前项目过滤 |

前端入口：小程序「我的 → 项目规约」（新页 `pages/project-memory`）与
「我的 → AI 记忆」（个人记忆，总监可对单条点「发布到项目」）。
聊天历史抽屉的本地缓存也按 `projectId` 过滤。

## 7. 迁移与生命周期

1. **建表/补列**：启动 `db.init_db` 的 `create_all` + `_reconcile_missing_columns` 自动完成；
   共享只读库账号用 `migrations/2026-11-09_agent_memory_scope.sql`（含索引与回填）执行一次。
2. **老数据回填**：启动时 `backfill_agent_project_columns()` 把会话/草稿的 NULL 项目
   回填为用户当前项目；个人记忆故意不回填（NULL=全局）。
3. **项目删除**：`agent_project_memories` / 会话 / 草稿列入项目删除清理集合
   （`project.py::_CLEANUP_TABLES`），随项目物理删除。
   注意：若用户把**个人记忆**绑定到该项目，删除仍会被拦截（避免静默删个人记忆），
   需先在个人记忆里解绑（当前未暴露绑定 UI，属后续能力）。
4. **项目结项**：结项后用户无法再切回（现有项目切换限制），记忆保留不删。
5. **总监变更**：项目规约挂 `project_id`，新总监直接继承；旧总监个人记忆不共享。

## 8. 使用统计与审计

- 项目规约新增 `use_count` / `last_used_at`：每轮聊天实际注入时批量 +1；
- 新增 `agent_project_memory_audit` 流水：`create/suggest/update/enable/disable/delete/approve/reject/publish/use`，
  记录操作人、时间、摘要（`use` 记录本轮注入的 memory_ids 与 conversation_id）；
- 管理页展示「使用统计」（按引用次数排序）与「最近变更 / 使用审计」；
- 审计随项目删除清理，不保存 500 字全文，只留摘要。

## 9. 已知边界 / 后续

- 不自动共享总监聊天原话：仅共享显式沉淀的项目规约；如需「总监会话摘要自动入库」，
  应作为独立能力（需脱敏与审批）另做。
- 个人记忆的「绑定到当前项目」已有页面开关（新增时选择 + 已保存条目一键切换）。
- 旧会话 `project_id=NULL` 且用户已有项目时，首次续聊会认领到当前项目；无法判定的
  仍以新建对话兜底。
- 未做项目规约的使用趋势图/导出；审计表未做保留期清理（可按需加定时归档）。
- `agent_conversation_memory` 的唯一索引仍是 `(user_id, conversation_id)`；
  前端 conversation_id 由时间戳生成，实际不会跨项目复用。

## 9. 验证

- `tests/test_agent_scope.py`：成员/非成员、总监/代管、跨项目会话拒绝、管理员兜底。
- `tests/test_agent_project_memory.py`：项目隔离、去重、上限、待审批/通过/驳回、
  发布个人→项目、个人记忆全局+项目绑定。
- `tests/test_agent_conversation_history.py`：会话/原话/图片按项目隔离与删除。
- `tests/test_agent_preferences.py`：个人记忆仅本人可编辑/删除。
- 小程序：`npm run typecheck` 与 `npm test`（124 项页面契约全过）。
