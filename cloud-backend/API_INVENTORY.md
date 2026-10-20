# Cloud Backend API Inventory

> Auto-generated from source. Base: `D:\rokid\rokid_glass\rokid_glass\cloud-backend`
> Total routers: 26 · Total endpoints: ~185+

---

## 1. Directory Structure

```
cloud-backend/
├── .env / .env.example
├── main.py                          # uvicorn entrypoint
├── requirements.txt
├── import_simple.py
├── tools_dump_templates.py
├── app/
│   ├── config.py
│   ├── db.py
│   ├── main.py                      # FastAPI app + include_router
│   ├── models/                      # 24 SQLAlchemy model files + __init__
│   │   ├── acceptance_template.py
│   │   ├── archive.py
│   │   ├── document.py
│   │   ├── hazard.py
│   │   ├── image.py
│   │   ├── inspection_session.py
│   │   ├── invite_code.py
│   │   ├── knowledge_base.py
│   │   ├── knowledge_graph.py
│   │   ├── ledger.py
│   │   ├── miniprogram.py
│   │   ├── model_config.py
│   │   ├── note.py
│   │   ├── notification.py
│   │   ├── project.py
│   │   ├── rectification.py
│   │   ├── regulation_chunk.py
│   │   ├── regulation_version.py
│   │   ├── signature.py
│   │   ├── task_assignment.py
│   │   ├── user.py
│   │   ├── video.py
│   │   ├── wechat_subscription.py
│   │   ├── work_record.py
│   │   └── __init__.py
│   ├── routers/                     # 26 router files + __init__
│   │   ├── acceptance_template.py
│   │   ├── ai_extras.py
│   │   ├── auth.py
│   │   ├── document.py
│   │   ├── form_doc.py
│   │   ├── hazard.py
│   │   ├── inspection.py
│   │   ├── knowledge.py
│   │   ├── knowledge_base.py
│   │   ├── knowledge_graph.py
│   │   ├── ledger.py
│   │   ├── local_ai.py
│   │   ├── media.py
│   │   ├── miniprogram_auth.py
│   │   ├── model_settings.py
│   │   ├── note.py
│   │   ├── omni.py
│   │   ├── project.py
│   │   ├── signature.py
│   │   ├── simulator.py
│   │   ├── task.py
│   │   ├── watermark.py
│   │   ├── wechat_notice.py
│   │   ├── work_record.py
│   │   ├── work_session.py
│   │   └── ws.py
│   ├── services/                    # 20 service modules
│   │   ├── acceptance_template_service.py
│   │   ├── ai_client.py
│   │   ├── doc_generator.py
│   │   ├── form_registry.py
│   │   ├── form_renderer.py
│   │   ├── inspection_service.py
│   │   ├── kb_seed.py
│   │   ├── knowledge_service.py
│   │   ├── ledger_exporter.py
│   │   ├── media_validation.py
│   │   ├── model_config_service.py
│   │   ├── model_gateway.py
│   │   ├── notification.py
│   │   ├── passwords.py
│   │   ├── regulation_category_service.py
│   │   ├── regulation_matcher.py
│   │   ├── regulation_version_service.py
│   │   ├── signature_service.py
│   │   ├── watermark_service.py
│   │   └── wechat_notice.py
│   └── static/
│       ├── glasses-simulator.html
│       └── voice-test.html
├── exports/                         # Generated docs, archives, drawings
├── scripts/
│   ├── import_knowledge_to_graph.py
│   └── init_default_users.py
├── tests/                           # 10 test files
├── uploads/                         # Runtime uploads (images, archives, etc.)
└── 知识库/                           # Knowledge base files (省统表, 安全文明用表)
```

> **Note:** `rtc.py` and `rtc_service.py` exist only as stale `.pyc` files (module removed from source).

---

## 2. Router Index

| Router Module | Prefix | Chinese Name | Endpoints |
|---|---|---|---|
| auth | `/api/auth` | 认证鉴权 | 12 |
| hazard | `/api/hazard` | 隐患识别 | 10 |
| document | `/api/document` | 监理文书 | 11 |
| knowledge | `/api/knowledge` | 知识库 | 18 |
| knowledge_base | `/api/kb` | 个人/共享知识库 | 22 |
| note | `/api/notes` | 笔记 | 5 |
| ai_extras | `/api/ai` | AI扩展 | 3 |
| ledger | `/api/ledger` | 台账管理 | 10 |
| watermark | `/api/watermark` | 水印服务 | 3 |
| local_ai | `/api/local` | 本地AI服务 | 6 |
| work_record | `/api/work-record` | 工作记录 | 26 |
| task | `/api/task` | 派单管理 | 15 |
| knowledge_graph | `/api/kg` | 知识图谱 | 9 |
| project | `/api/project` | 项目管理 | 9 |
| media | `/api/media` | 统一媒体 | 8 |
| signature | `/api` (signatures/docs) | 电子签章 | 5 |
| inspection | `/api/inspection` | 云端巡检 | 11 |
| ws | `/api/ws` | WebSocket实时推送 | 1 (WS) |
| acceptance_template | `/api/acceptance-templates` | 验收流程模板 | 6 |
| wechat_notice | `/api/wechat` | 微信服务通知 | 4 |
| omni | `/api/omni` | 语音交互兼容接口 | 6 |
| model_settings | `/api/model-settings` | 模型调用设置 | 3 |
| form_doc | `/api/form-docs` | 省统表文书 | 5 |
| work_session | `/api/work-session` | 眼镜端工作会话 | 1 |
| simulator | `/api` (simulator/ws) | 眼镜端模拟器 | 7 |
| miniprogram_auth | `/api/miniprogram/auth` | 微信小程序认证 | 5 |

**Static mounts:** `GET /uploads/*`, `GET /exports/*`, `GET /simulator/*` (glasses-simulator.html)
**Health:** `GET /health`

---

## 3. Authentication & Authorization

- **JWT:** `OAuth2PasswordBearer(tokenUrl="/api/auth/login")`
- **Token payload:** `{sub, role, permissions, purpose:"access", exp}`
- **Helpers:** `get_current_user`, `require_permission(name)`, `require_role(*roles)`, `require_project_id(user)`
- **Roles (Chinese):** 总监理工程师 / 总监代表 / 专业监理工程师 / 监理员
- **`admin` username:** grants all permissions

---

## 4. Endpoint Details

### 4.1 auth (`/api/auth`) — 12 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/roles` | 获取角色列表及权限 | — | `RoleInfoResponse{roles, registration_roles, public_registration_enabled}` |
| POST | `/login` | 登录 | `LoginRequest{username, password, role}` | `TokenResponse{access_token, token_type, user_id, username, real_name, role, permissions, avatar_url, project_id, project_name}` |
| POST | `/logout` | 退出 | — | `{detail, user_id}` |
| POST | `/register` | 注册 | `RegisterRequest{username, password, real_name, role, phone?, invite_code, company_name?}` | `TokenResponse` |
| GET | `/me` | 当前用户信息 | — | `{user_id, username, real_name, role, phone, id_card, certificate_type, certificate_no, gender, email, avatar_url, remark, project_id, project_name, is_active, permissions, company_name, invite_code, created_at}` |
| PUT | `/me/profile` | 修改资料 | `dict{real_name?, phone?, gender?, email?, remark?}` | `{detail}` |
| PUT | `/me/password` | 修改密码 | `ChangePasswordRequest{current_password, new_password}` | `{detail}` |
| GET | `/users` | 用户列表 | — | `{items: [{id, username, real_name, role, phone, project_id, is_active, wechat_bound, company_name, invite_code, created_at}]}` |
| POST | `/users` | 创建用户 | `CreateUserRequest{username, password, real_name, role, phone?, project_id?}` | `{detail, user_id}` |
| PUT | `/users/{user_id}` | 修改用户 | `UpdateUserRequest{real_name?, role?, phone?, project_id?, is_active?, new_password?}` | `{detail}` |
| POST | `/glasses/qr-create` | 生成眼镜扫码凭证 | — | `{ticket, expires_in}` |
| POST | `/glasses/scan` | 眼镜扫码登录 | `GlassesScanRequest{ticket}` | `TokenResponse` |

---

### 4.2 hazard (`/api/hazard`) — 10 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/detect` | 上传图片隐患识别（不保存） | multipart: `image`, `prompt?` | `HazardResult{risk_status, risk_category, top1_risk, evidence, uncertainty, recheck_suggestion, has_hazard, scene_related, matched_regulations}` |
| POST | `/detect-and-save` | 识别隐患并保存 | multipart: `image`, `section?`, `location?`, `remark?`, `weather?` | `{hazard_id, result, image_url, saved_as_hazard}` |
| GET | `/list` | 隐患列表 | query: `status_filter?`, `risk_status?` | `[{id, status, risk_status, risk_category, top1_risk, section, location, image_path, evidence, recheck_suggestion, created_at}]` |
| GET | `/{hazard_id}` | 隐患详情 | — | `{id, status, risk_status, risk_category, top1_risk, evidence, uncertainty, recheck_suggestion, scene_related, image_path, rectification_image_path, section, location, remark, created_at, updated_at}` |
| DELETE | `/{hazard_id}` | 删除隐患 | — | `{detail, hazard_id}` |
| POST | `/{hazard_id}/recheck` | 复查 | `RecheckRequest{passed: bool, remark?}` | `{hazard_id, status, passed, idempotent_replay?}` |
| POST | `/{hazard_id}/rectify-image` | 上传整改影像 | multipart: `image` | `{hazard_id, rectification_image, status}` |
| POST | `/{hazard_id}/rectify-media` | 关联已上传媒体作为整改影像 | `RectifyMediaRequest{image_id}` | `{id, status, risk_status, ..., rectification_image_path}` |
| POST | `/{hazard_id}/create-rectification-doc` | 自动生成整改通知单 | query: `deadline?` | `{doc_id, hazard_id, doc_status, hazard_status, idempotent_replay?}` |
| POST | `/{hazard_id}/generate-rectification` | 同上（别名） | query: `deadline?` | `{doc_id, hazard_id, doc_status, hazard_status}` |

---

### 4.3 document (`/api/document`) — 11 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/create` | 创建文书 | `DocCreateRequest{doc_type:DocType, title, content:dict, section?, location?, weather?, attached_images?}` | `{doc_id, status, doc_number}` |
| GET | `/list` | 文书列表 | query: `doc_type?`, `status?` | `[{id, doc_type, title, status, section, location, creator_id, pdf_path, created_at, updated_at}]` |
| GET | `/{doc_id}` | 文书详情 | — | `{id, doc_type, doc_number, title, status, content, section, location, weather, attached_images, pdf_path, created_at, updated_at}` |
| PUT | `/{doc_id}` | 更新文书 | `DocUpdateRequest{title?, content?, section?, location?, weather?, attached_images?}` | `{doc_id, status}` |
| POST | `/{doc_id}/submit` | 提交审批 | — | `{doc_id, status}` |
| POST | `/{doc_id}/approve` | 总监审批通过 | — | `{doc_id, status}` |
| POST | `/{doc_id}/reject` | 总监驳回 | `DocRejectRequest{reason}` | `{doc_id, status}` |
| GET | `/{doc_id}/export-pdf` | 导出PDF | — | `{doc_id, pdf_url, pdf_path}` |
| GET | `/{doc_id}/download-pdf` | 下载PDF | — | FileResponse |
| POST | `/generate-with-llm` | LLM生成文书 | `LLMDocGenerateRequest{doc_type, title, section?, location?, hazard_id?, extra_context?}` | `{doc_id, status, doc_number, llm_content, source}` |
| POST | `/generate-daily-log` | 自动生成监理日志 | query: `date: YYYY-MM-DD` | `{doc_id, doc_number, status, pdf_url, summary:{work_records, hazards, ...}}` |

**NEED_APPROVAL_TYPES:** `rectification`, `monthly`, `weekly`

---

### 4.4 knowledge (`/api/knowledge`) — 18 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/regulations/versions` | 版本列表 | query: `filename?`, `category?`, `active_only?` | `{total, versions: [{id, doc_name, filename, category, version, effective_date, expire_date, is_active, checksum, remark, created_at, updated_at}]}` |
| POST | `/regulations/versions` | 注册版本 | `VersionCreateRequest{filename, doc_name?, category?, version?, effective_date?, expire_date?, remark?}` | `{success, version_id, message}` |
| GET | `/regulations/versions/{filename}` | 版本历史 | — | `{filename, total, history: [{id, version, effective_date, expire_date, is_active, remark, created_at}]}` |
| POST | `/regulations/versions/rollback` | 版本回退 | `VersionRollbackRequest{version_id}` | `{success, message, version_id}` |
| POST | `/regulations/versions/sync` | 自动同步版本 | — | `{success, scanned, created, message}` |
| GET | `/regulations/search` | 规范检索(GET) | query: `keyword`, `category?`, `search_content?` | `{keyword, total, results}` |
| POST | `/regulations/search` | 规范检索(POST) | `SearchRequest{keyword, category?, search_content?}` | `{keyword, total, results}` |
| GET | `/regulations/categories` | 分类列表 | — | `{categories}` |
| GET | `/regulations/list` | 全部规范 | query: `category?` | `{total, results: [{name, filename, source_file, category, exists}]}` |
| GET | `/regulations/content` | 规范文本内容 | query: `filename`, `category?` | `{name, filename, category, content, exists}` |
| GET | `/regulations/download/{filename}` | 下载规范文件 | query: `category?` | FileResponse |
| POST | `/acceptance-guide` | 验收指引(POST+LLM) | `AcceptanceRequest{keyword}` | `{steps, key_points, common_violations, regulation_basis, source}` |
| GET | `/acceptance-guide` | 验收指引(GET) | query: `keyword` | guide dict |
| GET | `/doc-template/{doc_type}` | 文书模板 | — | template dict |
| POST | `/regulations/maintain` | 规范维护 | `RegulationMaintainRequest{action, name, category?, target_category?, file_path?}` | action-dependent |
| POST | `/regulations/import` | 导入规范 | multipart: `file`, `category?`, `name?` | `{success, name, filename, category, path, message}` |
| POST | `/regulations/import-batch` | 批量导入 | multipart: `files[]`, `category?` | `{success, total, success_count, failed_count, results, message}` |
| POST | `/llm-query` | LLM智能问答 | `LLMQueryRequest{question, use_llm?, knowledge_scope?}` | `{question, answer, context_results, source, knowledge_scope}` |

---

### 4.5 knowledge_base (`/api/kb`) — 22 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `` | 我的知识库列表 | — | `{items: [{id, name, description, kb_type, owner_id, role, is_owner, is_builtin, is_public, document_count, member_count, created_at, updated_at}]}` |
| POST | `` | 创建知识库 | `KnowledgeBaseCreate{name, description?, kb_type?, project_id?}` | `{id, name, description, kb_type, role, invite_code}` |
| GET | `/plaza` | 知识库广场 | query: `keyword?` | `{items: [{id, name, description, owner_id, owner_name, is_builtin, document_count, member_count, joined, my_role, updated_at}]}` |
| POST | `/{kb_id}/publish` | 发布到广场 | `PublishRequest{is_public}` | `{id, is_public}` |
| POST | `/{kb_id}/subscribe` | 订阅公开知识库 | — | `{detail, kb_id, role}` |
| GET | `/{kb_id}` | 知识库详情 | — | `{id, name, description, kb_type, owner_id, role, is_owner, is_builtin, is_public, invite_code, document_count, member_count, created_at}` |
| PUT | `/{kb_id}` | 更新知识库 | `KnowledgeBaseUpdate{name?, description?}` | `{id, name, description}` |
| DELETE | `/{kb_id}` | 删除知识库 | — | `{detail, id}` |
| GET | `/{kb_id}/members` | 成员列表 | — | `{items: [{user_id, role, real_name, username, project_id, joined_at}]}` |
| POST | `/{kb_id}/members` | 添加成员 | `MemberAdd{username?, user_id?, role?}` | `{detail, user_id, role}` |
| PUT | `/{kb_id}/members/{user_id}` | 修改成员角色 | `MemberRoleUpdate{role}` | `{user_id, role}` |
| DELETE | `/{kb_id}/members/{user_id}` | 移除成员 | — | `{detail, user_id}` |
| POST | `/{kb_id}/invite` | 获取邀请码 | — | `{invite_code}` |
| POST | `/join/{invite_code}` | 加入知识库 | — | `{detail, kb_id, role}` |
| GET | `/{kb_id}/documents` | 文档列表 | query: `keyword?` | `{total, items: [serialized doc]}` |
| POST | `/{kb_id}/documents` | 上传文档 | multipart: `file`, `name?` | serialized doc |
| POST | `/{kb_id}/documents/audio` | 上传音频转文字 | multipart: `file`, `name?`, `language?` | `{..., text, duration}` |
| DELETE | `/{kb_id}/documents/{doc_id}` | 删除文档 | — | `{detail, id}` |
| GET | `/{kb_id}/documents/{doc_id}/content` | 文档内容 | — | `{id, name, filename, content}` |
| GET | `/{kb_id}/documents/{doc_id}/download` | 下载文档 | — | FileResponse |
| GET | `/{kb_id}/search` | 搜索文档 | query: `keyword` | `{keyword, total, results: [{...doc, snippet, match}]}` |
| POST | `/{kb_id}/query` | 按库问答 | `QueryRequest{question, use_llm?}` | `{question, answer, citations, source}` |

**KB Roles (hierarchy):** owner(3) > admin(2) > editor(1) > viewer(0)
**Built-in KB:** open to all logged-in users as viewer; cannot be modified/deleted.

---

### 4.6 note (`/api/notes`) — 5 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `` | 笔记列表 | query: `keyword?` | `{total, items: [{id, title, source_kb_id, created_at, updated_at}]}` |
| POST | `` | 创建笔记 | `NoteCreate{title?, content?, source_kb_id?}` | `{id, title, content, source_kb_id, created_at, updated_at}` |
| GET | `/{note_id}` | 笔记详情 | — | full note with content |
| PUT | `/{note_id}` | 更新笔记 | `NoteUpdate{title?, content?}` | full note with content |
| DELETE | `/{note_id}` | 删除笔记 | — | `{detail, id}` |

---

### 4.7 ai_extras (`/api/ai`) — 3 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/task` | 任务模式（结构化报告） | `TaskRequest{prompt, kb_id?, save_note?}` | `{title, content, note_id, used_kb}` |
| POST | `/podcast` | 播客口播生成+语音合成 | `PodcastRequest{text?, note_id?, kb_id?, question?}` | `{script, audio_url, model, provider}` |
| POST | `/image` | AI生图（百炼文生图） | `ImageRequest{prompt, size?}` | `{image_url, prompt}` |

---

### 4.8 ledger (`/api/ledger`) — 10 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/types` | 台账类型列表 | — | `{types: {image: "影像台账", 旁站记录: "旁站台账", ...}}` |
| GET | `/list` | 查询台账 | query: `ledger_type` (required), `start_date?`, `end_date?`, `section?`, `keyword?`, `page?`, `page_size?` | `{ledger_type, ledger_name, total, page, page_size, records: [...]}` |
| GET | `/export` | 导出台账 | query: `ledger_type`, `format?` (excel/pdf) | `{ledger_type, ledger_name, format, filename, download_url, total}` |
| GET | `/download/{filename}` | 下载导出文件 | — | FileResponse |
| POST | `/batch-export` | 批量导出(多类型) | `BatchExportRequest{ledger_types, date_from?, date_to?, format?}` | StreamingResponse (zip or single file) |
| POST | `/batch-archive` | 批量归档 | `BatchArchiveRequest{ledger_types, date_from?, date_to?, archive_name, format?}` | `{detail, archive_id, files, zip_url, count}` |
| GET | `/archives` | 归档列表 | — | `{total, archives: [{id, name, file_path, ledger_types, date_from, date_to, created_by, created_at}]}` |
| GET | `/archives/{archive_id}/download` | 下载归档包 | — | FileResponse (zip) |
| DELETE | `/archives/{archive_id}` | 删除归档 | — | `{detail, archive_id}` |
| POST | `/batch-print` | 批量打印 | `BatchPrintRequest{ledger_types, date_from?, date_to?}` | `{detail, job_id, files, count}` |

**LEDGER_TYPES:** `image`(影像台账), `旁站记录`(旁站台账), `监理日志`(监理日志台账), `安全隐患整改通知单`(整改通知单台账), `hazard`(AI安全巡检台账), `监理周报`(周报台账), `监理月报`(月报台账), `见证取样记录`(见证取样台账)

---

### 4.9 watermark (`/api/watermark`) — 3 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/apply` | 图片水印 | multipart: `image`, `project_name?`, `supervisor?`, `section?`, `location?`, `weather?`, `hazard_flag?`, `lat?`, `lon?` | `{watermarked_url, project_name, supervisor, section, location, weather, hazard_flag, timestamp}` |
| POST | `/apply-stream` | 水印图片流(不保存) | multipart: `image` + watermark fields | StreamingResponse image/jpeg |
| POST | `/upload-photos` | 批量上传带水印照片 | multipart: `photos[]`, `module?` (default "inspection") | `{uploaded_count, photos: [{filename, url, module, timestamp}], module}` |

---

### 4.10 local_ai (`/api/local`) — 6 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/stt/transcribe` | 语音识别 | multipart: `file`, `language?` (default "zh") | STT result dict |
| POST | `/llm/chat` | LLM对话 | `LLMChatRequest{message, system_prompt?, temperature?}` | `{answer, model, source}` |
| GET | `/llm/status` | 模型状态 | — | `{configured, enabled, provider, current_model, model_available}` |
| POST | `/tts/synthesize` | 语音合成 | `TTSRequest{text}` | `{audio_url, model, provider}` |
| POST | `/voice-command` | 语音指令匹配 | `VoiceCommandRequest{text, module?, context?}` | `{action, params, reply, workflow, module, module_label, raw_text}` |
| GET | `/workflow-guide` | 工作流程指引 | query: `module?` (default "inspection") | `{module, module_label, workflow}` |

**Voice modules:** inspection, acceptance, station, witness, quality, other, hazard, rectify
**Voice actions:** capture_photo, start, end, query, guidance, switch_module, unknown

---

### 4.11 work_record (`/api/work-record`) — 26 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/create-with-photos` | 创建工作记录(含照片) | multipart: `record_type` (inspection/acceptance/witness/station/other/待定), `payload_json`, `photos[]` | `{detail, record_id, status, photo_urls, generated_doc_id}` |
| POST | `/inspection-with-photo` | 巡视+照片(兼容) | multipart: `payload_json`, `photos[]` | similar |
| POST | `/inspection` | 创建巡视记录 | `CreateInspectionRequest` | record |
| POST | `/inspection/{record_id}/photos` | 追加巡视照片 | multipart: `photos[]` | `{detail, photo_urls}` |
| POST | `/inspection/{record_id}/submit` | 提交巡视 | — | `{detail, status}` |
| POST | `/{record_id}/submit` | 提交记录 | — | `{detail, status}` |
| POST | `/{record_id}/approve` | 审批通过 | — | `{detail, status}` |
| POST | `/{record_id}/reject` | 驳回 | `RejectRequest{reason}` | `{detail, status}` |
| POST | `/{record_id}/archive` | 归档 | — | `{detail, status}` |
| POST | `/acceptance` | 创建验收 | `CreateAcceptanceRequest{guide_ref?}` → KG entity lookup → generates 省统表 | record + generated doc |
| POST | `/witness` | 创建见证 | `CreateWitnessRequest` | record |
| POST | `/witness/{record_id}/photos` | 追加见证照片 | multipart: `photos[]` | `{detail, photo_urls}` |
| POST | `/witness/{record_id}/report` | 生成见证报告 | — | report |
| POST | `/station` | 创建旁站 | `CreateStationRequest` | record |
| PUT | `/station/{record_id}` | 更新旁站 | `UpdateWorkRecordRequest` | record |
| GET | `/list` | 记录列表 | query params | `{items, total}` |
| GET | `/stats` | 统计 | — | stats dict |
| PUT | `/{record_id}/viewed` | 标记已读 | — | `{detail}` |
| GET | `/guides` | 验收指引列表 | — | guides |
| GET | `/{record_id}` | 记录详情 | — | full record |
| PUT | `/{record_id}` | 更新记录 | `UpdateWorkRecordRequest` | record |
| DELETE | `/{record_id}` | 删除记录 | — | `{detail}` |
| POST | `/other` | 创建其他记录 | `CreateOtherRequest` | record |
| POST | `/quality` | 创建质量记录 | `CreateQualityRequest` | record |
| POST | `/{record_id}/set-type` | 设置记录类型 | `UpdateRecordTypeRequest` | `{detail, record_type}` |
| POST | `/{record_id}/share` | 分享记录 | `ShareWorkRecordRequest` | `{detail}` |

**create-with-photos details:**
- `type_map`: inspection → 旁站; acceptance → 验收; witness → 见证; station → 旁站; other → 施工记录; 待定 → 施工记录
- `result_map`: pass → 合格; fail → 不合格; pending → 待整改
- Max 9 photos per request
- Response: `{detail, record_id, status, photo_urls, generated_doc_id}`

**SHARE_ROLE_GROUPS:**
- 监理部门: [专业监理工程师, 监理员, 总监代表]
- 总监: [总监理工程师]

---

### 4.12 task (`/api/task`) — 15 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `` | 派单 | `CreateTaskRequest{title, description?, priority?, category?, assignee_id, project_id?, deadline?, location?, section?, attachments?, notify_glasses?}` | `{detail, task_id}` |
| GET | `/assignable-users` | 可指派人列表 | — | `[{id, username, real_name, role, phone}]` |
| GET | `/my-tasks` | 我的任务 | query: `status?` | `{total, tasks: [task_dict], assignable_users}` |
| GET | `/assigned-by-me` | 我派发的任务 | query: `status?` | `{total, tasks: [task_dict]}` |
| GET | `/todo-stats` | 待办统计 | — | `{pending_receive, in_progress, completed, rejected, total_received, total_assigned?}` |
| GET | `/notifications` | 实时通知 | query: `since_ts?` | `{total, notifications}` |
| POST | `/notifications/clear` | 清空通知 | — | `{detail}` |
| GET | `/{task_id}` | 任务详情 | — | task_dict |
| POST | `/{task_id}/respond` | 接单/拒单 | `AcceptTaskRequest{action: "accept"\|"reject", reject_reason?}` | `{detail}` |
| POST | `/{task_id}/start` | 开始执行 | — | `{detail}` |
| POST | `/{task_id}/complete-with-files` | 完成(含附件) | multipart: `completion_remark?`, `result_files[]` | task_dict (via _finish_task) |
| POST | `/{task_id}/complete` | 完成 | `CompleteTaskRequest{completion_remark?, result_files?}` | task_dict |
| POST | `/{task_id}/reassign` | 转派 | `ReassignTaskRequest{new_assignee_id, reason?}` | `{detail, old_assignee_id}` |
| POST | `/{task_id}/urge` | 催办 | — | `{detail}` |
| POST | `/{task_id}/cancel` | 取消 | — | `{detail}` |

**task_dict:** `{id, title, description, status, priority, category, assigner_id, assignee_id, project_id, deadline, location, section, attachments, notify_glasses, accepted_at, started_at, completed_at, reject_reason, completion_remark, created_at, updated_at}`

**TaskCategory:** daily_inspection, rectification_tracking, material_inspection, acceptance_check, safety_patrol, quality_review, document_review, other
**TaskPriority:** 低, 中, 高, 紧急
**TaskStatus:** 待接单, 已接单, 已拒单, 进行中, 已完成, 已取消

---

### 4.13 knowledge_graph (`/api/kg`) — 9 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/entities` | 创建实体 | `EntityCreate{name, entity_type, description?, properties?, source_doc?}` | `{id, name, entity_type}` |
| GET | `/entities` | 实体列表 | query: `entity_type?`, `keyword?`, `limit?` | `[{id, name, entity_type, description, source_doc, created_at}]` |
| POST | `/entities/search` | 搜索实体 | `EntitySearchRequest{keyword, entity_type?, limit?}` | `[{id, name, entity_type, description}]` |
| DELETE | `/entities/{entity_id}` | 删除实体(级联) | — | `{detail}` |
| POST | `/relations` | 创建关系 | `RelationCreate{source_name, target_name, relation_type, weight?, description?}` | `{detail, source, target, relation}` |
| GET | `/graph/subgraph` | 子图遍历 | query: `entity_id`, `depth?` (default 2) | `{nodes: [{id, name, type, description}], edges: [{source, target, relation, weight}]}` |
| POST | `/extract` | LLM抽取实体关系 | `ExtractRequest{text, source_doc?}` | `{extracted_entities, extracted_relations, raw}` |
| GET | `/stats` | 图谱统计 | — | `{total_entities, total_relations, entity_types}` |
| POST | `/import-from-knowledge-base` | 从知识库批量导入 | — | `{total_files, extracted_entities, extracted_relations, skipped, errors}` |

**KG Entity Types:** 规范条款, 验收流程, 施工工序, 材料标准, 隐患类型, 工程部位, 监理要点
**KG Relation Types:** 属于, 前置, 后续, 关联, 应用于, 依据

---

### 4.14 project (`/api/project`) — 9 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/list` | 项目列表 | — | `list[ProjectInfo]` |
| POST | `/create` | 创建项目 | `CreateProjectRequest{name, code, short_name, location, description}` | `ProjectInfo` |
| POST | `/{project_id}/switch` | 切换项目 | — | `{detail, project_id}` |
| GET | `/drawings/list` | 图纸列表 | query: `keyword?`, `category?` | `{total, items: [{id, name, category, version, file_size, file_url, created_at}]}` |
| POST | `/drawings/upload` | 上传图纸 | multipart: `file`, `name?`, `category?`, `version?` | `{success, name, file_url}` |
| DELETE | `/drawings/{drawing_id}` | 删除图纸 | — | `{detail}` |
| GET | `/archives/list` | 档案列表 | query: `keyword?`, `archive_type?` | `{total, items: [{id, name, archive_type, archive_no, file_size, file_url, archived_at}]}` |
| POST | `/archives/upload` | 上传档案 | multipart: `file`, `name?`, `archive_type?`, `archive_no?` | `{success, name, file_url}` |
| DELETE | `/archives/{archive_id}` | 删除档案 | — | `{detail}` |

**ProjectInfo:** `{id, name, code, short_name, location, description}`

---

### 4.15 media (`/api/media`) — 8 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/images` | 上传图片 | multipart: `file`, `source?`, `business_type?`, `business_id?`, `section?`, `location?`, `weather?`, `unit_project?`, `work_content?`, `trigger_detection?`, `idempotency_key?` | `{id, media_type, url, ..., idempotent_replay, hazard_id, detection}` |
| POST | `/videos` | 上传视频 | multipart: `file`, `source?`, `business_type?`, `business_id?`, `duration_seconds?`, `section?`, `location?`, `weather?`, `idempotency_key?` | MediaResponse + idempotent_replay |
| POST | `/audio/transcribe` | 上传音频+转写 | multipart: `file`, `language?`, `source?`, `idempotency_key?` | MediaResponse + transcription + idempotent_replay |
| GET | `` | 媒体列表 | query: `media_type?`, `business_type?`, `business_id?`, `limit?` | `{items: [MediaResponse], total}` |
| POST | `/link` | 关联媒体到业务 | `LinkMediaRequest{media_ids, business_type, business_id}` | `{detail, media_ids, business_type, business_id}` |
| POST | `/{media_id}/detect` | 识别已上传图片 | `DetectMediaRequest{section?, location?, remark?}` | detection dict |
| POST | `/{media_id}/detect-and-save` | 识别并创建隐患 | `DetectMediaRequest{section?, location?, remark?}` | `_serialize_hazard(hazard)` |
| GET | `/{media_id}` | 媒体详情 | — | `{id, media_type, url, ..., duration_seconds, section, location, weather, metadata}` |

**MediaResponse:** `{id, media_type, url, original_url, file_hash, size_bytes, source, business_type, business_id, created_at}`
**Idempotency:** via `Idempotency-Key` header or `idempotency_key` form field
**Business types for link:** work_record, hazard, task

---

### 4.16 signature (`/api`) — 5 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/signatures/upload` | 上传签章 | multipart: `file` (PNG), `name`, `role?`, `password` | `SignatureResponse{id, name, role, image_path, is_active, created_at}` |
| GET | `/signatures/me` | 我的签章列表 | — | `list[SignatureResponse]` |
| DELETE | `/signatures/{signature_id}` | 删除签章 | `DeleteSignatureRequest{password}?` | `{detail}` |
| POST | `/documents/{doc_id}/sign` | 签署文书 | `SignDocumentRequest{signature_id, password, position: "bottom-right"\|"bottom-left"\|"top-right"\|"top-left"}` | `{doc_id, signed_pdf_url, signed_at, signed_by, signature_hash}` |
| GET | `/documents/{doc_id}/signed-pdf` | 下载已签章PDF | — | FileResponse |

---

### 4.17 inspection (`/api/inspection`) — 11 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/start` | 启动巡检 | `StartSessionRequest{project_id?, frequency_seconds?}` | `{session_id, status, frequency_seconds, started_at}` |
| POST | `/{session_id}/stop` | 停止巡检 | — | `{session_id, status, stopped_at, total_captures, total_hazards}` |
| GET | `/active` | 活动会话 | — | `{session: {id, project_id, status, frequency_seconds, started_at, total_captures, total_hazards, last_capture_at} \| null}` |
| GET | `/stats` | 看板统计 | — | `{today_inspection_count, today_hazard_count, total_inspection_count, total_hazard_count, active_session_count}` |
| GET | `/trend` | 趋势数据 | query: `days?` (default 7, max 90) | `[{date, inspection_count, hazard_count}]` |
| GET | `/category-distribution` | 隐患类别分布 | — | `[{category, name, count}]` |
| GET | `/recent` | 最近风险记录 | query: `limit?` (default 20, max 100) | `[{id, inspected_at, location, risk_status, risk_category, handle_status, hazard_status, image_url}]` |
| GET | `/{session_id}/stats` | 会话统计 | — | session stats |
| GET | `/history` | 历史会话 | query: `limit?` (default 50, max 200) | `{total, items: [{id, user_id, status, frequency_seconds, started_at, stopped_at, total_captures, total_hazards}]}` |
| POST | `/{session_id}/capture-media` | 关联已上传媒体 | `CaptureMediaRequest{image_id}` | `{id, session_id, project_id, status, total_captures, total_hazards, started_at, detection, hazard_id}` |
| POST | `/{session_id}/capture` | 上传图片采集 | multipart: `image`, `section?`, `location?`, `weather?` | `{session_id, total_captures, total_hazards, result, hazard_id, image_url, instruction}` |

---

### 4.18 ws (`/api/ws`) — 1 endpoint (WebSocket)

| Protocol | Path | Purpose | Auth | Behavior |
|---|---|---|---|---|
| WS | `/{user_id}` | WebSocket连接 | `?token=<JWT>` | Heartbeat ping 30s; timeout 90s; close 4408 on timeout, 4401 on auth fail, 4403 on user mismatch |

**Connection pool:** `Dict[int, Set[WebSocket]]` per user_id
**Project mapping:** `Dict[int, Set[int]]` project_id → user_ids
**Tools:** `broadcast_to_user(user_id, message)`, `broadcast_to_project(project_id, event_type, data)`
**Messages:** `{event: "connected"\|"ping"\|"pong", data: {...}}`
**Client types:** `{type: "ping"\|"pong"}`

---

### 4.19 acceptance_template (`/api/acceptance-templates`) — 6 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `` | 模板列表 | query: `acceptance_type?` | `{total, templates: [template_dict]}` |
| GET | `/types` | 验收类型枚举 | — | `{types: [...]}` |
| GET | `/{template_id}` | 模板详情 | — | template_dict |
| POST | `` | 创建模板 | `TemplateCreateRequest{acceptance_type, title, steps, key_points, common_violations, regulation_references, is_active?, remark?}` | `{detail, template}` |
| PUT | `/{template_id}` | 更新模板 | `TemplateUpdateRequest` (all optional) | `{detail, template}` |
| DELETE | `/{template_id}` | 删除模板 | — | `{detail}` |

**template_dict:** `{id, acceptance_type, title, steps, key_points, common_violations, regulation_references, is_active, created_by, remark, created_at, updated_at}`
**Delete behavior:** admin → hard delete; others → soft delete (is_active=False)

---

### 4.20 wechat_notice (`/api/wechat`) — 4 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/notice/send` | 发送服务通知 | `SendNoticeRequest{user_id, template_id, data: dict, page?}` | depends on service |
| POST | `/notice/subscribe` | 记录订阅 | `SubscribeRequest{template_id, openid?}` | `{detail}` |
| GET | `/notice/templates` | 模板ID列表 | — | `{templates: [...]}` |
| GET | `/notice/status` | 订阅状态 | query: `template_id?` | `{openid_bound, subscriptions: [{template_id, is_subscribed, subscribed_at}]}` |

---

### 4.21 omni (`/api/omni`) — 6 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/status` | 语音服务状态 | — | `{status: "ready"\|"not_configured", message, stt_model, llm_model}` |
| POST | `/load` | 加载模型(云模式noop) | — | `{message, status}` |
| POST | `/transcribe` | 语音识别 | multipart: `file`, `language?` | STT result dict |
| POST | `/stt` | 兼容接口 | multipart: `file` | STT result dict |
| POST | `/voice-chat` | 语音对话 | multipart: `file`, `context?` | `{text, transcript, understood, duration, model, provider}` |
| POST | `/tts` | TTS(未实现) | — | 501 |

**Mock mode:** `STT_MOCK_MODE=true` env var returns mock transcription text

---

### 4.22 model_settings (`/api/model-settings`) — 3 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `` | 当前配置 | — | `{items: [serialized configs]}` |
| PUT | `/{model_type}` | 更新配置 | `ModelConfigUpdate{provider?, base_url?, model_id?, api_key?, clear_api_key?, enabled?, options?}` | serialized config |
| POST | `/{model_type}/reset` | 恢复默认 | — | serialized config |

**model_type:** `llm` | `tts` | `stt` | `vlm`
**ModelConfigUpdate.options:** dict (no secrets/keys allowed)
**api_key:** max 4096 chars; Fernet-encrypted at rest

---

### 4.23 form_doc (`/api/form-docs`) — 5 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| GET | `/forms` | 表单模板列表 | — | `{groups: {group_name: [form_info]}, total}` |
| GET | `/record/{record_id}/forms` | 适用表单列表 | — | `{record_type, forms: [{form_code, form_name, filename, available}]}` |
| POST | `/generate` | 从记录生成文书 | `GenerateFromRecordRequest{record_id, form_code?, doc_number?, remark?}` | `{doc_id, title, form_code, form_name, doc_number, docx_url, status}` |
| POST | `/aggregate` | 聚合期报表 | `AggregateRequest{period: "day"\|"week"\|"month", date?, form_code?, use_llm?}` | aggregated result |
| POST | `/{doc_id}/export` | 导出docx/pdf | — | FileResponse |

**Form code mappings:**
- `gd_b_214_xunshi` → 巡视记录
- `gd_b_212_pangzhan` → 旁站记录
- `gd_b_213_pingxing` → 平行检查记录
- `gd_b_219_cailiao` → 见证取样记录
- `gd_b_220_rizhi` → 监理日志
- `gd_b_221_yuebao` → 监理月报
- `gd_b_223_zhoubao` → 监理周报
- `gd_b_215_tongzhi` → 监理通知单
- `gdaq_30308_yinhuan` → 安全隐患整改通知单

---

### 4.24 work_session (`/api/work-session`) — 1 endpoint

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/extract` | 语音文本AI提取 | `ExtractRequest{voice_text, force_type?}` | `{work_type, confidence, reasons, suggested_title, fields: {section, location, weather, work_content, ...}}` |

**work_type values:** 巡视, 验收, 旁站, 见证, 材料进场, 施工记录

---

### 4.25 simulator (`/api`) — 7 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| WS | `/ws/glasses-simulator` | 模拟器WebSocket | Auth: first msg `{type:"auth", token}` | Supports: key_event, photo_taken, status, voice_result |
| GET | `/simulator/status` | 连接状态 | — | `{connected, connection_count, connections: [{device_id, connected_at, status}]}` |
| POST | `/simulator/send-command` | 下发指令 | `SendCommandRequest{action, text?, data?}` | `{success, command, timestamp}` |
| POST | `/simulator/take-photo` | 触发拍照 | `PhotoRequest{width?, height?, quality?}` | `{success, message, timestamp}` |
| POST | `/simulator/start-audio` | 启动音频流 | — | `{success, message}` |
| POST | `/simulator/stop-audio` | 停止音频流 | — | `{success, message}` |
| POST | `/simulator/broadcast` | 广播指令(settings权限) | `SendCommandRequest` | `{success, command, timestamp}` |

**Supported actions:** speak, speak_urgent, show_hazard, show_guide, vibrate, start_inspection, stop_inspection, show_rectification, show_recheck, show_document, clear_display, take_photo, start_audio, stop_audio
**device_id:** `simulator_{n}`

---

### 4.26 miniprogram_auth (`/api/miniprogram/auth`) — 5 endpoints

| Method | Path | Purpose | Request | Response |
|---|---|---|---|---|
| POST | `/login` | 微信登录 | `WechatLoginRequest{code}` | If bound: `{bound: true, access_token, ...}` (TokenResponse); If unbound: `{bound: false, bind_token, expires_in: 600}` |
| POST | `/bind` | 绑定账号 | `BindAccountRequest{bind_token, username, password, role}` | TokenResponse |
| POST | `/register` | 注册+可选绑定 | `RegisterRequest{username, password, real_name, role, phone?, bind_token?}` | If with bind_token: TokenResponse; else `{bound: false, detail}` |
| POST | `/unbind` | 解绑 | `UnbindRequest{password}` | `{detail}` |
| GET | `/status` | 绑定状态 | — | `{bound, bound_at, last_login_at}` |

**Mock login:** code prefix `mock:` → mock openid (for dev)

---

## 5. Models & Enums Reference

### 5.1 Core Enums

| Enum | Values |
|---|---|
| **UserRole** | `总监理工程师`, `总监代表`, `专业监理工程师`, `监理员` |
| **DocType** | `旁站记录`, `监理日志`, `通知单(质量)`, `整改通知单`, `巡视记录`, `验收记录`, `见证取样记录`, `监理周报`, `监理月报` |
| **DocStatus** | `草稿`, `待审批`, `已审批`, `已驳回`, `已归档` |
| **HazardStatus** | `已提报`, `已通知整改`, `整改中`, `待复查`, `已闭环`, `驳回整改` |
| **TaskStatus** | `待接单`, `已接单`, `已拒单`, `进行中`, `已完成`, `已取消` |
| **TaskPriority** | `低`, `中`, `高`, `紧急` |
| **TaskCategory** | `日常巡查`, `整改跟踪`, `材料进场`, `验收检查`, `安全巡检`, `质量审查`, `资料审核`, `其他事务` |
| **WorkRecordType** | `巡视`, `验收`, `见证`, `旁站` |
| **WorkRecordStatus** | `草稿`, `进行中`, `已提交`, `已审批`, `已驳回`, `已归档` |
| **AcceptanceType** | `钢筋`, `混凝土`, `模板`, `脚手架`, `深基坑`, `防水`, `电气`, `安全帽`, `临边洞口`, `临时用电`, `其他` |
| **MediaType** | `image`, `video`, `audio` |
| **InspectionStatus** | `active`, `stopped` |
| **ModelType** | `llm`, `tts`, `stt`, `vlm` |
| **KG Entity Types** | `规范条款`, `验收流程`, `施工工序`, `材料标准`, `隐患类型`, `工程部位`, `监理要点` |
| **KG Relation Types** | `属于`, `前置`, `后续`, `关联`, `应用于`, `依据` |

### 5.2 Key Pydantic Models

| Model | Fields |
|---|---|
| **TokenResponse** | access_token, token_type, user_id, username, real_name, role, permissions, avatar_url, project_id, project_name |
| **LoginRequest** | username, password, role |
| **RegisterRequest** | username, password, real_name, role, phone?, invite_code, company_name? |
| **ChangePasswordRequest** | current_password, new_password (8-128 chars) |
| **CreateUserRequest** | username, password, real_name, role, phone?, project_id? |
| **UpdateUserRequest** | real_name?, role?, phone?, project_id?, is_active?, new_password? |
| **DocCreateRequest** | doc_type (DocType), title, content: dict, section?, location?, weather?, attached_images? |
| **DocUpdateRequest** | title?, content?, section?, location?, weather?, attached_images? |
| **LLMDocGenerateRequest** | doc_type, title, section?, location?, hazard_id?, extra_context? |
| **DocRejectRequest** | reason |
| **HazardResult** | risk_status, risk_category, top1_risk, evidence, uncertainty, recheck_suggestion, has_hazard, scene_related, matched_regulations |
| **RecheckRequest** | passed: bool, remark? |
| **RectifyMediaRequest** | image_id |
| **CreateTaskRequest** | title, description?, priority?, category?, assignee_id, project_id?, deadline?, location?, section?, attachments?, notify_glasses? |
| **AcceptTaskRequest** | action: "accept"\|"reject", reject_reason? |
| **CompleteTaskRequest** | completion_remark?, result_files? |
| **ReassignTaskRequest** | new_assignee_id, reason? |
| **VersionCreateRequest** | filename, doc_name?, category?, version?, effective_date?, expire_date?, remark? |
| **LLMQueryRequest** | question (1-2000), use_llm?, knowledge_scope? |
| **SearchRequest** | keyword, category?, search_content? |
| **AcceptanceRequest** | keyword |
| **KnowledgeBaseCreate** | name (1-128), description? (max 2000), kb_type? ("personal"\|"shared"), project_id? |
| **KnowledgeBaseUpdate** | name?, description? |
| **MemberAdd** | username?, user_id?, role? ("viewer"\|"editor"\|"admin") |
| **QueryRequest** | question (1-2000), use_llm? |
| **EntityCreate** | name, entity_type, description?, properties?, source_doc? |
| **RelationCreate** | source_name, target_name, relation_type, weight?, description? |
| **EntitySearchRequest** | keyword, entity_type?, limit? |
| **ExtractRequest** | text, source_doc? |
| **StartSessionRequest** | project_id?, frequency_seconds? (default 5) |
| **CaptureMediaRequest** | image_id |
| **MediaResponse** | id, media_type, url, original_url, file_hash, size_bytes, source, business_type, business_id, created_at |
| **DetectMediaRequest** | save?, section?, location?, remark? |
| **LinkMediaRequest** | media_ids, business_type, business_id |
| **CreateProjectRequest** | name, code, short_name, location, description |
| **ProjectInfo** | id, name, code, short_name, location, description |
| **SignatureResponse** | id, name, role, image_path, is_active, created_at |
| **SignDocumentRequest** | signature_id, password, position (bottom-right\|bottom-left\|top-right\|top-left) |
| **DeleteSignatureRequest** | password |
| **ModelConfigUpdate** | provider?, base_url?, model_id?, api_key? (max 4096), clear_api_key?, enabled?, options? |
| **NoteCreate** | title? (max 256), content?, source_kb_id? |
| **NoteUpdate** | title?, content? |
| **TemplateCreateRequest** | acceptance_type, title (1-128), steps, key_points, common_violations, regulation_references, is_active?, remark? |
| **TemplateUpdateRequest** | all fields optional |
| **GenerateFromRecordRequest** | record_id, form_code?, doc_number?, remark? |
| **AggregateRequest** | period: "day"\|"week"\|"month", date?, form_code?, use_llm? |
| **TaskRequest** | prompt (1-4000), kb_id?, save_note? |
| **PodcastRequest** | text? (max 8000), note_id?, kb_id?, question? |
| **ImageRequest** | prompt (1-1000), size? (default "1024*1024") |
| **LLMChatRequest** | message, system_prompt?, temperature? (default 0.7) |
| **TTSRequest** | text |
| **VoiceCommandRequest** | text, module? ("auto"), context? |
| **ExtractRequest (work_session)** | voice_text, force_type? |
| **ExtractRequest (KG)** | text, source_doc? |
| **SendCommandRequest** | action, text?, data? |
| **PhotoRequest** | width? (1920), height? (1080), quality? (90) |
| **SendNoticeRequest** | user_id, template_id, data: dict, page? |
| **SubscribeRequest** | template_id, openid? |
| **WechatLoginRequest** | code |
| **BindAccountRequest** | bind_token, username, password, role |
| **UnbindRequest** | password |
| **BatchExportRequest** | ledger_types, date_from?, date_to?, format? |
| **BatchArchiveRequest** | ledger_types, date_from?, date_to?, archive_name, format? |
| **BatchPrintRequest** | ledger_types, date_from?, date_to? |
| **ShareWorkRecordRequest** | (share config) |

### 5.3 Work Record Request Models (all Optional fields)

| Model | Key Fields |
|---|---|
| **CreateInspectionRequest** | work_content, section, location, weather, inspection_remark, inspection_photos |
| **CreateAcceptanceRequest** | acceptance_guide_ref, acceptance_type, acceptance_content, acceptance_result, section, location |
| **CreateWitnessRequest** | witness_material_name, witness_material_spec, witness_photos, section, location |
| **CreateStationRequest** | station_project_name, station_location, station_content, station_start_time, station_end_time, station_conclusion |
| **CreateOtherRequest** | work_content, section, location, remark |
| **CreateQualityRequest** | quality_type, quality_content, section, location |
| **UpdateRecordTypeRequest** | record_type |
| **UpdateWorkRecordRequest** | record_type, work_content, section, location, weather, inspection_remark, acceptance_type, acceptance_content, acceptance_result, witness_material_name, witness_material_spec, station_project_name, station_location, station_content, station_start_time, station_end_time, station_conclusion, remark |
| **RejectRequest** | reason |
