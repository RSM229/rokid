# 语音拍照自动创建工作记录

## Context

当前流程：用户拍照后弹出 `askImagePurpose` 选择用途 → 选"生成工作记录" → `showWorkFormOptions` 弹出 6 项 ActionSheet → 走"语音描述现场"或"手动填写"或"新建某类记录" → 最终弹出 **全屏表单 overlay**（`showWorkForm`），用户手动填写后提交。

用户需求：拍照 + 语音描述 → AI 自动提取字段并创建记录，**表单不出现**，缺失字段由 AI 在对话中追问，用户语音补充，全程语音交互（STT 输入 + TTS 播报）。记录只在后台管理端展示。

## 改动范围

### 1. 后端：新增 `POST /work-session/voice-create`

文件：`cloud-backend/app/routers/work_session.py`

**职责**：接收累计语音文本 + 可选照片 → VLM 分析照片 → LLM 提取字段 + 判断缺失 → 字段齐全则直接创建记录，否则返回追问提示。

**请求**（multipart，因为要传照片）：
```
POST /work-session/voice-create
- voice_text: str (累计的语音文本)
- photo: UploadFile (可选，首次发送时附带)
- previous_text: str (可选，前几轮的累计文本，用于让 LLM 看到完整上下文)
```

**处理流程**：
1. 如有照片 → 调用 `ai_client.detect_hazard(photo)` 获取 VLM 结果（scene_related / risk_category / evidence 等作为场景上下文）
2. 构造 LLM prompt（见下），调用 `model_gateway.chat_completion`
3. 解析 JSON → 得到 work_type / fields / missing / complete
4. `complete=true` → 根据 work_type 创建工作记录（复用 `work_record.py` 的创建逻辑，直接 insert WorkRecord）→ 如有照片则保存为取证照片 → 返回 `{ created: true, record_id, record_type, summary }`
5. `complete=false` → 返回 `{ created: false, work_type, missing_fields: [{field, label, prompt}], prompt_text }`

**LLM System Prompt**（增强版，含照片分析结果 + 必填字段 schema）：
```
你是工程监理语音助手。从现场语音文本{和照片分析结果}中提取工作记录字段。

各类型必填字段：
- 巡视：section(巡视部位), work_content(施工情况)
- 验收：acceptance_type(验收类型), section(施工区域), acceptance_content(验收内容), acceptance_result(验收结果)
- 旁站：station_project_name(旁站工程), station_location(旁站部位), start_time(开始时间), end_time(结束时间), station_content(施工情况), station_supervision_status(监理情况)
- 见证：witness_material_name(材料名称), witness_quantity(取样数量)

只返回JSON：
{ "work_type":"巡视/验收/旁站/见证", "fields":{...}, "missing":[{"field":"x","label":"巡视部位","prompt":"请问巡视部位在哪里？"}], "complete":true/false }
```

**创建记录**：在 work_session.py 中导入 WorkRecord 模型，直接创建。复用 work_record.py 中各类型的字段映射，但简化为直接 insert + flush。照片保存逻辑参考 `create_inspection_with_photo`（L482-540）中的水印照片保存方式。

### 2. 前端 agent/index.ts：重写照片→记录流程

**修改 `askImagePurpose`（L645）**：
- ActionSheet 简化为两项：`['隐患识别（AI 检查现场风险）', '语音记录现场工作（拍照+语音自动创建）']`
- 选第 2 项 → 不再调 `showWorkFormOptions()`，改调新方法 `startVoiceWorkSession(path)`

**新增 `startVoiceWorkSession(photoPath: string)`**：
- 设置 `voiceSessionActive=true`, `voiceWorkMode=true`, `pendingImage=photoPath`
- 添加消息：`📸 照片已选择。请按住说话描述现场工作，例如"3号楼二层钢筋绑扎，天气晴..."`
- TTS 播报该提示

**修改 `extractVoiceSession`（L745）→ 改为 `autoCreateWorkSession()`**：
- 不再调 `/work-session/extract` + `applyExtractedFields` + 打开表单
- 改为上传照片（如有）+ 调 `/work-session/voice-create`（multipart：voice_text + photo + previous_text）
- 响应 `created: true` → 添加消息 "✅ {record_type}记录已创建" + TTS → 退出语音会话
- 响应 `created: false` → 添加消息 "{prompt_text}" + TTS → **保持语音会话**，清空当前片段，等待用户继续说话

**修改 `recorder.onStop`（L358）中的语音会话分支**：
- 当 `voiceWorkMode=true` 且 AI 正在追问（`voiceSessionPending=true`）→ STT 完成后自动触发 `autoCreateWorkSession()`（无需用户手动点按钮）
- 首次需用户手动点"AI提取并创建"按钮，后续追问轮自动触发

**修改 `handleActionTap`（L598）**：
- `command === 'show-form'` 不再打开表单 → 改为不操作（或移除该 action）

**保留但不从 agent 页调用的方法**：`openWorkForm`, `submitWorkForm`, `postWorkForm`, `applyExtractedFields`（它们仍被"工作记录"命令路径引用，但 `showWorkFormOptions` 要改为直接进入语音会话或导航到 work 页面）

**修改 `showWorkFormOptions`（L713）**：
- 简化为两项：`['语音描述现场（自动创建）', '手动填写（前往工作记录页）']`
- 第 1 项 → `startVoiceWorkSession('')`（无照片场景）
- 第 2 项 → `wx.navigateTo({ url: '/pages/work/index?create=1' })`

### 3. 前端 agent/index.wxml：移除表单 overlay

- **删除 L256-344** 的 `Work Record Form` overlay 整段（`wx:if="{{showWorkForm}}"`）
- 语音会话 UI（L347-370）保留并复用，增加"AI提取并创建"按钮的文案改为"完成记录"
- 追问轮中"完成记录"按钮隐藏，改为自动触发

### 4. TTS 自动播报

在 `autoCreateWorkSession` 的成功/追问分支中，调用现有 `speak` 方法（L1491）播报 AI 回复：
```ts
this.speak({ currentTarget: { dataset: { text: answerText } } } as any)
```
或重构 `speak` 为接受纯文本参数的辅助方法。

## 关键文件

| 文件 | 修改 |
|------|------|
| `cloud-backend/app/routers/work_session.py` | 新增 `POST /voice-create` 端点（VLM + LLM 提取 + 创建/追问） |
| `wechat-miniprogram/miniprogram/pages/agent/index.ts` | 重写照片→记录流程，移除表单调用，新增 autoCreate 逻辑 |
| `wechat-miniprogram/miniprogram/pages/agent/index.wxml` | 删除 Work Record Form overlay，调整语音会话 UI |
| `cloud-backend/app/routers/work_record.py` | 只读参考（字段 schema、创建逻辑、照片保存逻辑） |

## 验证

1. 拍照 → 选"语音记录现场工作" → 按住说话描述 → 点"完成记录" → AI 提取并创建 → 聊天显示"✅ 巡视记录已创建" + TTS 播报
2. 拍照 → 语音描述（故意缺少巡视部位）→ AI 追问"请问巡视部位在哪里？" + TTS → 用户按住说话补充 → 自动触发再次提取 → 创建成功
3. 确认表单 overlay 不再出现
4. 确认后台管理端能看到新建的记录
5. "工作记录"命令 → 仍可进入语音会话或跳转工作页
