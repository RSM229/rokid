# Agent 语义事实与事项候选：第二阶段

## 1. 阶段目标

把 Agent 从“模型直接选择工具”推进到“模型提交语义候选，服务端确定业务动作”。

本阶段完成后：

- 纯文字和附件请求共用同一套受控动作目录。
- 模型不再需要输出 `tool`；即使旧模型输出工具名，也只能通过动作目录反向映射。
- 用户原话事实、图片证据、模型推断使用不同来源标识。
- 用户明确事实优先于水印和图片观察；低优先级证据只能补充、佐证或提出冲突。
- 服务端根据日期、项目、地点、部位生成零个、一个或多个事项候选。
- 候选计划、字段状态、冲突和事项数量都返回给调用方，但计划本身仍 `write_allowed=false`。

## 2. 语义帧

聊天模型现在输出：

```json
{
  "intent": "create_record",
  "actions": [
    {"id": "record_work", "confidence": 0.9, "reason": "用户要求记录现场工作"}
  ],
  "record_type": "巡视",
  "record_type_confidence": 0.88,
  "facts": [
    {
      "field": "record_date",
      "value": "2026-10-18",
      "source_span": "10月18日",
      "confidence": 0.98
    }
  ],
  "args": {
    "section": "3号楼二层",
    "work_content": "钢筋绑扎"
  },
  "answer": ""
}
```

模型不能指定事实来源或信任状态。`facts` 进入服务端后统一标记为
`user_message_model` / `user_stated_candidate`，然后再与确定性抽取、历史草稿和图片证据合并。

## 3. 责任边界

### 模型负责

- 理解自然语言、同义表达和上下文指代。
- 提出一个或多个动作候选。
- 提出用户原话中的结构化事实候选。
- 在信息不足时生成面向用户的简短说明。

### 服务端负责

- 动作白名单、记录类型目录和工具映射。
- 用户事实归一化、字段别名处理和来源控制。
- 多轮草稿、明确更正和历史版本。
- 用户事实与图片证据的冲突检测。
- 事项候选分组和数量计算。
- 权限、参数、事务、幂等、审计和最终写库边界。

### 前端负责

- 上传文字、语音、图片和文件。
- 渲染服务端返回的计划、表单、冲突和候选事项。
- 收集用户选择、补充和确认。

## 4. 事实状态

当前事实层支持：

- `user_stated_candidate`：用户原话中提取，尚待用户最终确认。
- `user_confirmed`：用户明确确认。
- `evidence_extracted`：水印、OCR、EXIF 或文件解析得到。
- `vision_observed`：视觉模型观察。
- `model_inferred`：模型推断，不进入用户事实回填。
- `unknown`：没有可用值。
- `conflict`：用户描述与证据不一致。
- `not_applicable`：该字段不适用于当前记录类型。

合并优先级：

```text
用户明确陈述
> 用户后续确认
> 项目基础资料
> 图片水印/OCR
> 视觉模型观察
> 模型推断
> 系统默认值
```

用户说过的日期、地点、部位和工作内容，不会因为图片未识别而变成缺失。若图片给出不同值，计划返回
`field_conflicts`；当前候选保留用户值，同时显示证据值并要求最终确认。

## 5. 事项候选

新增服务端事项规划器，输入为事实和证据，输出为候选事项：

```json
{
  "items": [
    {
      "id": "item_1",
      "record_type": "巡视",
      "fields": {
        "record_date": {"value": "2026-10-18", "source": "evidence", "status": "evidence_extracted"},
        "section": {"value": "3号楼二层", "source": "evidence", "status": "evidence_extracted"}
      },
      "evidence_indexes": [0, 1, 4],
      "missing_fields": ["location"],
      "issues": [],
      "confidence": 0.9,
      "status": "candidate"
    }
  ],
  "candidate_count": 1,
  "unassigned_evidence_indexes": [],
  "requires_confirmation": true,
  "write_allowed": false
}
```

分组规则：

- 只使用明确的日期、项目、地点和部位作为身份字段。
- 缺失值不视为冲突。
- 相同身份字段可以合并证据。
- 不同日期、项目、地点或部位形成不同候选事项。
- 完全没有身份字段的资料不静默并入已知事项。
- 图片数量不等于记录数量。
- `candidate_count` 只是候选数量，不能直接触发批量写入。

## 6. 请求计划结构

`POST /api/agent/request-plan` 和 `/api/agent/chat` 返回的计划新增：

```json
{
  "schema_version": 2,
  "facts": [],
  "field_status": {},
  "field_conflicts": [],
  "item_plan": {},
  "trace": {
    "planner_version": "2026-10-21.2",
    "action_ids": [],
    "clarification_fields": [],
    "fact_count": 0,
    "conflict_count": 0,
    "candidate_count": 0,
    "write_allowed": false
  }
}
```

`trace` 目前提供一次请求的计划级追踪摘要。后续可以把用户原话摘要、证据摘要、规则命中、用户确认和执行结果追加到持久化请求追踪表。

## 7. 保留的兼容层

现有工具执行器、字段表、草稿表和文书生成能力不推倒。新的计划层位于它们上方：

```text
语义帧
  -> RequestPlan
  -> FactMerge / Conflict
  -> ItemPlan
  -> 现有草稿与字段追问
  -> 现有 Tool Execution
```

旧模型若仍返回 `tool`，服务端会把它映射回动作目录；未知工具和未知动作都会被丢弃。

## 8. 尚未完成

- 事项候选的聊天内可视化编辑尚未接入全部入口。
- 每个候选事项的确认版本和批量事务执行仍需统一。
- 表单字段还应从目录生成，并携带字段来源、可编辑性和确认状态。
- 请求追踪目前存在于响应中，尚未落独立追踪表。
- 多动作请求仍以保留候选为主，尚未形成可中断恢复的完整流程实例。

## 9. 验证

已覆盖：

- 模型不能自授可信来源。
- 字段别名归一和未知字段拒绝。
- 用户事实与证据冲突检测。
- 图片同日期、同项目、同部位合并为一个候选。
- 不同日期或部位形成多个候选。
- 完全无身份字段的图片保持待归类和低置信度。
- 无图片的文字记录可以形成单个候选。
- 查询类动作不形成记录候选。
- 所有现有执行工具都必须有受控动作映射。
- 计划始终不能直接写库。
