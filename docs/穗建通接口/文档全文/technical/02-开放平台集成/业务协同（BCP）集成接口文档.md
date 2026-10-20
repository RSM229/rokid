# 穗建通开放平台 · 业务协同（BCP）第三方集成接口文档

> 版本：v2.5（对外发布版）  
> 更新日期：2026-09-07  
> v2.5 变更：**补载 FSM 实例状态查询接口**（§11.6，`GET /bcp/open/{scene}/fsm-instance`，scope `bcp:fsm:read`）——按 biz_key 查当前状态与迁移历史，为状态对账唯一权威通道；§6.1 纪律 3 原"接口 4 的 fsm-instance 查询"悬空引用落锚至 §11.6  
> v2.4 变更：**签发期应用绑定（app_code）**——`client_credentials` 签发令牌可携扩展参数 `app_code`，令牌 claims 锚定应用身份，后续调用免带 `X-BCP-App-Code` 头；头部与令牌绑定不一致 → 401 `BCP-AUTH-4091` 明拒（消灭凭证混用/头部丢失导致的静默错配，§三「签发期应用绑定」）  
> v2.3 变更：①**信封坐标域/数据域分离**——FSM 技术坐标（instance_id/event 令牌/lifecycle 序号列/model_type）归并根节点 `fsm` 组，payload 仅留业务语义字段（平台坐标键不再混入，§6.2）；②`scope` 新增触发方坐标 `owner_project_code` 与**最终目的地坐标 `destination`**（订阅方免拼装判定规则，§6.3）；③`scope.target` 补齐接收方企业码 `enterprise_code`；④新增**集成参数体系总览**（§6.1，全坐标语义与取值源）  
> v2.2 变更（2026-08-28）：新增「接口 4：保障性接口（对账与运维）」（§十一）；查询接口时间参数兼容空格格式；`client_request_id` 同键不同参拒绝 `BCP-EXT-8051`；动作流水落请求载荷快照与参数指纹；**biz_key 主权归调用方**（v2.1 mdm 三段式派生作废——平台不重写不转译，行定位只读末段，存量归一键实例平台侧自动自愈改写，§七）  
> 适用对象：以协同角色（项目部/监理/检测/产权/安拆/使用/建设/监管等）接入业务协同场景的第三方应用研发  
> 配套资料：《01-业务协同（BCP）集成开发操作指南》（集成模型、webhook 接收端代码范式、联调清单）、《03-业务协同（BCP）保障性接口操作指南》（对账与运维操作范式）

---

## 目录

1. [接入总览](#一接入总览)
2. [通用约定](#二通用约定)
3. [接口 1：获取访问令牌](#三接口-1获取访问令牌)
4. [接口 2：查询场景目录 catalog](#四接口-2查询场景目录-catalog)
5. [接口 3：触发业务动作](#五接口-3触发业务动作)
6. [webhook：接收业务事件](#六webhook接收业务事件)
7. [业务对象主键 biz_key 取值表](#七业务对象主键-biz_key-取值表)
8. [错误码与业务拒绝处理](#八错误码与业务拒绝处理)
9. [附录 A：各场景动作与事件目录](#九附录-a各场景动作与事件目录)
10. [附录 B：扩展接口（按需接入）](#十附录-b扩展接口按需接入)
11. [接口 4：保障性接口（对账与运维）](#十一接口-4保障性接口对账与运维)

---

## 一、接入总览

第三方系统与平台的全部交互只有三类：

| # | 方向 | 接口 | 用途 |
|---|------|------|------|
| 1 | 你 → 平台 | `POST /bcp/open/{scene}/actions/{action_code}` | 触发业务动作，推动流程 |
| 2 | 你 → 平台 | `GET /bcp/open/{scene}/catalog` | 目录自描述（可触发动作/可订阅事件/写范围） |
| 3 | 平台 → 你 | 平台 POST 你的 webhook 地址 | 实时接收业务事件 |

> **接口边界**：业务协同接口只承担「动作触发 / 目录自描述 / 事件推送」三件事。业务对象状态查询、主数据与表单的读取写入，统一由**数据协同接口**提供（见数据协同接入文档）——查询类需求不要找本文档。

**三条集成纪律**：

1. **业务语言交互**：只使用动作码/业务事件码（如 `rectify.reply` / `rectify.replied`），平台内部技术标识无需也不应出现在你的报文中；
2. **授权即目录**：你能触发什么动作、订阅什么事件，以 catalog 接口返回为准（按你的协同角色过滤），不要凭文档硬编码；
3. **幂等三件套**：动作 `client_request_id`、事件 `event_id`——重试/重投不重复执行、不重复消费。

---

## 二、通用约定

### 2.1 请求基址

| 环境 | 基础地址（`{gateway}`） |
|------|------------------------|
| 测试环境 | `http://183.6.48.123:30284` |
| 生产环境 | `http://sjt.gz-szcj.com:8000/sjzt-center` |

### 2.2 通用请求头

| 请求头 | 必填 | 说明 |
|--------|------|------|
| `Authorization` | 是 | `Bearer {access_token}`，令牌获取见 §三 |
| `X-BCP-App-Code` | 多应用租户必填（签发未绑定时） | 应用编码（平台开通应用时颁发，形如 `APP-20260617-2391`）。同一软件公司租户开通多个业务协同应用时用于指定本次调用的应用身份；仅开通一个应用时可省略。**2026-09-02 起降级为兼容/一致性校验**：签发令牌时已携 `app_code` 完成应用绑定的，本头可省；若同时携带且与令牌绑定不一致 → 401 `BCP-AUTH-4091` 明拒 |
| `Content-Type` | POST 必填 | `application/json` |

### 2.3 通用响应信封

所有接口返回统一信封：

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | String | 成功固定 `"000000"`；失败为错误码（见 §八） |
| `message` | String | 结果描述 / 错误信息 |
| `data` | Object | 业务数据体（各接口结构见下文） |
| `trace_id` | String | 链路追踪号，报障凭据 |

**重要：业务校验拒绝不是 HTTP 错误。** 触发动作时若业务校验不通过（如单据缺引用编号），平台返回 HTTP 200 + `code="000000"` + `data.success=false` + `data.reject_reason`（规则编号）。处理方式见 §8.2。

### 2.4 幂等约定

| 场景 | 幂等键 | 重放表现 |
|------|--------|----------|
| 触发业务动作 | `client_request_id` | 返回首个结果，`idempotent_replay=true`，不重复执行 |
| 提交表单实例 | `doc_no`（单据编号） | 返回首个实例，`idempotent_replay=true` |
| 接收 webhook 事件 | `event_id` | 重复投递直接返回 200（接收端去重，建议保留 24h） |

> **同键必须同参**（v2.2 起平台强制）：`client_request_id` 已被成功执行过后，再次携带**相同幂等键但 biz_key/payload 不一致**的触发将被拒绝（`BCP-EXT-8051`，HTTP 409）——平台按参数指纹（scene+action+biz_key+payload 规范化 SHA-256）比对。新意图请换新的幂等键；参数修正重试（上次未成功）不受限。

---

## 三、接口 1：获取访问令牌

以应用凭证（client_id/client_secret，平台开通应用时颁发）换取访问令牌。

| 项 | 内容 |
|----|------|
| 地址 | `POST {gateway}/auth/oauth2/token` |
| 请求方式 | POST（query 参数 + Basic Auth 头） |
| 凭证携带 | `Authorization: Basic base64(client_id:client_secret)` |

**请求参数**（query）：

| 参数 | 必填 | 说明 |
|------|------|------|
| `grant_type` | 是 | 固定 `client_credentials` |
| `scope` | 是 | 固定 `server` |
| `app_code` | 推荐（2026-09-02 起） | 应用编码（形如 `APP-20260617-2391`）。**签发期应用绑定**：携带后令牌 claims 内锚定该应用身份，后续调用免带 `X-BCP-App-Code` 头；令牌绑定应用与头部声明不一致时请求将被明确拒绝（401 `BCP-AUTH-4091`）。不带则维持旧行为（多应用租户每次调用须带头消歧）——建议所有集成方在下次换发令牌时带上 |

**请求示例**：

```http
POST {gateway}/auth/oauth2/token?grant_type=client_credentials&scope=server
Authorization: Basic ODcyMDYxMjYwNTE3MTAzOTg2OnF0amIzNzM3YVQ4MVppNTgwMTdZdDdBRzRXVlkzVHZo
```

**响应示例**（统一信封，`code=0` 表示成功）：

```json
{
  "code": 0,
  "msg": "操作成功",
  "data": {
    "expireIn": 43200,
    "accessToken": {
      "tokenType": { "value": "Bearer" },
      "tokenValue": "872061260517103986::872061260517103986::87ef0132-05f2-44da-abde-de11923b045c",
      "expiresAt": "2026-08-20 03:06:33"
    }
  }
}
```

令牌取值路径：`data.accessToken.tokenValue`；有效期见 `data.expireIn`（秒，当前 43200=12h）。

**使用**：后续所有接口携带 `Authorization: Bearer {tokenValue}`。令牌过期后重新获取；建议缓存并在到期前刷新，不要每请求都换令牌。

### 签发期应用绑定（app_code，2026-09-02 起）

**适用场景**：平台凭证（client_id/client_secret）按软件公司租户颁发，同一租户可能开通多个业务协同应用（如同时是施工侧与监理侧系统）。旧模式下令牌只含租户身份，每次调用需用 `X-BCP-App-Code` 头指定"本次以哪个应用身份调用"——头部一旦丢失或与令牌凭证混用，平台只能按租户内唯一应用静默直选，**身份错配无任何提示**（实证事故：监理动作用施工通令牌触发被 8016 拒绝，双方排查困难）。

**绑定模式**：签发时携带 `app_code`，令牌内即锚定该应用身份，后续调用**免带头部**：

**请求示例**（带应用绑定）：

```http
POST {gateway}/auth/oauth2/token?grant_type=client_credentials&scope=server&app_code=APP-20260617-2391
Authorization: Basic base64(client_id:client_secret)
```

**绑定原理（透明告知）**：`app_code` 作为声明写入令牌 claims（`check_token` 可见）；每次调用时平台以令牌内声明为锚，并实时校验该应用确属令牌所属 client 的应用集——声明不属于自己的应用将在请求期被明确拒绝，令牌本身不构成越权通道。

**如何验证绑定生效**：令牌签发后可调内省端点自查 claims 中是否含 `app_code`：

```http
GET {gateway}/auth/token/check_token?token={tokenValue}
# 响应 claims 中应见 "app_code": "APP-20260617-2391"
```

**调用期行为矩阵**：

| 令牌是否绑定 app_code | 是否带 `X-BCP-App-Code` 头 | 平台行为 |
|---|---|---|
| 已绑定 | 不带 | ✅ 以令牌绑定应用身份执行（**推荐终态**） |
| 已绑定 | 带且一致 | ✅ 正常执行（头部仅作一致性校验） |
| 已绑定 | 带但**不一致** | ❌ 401 `BCP-AUTH-4091` 明拒（响应体含 token/header 双方值）——说明集成侧凭证混用，请排查 |
| 未绑定（存量令牌） | 带 | ✅ 旧消歧路径，行为不变 |
| 未绑定（存量令牌） | 不带 | 单应用租户：直选执行；多应用租户：拒绝（须带头）——维持旧行为 |

**集成方迁移建议**：

1. 下次令牌换发时在签发请求加 `app_code`（令牌默认 12h 过期，自然轮换即可，无需紧急变更）
2. **token 缓存按应用隔离**：多应用集成方务必为每个应用独立缓存令牌（缓存 key 含 app_code），杜绝"监理动作复用施工通令牌"
3. 绑定令牌后建议代码中**移除** `X-BCP-App-Code` 头设置，避免未来两边维护漂移产生不一致拒绝
4. 观察期内未绑定的存量调用方式完全兼容，不强制切换

> 业务协同相关 scope：`bcp:open:action`（触发动作）、`bcp:open:catalog`（查目录）、`bcp:fsm:read`（查状态）、`bcp:md:read`/`bcp:md:write`（主数据）、`bcp:form:read`/`bcp:form:write`（表单）。
> **scope 由平台服务端按你的应用开通数据（场景×角色）实时派生**，不随令牌携带——应用开通/收回后下一次调用即生效，无需重新换令牌。scope 不足时接口返回 403（`BCP-CMN-0003`），请核对应用的场景角色开通状态或联系平台。

---

## 四、接口 2：查询场景目录 catalog

开机自检接口——问平台"我在这个场景能做什么"。返回按**你的协同角色**过滤后的动作目录、可订阅事件目录、数据写范围。

| 项 | 内容 |
|----|------|
| 地址 | `GET {gateway}/bcp/open/{scene}/catalog` |
| 请求方式 | GET |
| 所需 scope | `bcp:open:catalog` |

**路径参数**：

| 参数 | 说明 |
|------|------|
| `scene` | 场景码，如 `safety-rectify-loop`（已发布场景见 §九） |

**请求示例**：

```http
GET {gateway}/bcp/open/safety-rectify-loop/catalog
Authorization: Bearer {access_token}
```

**响应参数**（`data`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `scene_code` | String | 场景码 |
| `roles` | Array | 你在该场景的协同角色集 |
| `actions[]` | Array | 可触发动作目录 |
| `actions[].action_code` | String | 动作码 |
| `actions[].biz_name` | String | 中文业务名 |
| `actions[].from_status` / `to_status` | String | 该动作绑定的状态迁移 |
| `actions[].payload_ref` | String | 载荷结构引用（如 `form:GDAQ30309`，表单结构经数据协同接口获取） |
| `events[]` | Array | 可订阅事件目录 |
| `events[].event_code` | String | 业务事件码 |
| `events[].biz_name` | String | 中文业务名 |
| `write_scope[]` | Array | 主数据/表单写范围：`grant_type`、`target_code`、`constraints` |

**响应示例**（项目部角色，安全隐患整改闭环）：

```json
{
  "code": "000000",
  "message": "success",
  "trace_id": "a1b2c3",
  "data": {
    "scene_code": "safety-rectify-loop",
    "roles": ["project_dept"],
    "actions": [
      {
        "action_code": "rectify.reply",
        "biz_name": "整改回复提交",
        "from_status": "RECTIFYING",
        "to_status": "REVIEW",
        "payload_ref": "form:GDAQ30309"
      },
      {
        "action_code": "resume.apply",
        "biz_name": "复工申请提交",
        "from_status": "ESCALATED",
        "to_status": "RESUME_REVIEW",
        "payload_ref": "form:GDAQ30311"
      }
    ],
    "events": [
      { "event_code": "rectify.notified", "biz_name": "整改通知签发" },
      { "event_code": "rectify.closed",   "biz_name": "复查合格销号" },
      { "event_code": "rectify.returned", "biz_name": "复查不合格退回" },
      { "event_code": "resume.approved",  "biz_name": "复工批复签认" }
    ],
    "write_scope": [
      { "grant_type": "form", "target_code": "GDAQ30309",
        "constraints": { "editable_states": ["DRAFT"], "scope": "creator" } }
    ]
  }
}
```

**开发动作**：

- catalog 自检接入应用启动流程——本地动作码/事件码清单与目录不一致即告警（场景升版早知道）；
- 触发动作前看 `from_status`：业务对象当前状态不在 from 侧，调用必然被拒——对照 catalog 的 `from_status` 核校本地记录后再触发，省一次往返。

---

## 五、接口 3：触发业务动作

**核心接口。** 用业务动作推动流程（如"整改回复提交"），平台完成业务裁决并派生业务事件。

| 项 | 内容 |
|----|------|
| 地址 | `POST {gateway}/bcp/open/{scene}/actions/{action_code}` |
| 请求方式 | POST |
| 所需 scope | `bcp:open:action` |

**路径参数**：

| 参数 | 说明 |
|------|------|
| `scene` | 场景码 |
| `action_code` | 动作码（取自 catalog，如 `rectify.reply`） |

**请求体参数**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `biz_key` | String | 是 | 业务对象主键（各场景取值字段见 §七，如整改单号 `ZG-2026-0188`） |
| `client_request_id` | String | 是 | 稳定幂等键。建议由 `biz_key + 动作码` 派生（如 `ZG-2026-0188-rectify.reply`），网络重试/双击/重发必须用同一个键 |
| `payload` | Object | 按动作 | 业务载荷，结构以 catalog 中该动作的 `payload_ref` 关联表单为准 |

**请求示例**：

```http
POST {gateway}/bcp/open/safety-rectify-loop/actions/rectify.reply
Authorization: Bearer {access_token}
Content-Type: application/json

{
  "biz_key": "ZG-2026-0188",
  "client_request_id": "ZG-2026-0188-rectify.reply-01",
  "payload": {
    "reply_doc_no": "GDAQ30309-2026-0117",
    "ref_notice_no": "GDAQ30308-2026-0042",
    "ref_notice_date": "2026-08-01"
  }
}
```

**响应参数**（`data`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `action_code` / `biz_name` | String | 动作码 / 中文业务名 |
| `biz_key` | String | 业务对象主键 |
| `from_status` | String | 迁移前状态 |
| `to_status` | String | 迁移后状态（拒绝时为 `null`） |
| `derived_event_code` | String | 派生的业务事件码（订阅端将收到该事件） |
| `success` | Boolean | 业务裁决结果 |
| `reject_reason` | String | 拒绝原因（规则编号+描述），成功时为 `null` |
| `idempotent_replay` | Boolean | `true` 表示本次为幂等重放，返回的是首个结果 |
| `role` | String | 平台识别出的你的协同角色 |
| `mode` | String | **仅直发动作输出**，固定 `direct_emit`（v2.1 新增）。直发 = 该动作不走状态机（无状态迁移），平台直接派生业务事件——此时 `from_status`/`to_status` 固定为 `null`；无此字段即为状态机迁移动作（from/to 如实返回） |

**响应示例 · 成功**：

```json
{
  "code": "000000",
  "trace_id": "f7e8d9",
  "data": {
    "action_code": "rectify.reply",
    "biz_name": "整改回复提交",
    "biz_key": "ZG-2026-0188",
    "from_status": "RECTIFYING",
    "to_status": "REVIEW",
    "derived_event_code": "rectify.replied",
    "success": true,
    "reject_reason": null,
    "idempotent_replay": false,
    "role": "project_dept"
  }
}
```

**响应示例 · 业务校验拒绝**（HTTP 200）：

```json
{
  "code": "000000",
  "trace_id": "c4d5e6",
  "data": {
    "action_code": "rectify.reply",
    "biz_key": "ZG-2026-0188",
    "from_status": "RECTIFYING",
    "to_status": null,
    "success": false,
    "reject_reason": "GUARD[r_esc_ref]: 回复单未引用通知单编号+日期",
    "idempotent_replay": false
  }
}
```

**响应示例 · 幂等重放**（同 `client_request_id` 重复提交）：

```json
{
  "code": "000000",
  "data": {
    "action_code": "rectify.reply", "biz_key": "ZG-2026-0188",
    "from_status": "RECTIFYING", "to_status": "REVIEW",
    "success": true, "idempotent_replay": true
  }
}
```

**响应示例 · 直发动作成功**（v2.1：`mode=direct_emit`，无状态迁移 from/to 固定 null）：

```json
{
  "code": "000000",
  "trace_id": "a9b8c7",
  "data": {
    "action_code": "form.send_to_supervisor",
    "biz_name": "表单发送到监理",
    "biz_key": "PRJ202608000052:RECTIFY_NOTICE:001",
    "from_status": null,
    "to_status": null,
    "derived_event_code": "form.sent_to_supervisor",
    "success": true,
    "reject_reason": null,
    "idempotent_replay": false,
    "mode": "direct_emit",
    "role": "project_dept"
  }
}
```

**调用要点**：

- 对未注册的 `biz_key` 触发动作返回错误码 `BCP-EXT-8017`（业务对象不存在）——新业务的首次登记动作（如产权备案申报 `device.file`、危大识别登记）由平台创建业务对象，之后的动作用既有编号；
- 每次调用的触发结果可在门户「业务协同跟踪日志 → 发布日志」自查；
- 拒绝处理：解析 `reject_reason` 规则编号 → 补正数据 → **同幂等键重提**，不要换键重试风暴。

---

## 六、webhook：接收业务事件

平台在动作裁决后派生业务事件，经「订阅 ∩ 授权范围」双重过滤后 POST 到你的 webhook 地址——**项目 A 的事件不会投给只有项目 B 授权的应用**。

### 6.1 投递请求头

| 请求头 | 说明 |
|--------|------|
| `X-BCP-Event-Id` | 事件幂等键（消费端去重） |
| `X-BCP-Topic` | 事件主题，如 `safety-rectify-loop.lifecycle.status.changed` |
| `X-BCP-Scene` | 场景码 |
| `X-BCP-Timestamp` | 毫秒时间戳 |
| `X-BCP-Delivery-Id` | 投递编号（排障凭据） |
| `X-BCP-Signature` | 签名：`hex( HmacSHA256(secret, timestamp + "." + body) )`，body 为原始报文 UTF-8 字节 |

### 6.1 集成参数体系总览（v2.3 新增——先读这张表，90% 的取值疑问在此有答案）

事件信封按**坐标域/数据域**严格分离。全部坐标按"谁的钱谁的名"分四组，每组语义与取值源如下：

| 坐标组 | 位置 | 字段 | 语义（你该怎么用） |
|---|---|---|---|
| **事件身份组** | 信封根 | `event_id` / `event_type` / `event_code` / `topic` / `occurred_at` / `seq` / `scene_code` / `scene_version` | 判别"发生了什么"。业务判别**只用 `event_code`**（派生业务事件码，全事件类型统一）；`seq` 用于同 biz_key 有序消费（直发事件无此键） |
| **触发方坐标组** | `scope` 根 | `project_code` / `enterprise_code` | **事件归属**项目/企业（FSM=实例归属；直发=触发方解析）——是"事件关于谁的对象"，**不是**触发方身份 |
| 〃 | `scope` 根 | `role` | 触发角色码（谁触发的，如 `project_dept`） |
| 〃 | `scope` 根 | `owner_project_code` | **触发方属主项目码**（装配前快照，载体改写够不着）——"数据是谁生产的"。实例归属≠数据属主的场景（监理发通知、施工方回复）靠它区分 |
| **接收方坐标组** | `scope` | `destination` | **最终目的地坐标（开箱即用）**：`{type: project\|enterprise, code}`——平台判定"你该以哪个空间身份消费本事件"的规范单值。**做业务判断/回查数据协同一律用它，不要自行拼装** |
| 〃 | `scope.target` | `project_code[]` | 订阅匹配命中的你方授权项目码**全集**（恒为数组；`destination.code` 即其首元素） |
| 〃 | `scope.target` | `enterprise_code` | 你方（订阅方）企业码 |
| 〃 | `scope.target` | `posts[]` | **承接岗位集（v2.4 新增，hint 非门禁）**：动作承接角色 ∩ 你方企业类型解析出的岗位名快照（如 `["项目经理","安全负责人"]`）——"这个事件在你们企业内该由哪些岗位承接"的参考坐标。可用于你方应用内二次路由（企业内按岗位到人）；平台投递判定永远不看它，无岗位映射时省略该键 |
| 〃 | `scope.target` | `gcbm` / `project_type` 等 | 触发侧投送目标预算（工程编码/项目类型等业务条件） |
| **FSM 技术组** | 信封根 `fsm` | `instance_id` / `model_type` / `lifecycle_instance_no` / `lifecycle_seq` / `event` | 状态机技术坐标（**仅迁移事件出现**；`event` 为 FSM 迁移令牌，业务判别不用它——用根 `event_code`）。一般无需消费，排障/对账备用 |
| **业务数据域** | `payload` | `biz_key` / `from_state` / `to_state` / `resources` / `body_entity_type` / `body_entity_code` / 装配业务字段 | **只有业务数据**：`biz_key` 回查锚点；`from/to_state` 迁移事实；`resources` 资源索引（协同数据权限回查依据）；`body_entity_*` 实体坐标（见 §6.2 末表）。**平台坐标键（project_id/enterprise_id/project_code/enterprise_code/owner_project_code）一律不出现**——它们在上四组 |

**三条使用纪律**：
1. **业务判断用 `destination`**："我作为订阅方的协同范围值"=`destination.code`（type=project 为项目码，enterprise 为企业码）。它与平台为你登记的协同数据权限空间**严格一致**——拿它做数据协同查询坐标必然命中授权；
2. **判别事件类型用 `event_code`**，不要解析 `event_type`（两态）或 `fsm.event`（内部令牌）；
3. **回查数据用 `payload.biz_key` / `resources`**，状态对账走「接口 4」的 FSM 实例状态查询（§11.6），禁止据事件本地推断状态。

### 6.2 事件信封（所有事件同构，场景升版不变）

| 字段 | 说明 |
|------|------|
| `event_id` | 事件 ID（幂等键） |
| `event_type` | 事件类型，两态：**状态机迁移事件** = `status.changed`；**直发派生事件** = 业务事件码（如 `form.sent_to_supervisor`）。判别业务事件一律以 `event_code` 为准 |
| `event_code` | **派生业务事件码**（全事件类型）：直发/md 族与 `payload.event_code` 同源；FSM 族由平台按场景+迁移事件反查动作目录得出（如 `rectify.notified`） |
| `topic` | 主题 |
| `trace_id` / `seq` | 链路号 / 序号（同一 biz_key 按 seq 有序；**直发事件无 `seq` 键**） |
| `occurred_at` | 发生时间 |
| `scene_code` / `scene_version` | 场景码 / 场景版本（直发事件无 `scene_version` 键） |
| `fsm` | **FSM 技术坐标组（v2.3 归并，仅迁移事件出现）**：`instance_id`（实例 ID）/ `model_type`（模型类型）/ `lifecycle_instance_no`（生命周期周期号）/ `lifecycle_seq`（迁移序号，与根 `seq` 同值）/ `event`（FSM 迁移令牌——内部词汇，业务判别请用根 `event_code`） |
| `scope` | 数据范围（见 §6.3 坐标组明细） |
| `payload` | **纯业务数据域（v2.3 起）**：`biz_key`、`from_state`/`to_state`（FSM 事件）、`resources`、`body_entity_*`、`model_type`（直发事件）、装配业务字段。**平台坐标键（project_id/enterprise_id/project_code/enterprise_code/owner_project_code/instance_id/event/lifecycle_*）一律不出现**——已分别归入 `fsm`/`scope` 两组（`model_type` 在迁移事件中归入 `fsm` 组，直发事件保留在 payload） |

### 6.3 scope 坐标组明细（逐订阅方渲染）

| 位置 | 字段 | 说明 |
|------|------|------|
| scope 根 | `version` | scope 版本（项目协同=2） |
| scope 根 | `project_code` | 事件归属项目码（发送侧坐标）。**不输出 `projectId`** |
| scope 根 | `enterprise_code` | 发送方企业码。**不输出 `enterpriseId`** |
| scope 根 | `role` | 触发角色码（触发方坐标组） |
| scope 根 | `owner_project_code` | **触发方属主项目码**（v2.3 新增）：触发方在装配链之前快照的声明坐标，载体装配改写不影响；实例归属≠数据属主场景（如监理发通知/施工方回复）的正确属主 |
| scope 根 | `destination` | **最终目的地坐标（v2.3 新增，开箱即用）**：`{"type": "project", "code": "PRJ…"}` 或 `{"type": "enterprise", "code": "ENT…"}`——取值=`target.project_code` 首元素（订阅匹配命中，字面命中优先），无命中回退接收方企业码。**"我作为订阅方的协同范围值"读它即可** |
| `scope.target` | `project_code` | 订阅匹配命中的你方授权项目码全集（字面命中 ∪ gcbm 等价命中），**恒为数组**；企业级授权无法枚举时省略该键 |
| `scope.target` | `enterprise_code` | 你方（订阅方）企业码（v2.3 新增） |
| `scope.target` | `posts` | 承接岗位集（v2.4 新增，数组，hint 非门禁）：你方企业内承接岗位参考（你方应用内二次路由用；平台投递不以此过滤，无映射时省略） |
| `scope.target` | `gcbm` / `project_type` 等 | 触发侧投送目标预算原样 |

> 坐标解析落空（主档缺失）时对应键省略，不回退输出 id；scope 根不出现 `gcbm`（工程编码见 `target.gcbm`）。
> 你收到的必然是你有权接收的事件（平台已完成授权判定）；`destination.code` 与平台为你登记的协同数据权限空间严格一致。

**投递示例 · 状态机迁移事件**：

```http
POST {你的 webhook 地址}
Content-Type: application/json
X-BCP-Event-Id: 9f3c8a
X-BCP-Topic: safety-rectify-loop.lifecycle.status.changed
X-BCP-Scene: safety-rectify-loop
X-BCP-Timestamp: 1786000800050
X-BCP-Delivery-Id: evt-1024-9f3c8a
X-BCP-Signature: 601e56…

{
  "event_id": "9f3c8a",
  "event_type": "status.changed",
  "event_code": "rectify.replied",
  "topic": "safety-rectify-loop.lifecycle.status.changed",
  "trace_id": "abc123",
  "seq": 128,
  "occurred_at": "2026-08-06T10:00:00+08:00",
  "scene_code": "safety-rectify-loop",
  "scene_version": "v2021",
  "fsm": {
    "instance_id": 1024,
    "model_type": "doc",
    "lifecycle_instance_no": 1,
    "lifecycle_seq": 128,
    "event": "T3_REPLY"
  },
  "scope": {
    "version": 2,
    "project_code": "PRJ202608000052",
    "enterprise_code": "ENT202608000053",
    "role": "project_dept",
    "owner_project_code": "PRJ202608000050",
    "destination": { "type": "project", "code": "PRJ202608000052" },
    "target": {
      "gcbm": "2411-440105-04-01-183590-2001",
      "project_type": "BUILDING_ENGINEERING",
      "project_code": ["PRJ202608000052"],
      "enterprise_code": "ENT202608000053"
    }
  },
  "payload": {
    "biz_key": "ZG-2026-0188",
    "from_state": "RECTIFYING",
    "to_state": "REVIEW",
    "body_entity_code": "ZG-2026-0188"
  }
}
```

**投递示例 · 直发派生事件**（`event_type` = 业务事件码；无 `seq`/`scene_version`/`fsm`；payload 无 from/to 与平台坐标键）：

```json
{
  "event_id": "3022b626-b8bb-424c-a05e-47605e2244bf",
  "event_code": "form.sent_to_supervisor",
  "topic": "standard-form-collab.lifecycle.status.changed",
  "tenant_id": 0,
  "trace_id": "",
  "occurred_at": "2026-08-24T15:14:41+08:00",
  "scene_code": "standard-form-collab",
  "scope": {
    "version": 2,
    "project_code": "PRJ202608000050",
    "enterprise_code": "ENT202608000053",
    "role": "project_dept",
    "owner_project_code": "PRJ202608000050",
    "destination": { "type": "project", "code": "PRJ202608000050" },
    "target": {
      "gcbm": "2411-440105-04-01-183590-2001",
      "project_type": "BUILDING_ENGINEERING",
      "project_code": ["PRJ202608000050", "PRJ202608000051"],
      "enterprise_code": "ENT202608000058"
    }
  },
  "payload": {
    "biz_key": "PRJ202608000050:RECTIFY_NOTICE:NT202606000002",
    "model_type": "doc",
    "body_entity_type": "RECTIFY_NOTICE",
    "body_entity_code": "NT202606000002"
  }
}
```

**事件 payload 实体坐标增补**（v2.1，2026-08-25 全模型 + FSM 族同规则）：**所有事件**（直发派生事件与状态机迁移事件）均按 biz_key 分段注入实体坐标，接收方免解析 biz_key 直取：

| biz_key 形态 | 注入字段 | 示例 |
|---|---|---|
| 三段式 `{首段}:{类型}:{编码}`（首段不限——项目码/场景码前缀均可） | `body_entity_type`=中段 + `body_entity_code`=尾段 | `PRJ202608000050:RECTIFY_NOTICE:NT202606000002` → `RECTIFY_NOTICE` / `NT202606000002` |
| 两段式 `{首段}:{编码}` | `body_entity_code`=第二段（无类型段，不注入 `body_entity_type`） | `PRJ202608000050:NT202606000002` → `NT202606000002` |
| 其他（无冒号自由格式） | 不注入 | — |

```json
"payload": {
  "biz_key": "PRJ202608000050:RECTIFY_NOTICE:NT202606000002",
  "model_type": "doc",
  "body_entity_type": "RECTIFY_NOTICE",
  "body_entity_code": "NT202606000002"
}
```

> 两键均按"可缺省"处理：`body_entity_type` 仅三段式出现；biz_key 无冒号时两键均不出现。直发派生事件与 FSM 迁移事件（from_state/to_state/instance_id 那套）同规则注入。

### 6.3 接收端三段式纪律：验签 → 幂等 → 200

1. **验签**：用平台颁发的 `secret` 对 `X-BCP-Timestamp + "." + 原始报文` 重算 HmacSHA256，与 `X-BCP-Signature` 比对。**必须基于原始报文字节**——先反序列化再拼接必然验签失败；失败返回 401；
2. **幂等**：`event_id` 去重（建议保留 24h），重复投递直接返回 200；
3. **消费后返回 200**：非 2xx / 超时平台将退避重试（最多 3 次，线性退避），连续失败进入补偿重放。

事件只用于刷新本地只读副本；需业务数据凭 `biz_key` 经数据协同接口回拉平台。完整 Java 代码范式见《01-集成开发操作指南》§6.2。投递结果可在门户「订阅日志」按 `event_id` / `delivery_id` 自查逐次重试时间线。

### 6.4 订阅范围

事件订阅随"场景×角色"开通**批量生效**（角色可订阅全集），无需逐主题登记。如需收敛，登记时用 `event_codes` 声明子集（必须是本角色可订阅集的子集，越界拒绝并返回可订清单）。

---

## 七、业务对象主键 biz_key 取值表

平台**不生成** biz_key——值取自业务制度既有编号。各场景取值字段：

| 场景码 | 场景名 | biz_key 取值 | 示例 |
|--------|--------|--------------|------|
| `crane-lifecycle` | 起重机械全生命周期管理 | 设备产权**备案编号** | `粤AG-备案-0001` |
| `safety-rectify-loop` | 安全隐患整改闭环 | **隐患编码**（hazard_code） | `HD202608000185` |
| `quality-rectify-loop` | 质量整改闭环 | 隐患编码（hazard_code） | `ZLH202609000031` |
| `danger-works` | 危大工程全周期协同 | 危大清单识别登记编号 | — |
| `concrete-order` | 混凝土订单协同 | 订单编号 `ddbm` / 生产批次号 `hntscpch` 等（按实体分取） | — |

**使用要点**：同一 biz_key 的事件按 `seq` 有序，顺序敏感业务按 biz_key 串行处理；同一 biz_key 可有多条生命周期（如设备转场后再进场），历史周期只读。

> **特殊形态 · mdm 协同实体动作**（v2.1 引入；**v2.2 修订：biz_key 主权归调用方**）：以协同实体（dwd 中台主数据）为主体的动作（`model_type=mdm`），biz_key **始终为你传入的原文**——平台不重写不转译（v2.1 曾派生为 `{scene}:{ENTITY_TYPE}:{实体编码}`，已作废：改写导致续办/查询/对账断链）；行定位平台只读你 biz_key 的**末段**（最后一个冒号之后）作为实体编码，也可经 `payload.resources` 显式声明 `mdm:{ENTITY_TYPE}|{编码}`。推荐二段式 `{项目码}:{业务单号}` 或三段式 `{项目码}:{实体类型}:{业务单号}`（三段式实体类型须与动作声明载体一致）。接收事件时实体坐标可直接取 `payload.body_entity_type` / `body_entity_code`（§6.2），无需解析 biz_key。

---

## 八、错误码与业务拒绝处理

### 8.1 错误码（HTTP 非 200 或 code 非 000000）

**通用处理原则**：5xx/429 一律指数退避重试（重试必须带**同一幂等键**）；4xx 不重试，按处理建议修正后再调；任何报障请携带响应信封中的 `trace_id`。

**通用段 BCP-CMN**：

| 错误码 | HTTP | 说明 | 处理建议 |
|--------|------|------|----------|
| BCP-CMN-0001 | 400 | 参数错误 | 按 `message` 核对请求体必填项与字段类型 |
| BCP-CMN-0002 | 401 | 未认证或凭证已失效 | 重新获取访问令牌（§三）后再调；建议令牌到期前刷新缓存 |
| BCP-CMN-0003 | 403 | 缺少授权 scope | scope 由平台按应用开通数据（场景×角色）实时派生——核对应用场景角色是否已开通；多应用租户检查 `X-BCP-App-Code` 是否携带且正确 |
| BCP-CMN-0004 | 404 | 资源不存在 | 核对路径参数（场景码/动作码/biz_key）拼写 |
| BCP-CMN-0005 | 409 | 幂等冲突：相同幂等键的不同载荷 | `client_request_id` 必须由 biz_key+动作稳定派生；**同一键不得更换载荷**——改业务数据请换新的幂等键 |
| BCP-CMN-0006 | 429 | 请求超出限流阈值 | 降频 + 指数退避重试 |
| BCP-CMN-0007 | 500 | 系统繁忙 | 指数退避重试（同幂等键）；持续出现携 `trace_id` 报障 |
| BCP-CMN-0008 | 503 | 下游服务不可用 | 指数退避重试；持续出现携 `trace_id` 报障 |

**动作触发段 BCP-EXT-8xxx**（§五触发动作接口）：

| 错误码 | HTTP | 说明 | 处理建议 |
|--------|------|------|----------|
| BCP-EXT-8015 | 404 | 业务动作不存在或未启用 | 以 catalog 实时返回的动作码为准，勿硬编码 |
| BCP-EXT-8016 | 403 | 协同角色范围授权拒绝 | 核对应用开通的"场景×角色"与本企业职责是否一致；报"应用未获该企业授权"= 该企业未授权你的应用，联系平台核对授权 |
| BCP-EXT-8017 | 404 | 业务对象实例不存在 | 对该 biz_key 触发动作前平台尚无实例——核对 biz_key 取值；新对象先走场景的首次登记类动作 |
| BCP-EXT-8018 | 400 | 业务事件码非法或超角色可订阅范围 | 订阅登记时 `event_codes` 必须是本角色可订阅集的子集，按返回的可订清单修正 |
| BCP-EXT-8019 | 401 | 调用方身份缺失 | 检查令牌是否过期/未携带；多应用租户检查 `X-BCP-App-Code` |
| BCP-AUTH-4091 | 401 | 令牌绑定应用与 `X-BCP-App-Code` 声明不一致（2026-09-02 签发期应用绑定） | 令牌签发时携带的 `app_code` 与请求头声明不是同一应用——去掉头部（以令牌绑定为准），或换发令牌时改用头部声明的应用编码；出现此错误说明集成侧存在凭证/头部混用，请排查 token 缓存按应用隔离 |
| BCP-EXT-8020 | 409 | 主数据 schema 校验失败 | payload 与实体 schema 不符（缺必填/类型不符）——按 `message` 指明的缺失字段补齐后**同幂等键重提** |
| BCP-EXT-8021 | 409 | 主数据版本冲突（乐观锁） | 重新回拉最新数据后重试 1 次 |
| BCP-EXT-8022 | 502 | 主数据/上游调用失败 | 可重试（同幂等键）；平台侧已入补偿 |
| BCP-EXT-8023 | 400 | project_code 无法解析为项目 | 核对触发方项目坐标是否正确注册，不可重试 |
| BCP-EXT-8024 | 400 | 模型类型或载荷载体配置非法 | 平台侧场景配置问题，携 `trace_id` 联系平台 |
| BCP-EXT-8025 | 404 | 场景不存在或未注册 | 核对场景码（已发布场景见 §七/附录 A） |
| BCP-EXT-8026 | 403 | 触发必填参数校验未通过 | 按 `message` 补齐触发上下文参数（如项目/企业坐标） |
| BCP-EXT-8027 | 400 | payload 必要参数未装载 | 按动作关联表单/schema 补齐 payload 字段 |
| BCP-EXT-8028 | 400 | 投送参数运算失败 | 平台侧配置问题，携 `trace_id` 联系平台 |
| BCP-EXT-8029 | 409 | 状态机迁移守卫拒绝 | 业务对象当前状态不允许该动作——对照 catalog `from_status` 核校后再触发；属业务时序问题，勿重试风暴 |
| BCP-EXT-8032 | 400 | 外部数据行定位失败 | 核对 payload 中的绑定参数（如编号字段）是否能在业务侧定位到记录 |
| BCP-EXT-8034 | 400 | 必填数据装配为空 | 补齐对应业务数据后重提 |
| BCP-EXT-8051 | 409 | 幂等键参数指纹不一致 | `client_request_id` 已被成功执行且本次 biz_key/payload 不同——同键须同参；新意图换新幂等键；参数修正重试（上次未成功）不受影响 |
| BCP-EXT-8035 | 400 | 覆盖字段越权 | payload 仅可覆盖动作声明的白名单字段，移除越权字段 |
| BCP-EXT-8036 | 400 | 引用资源未在动作载体声明 | 平台侧配置问题，携 `trace_id` 联系平台 |
| BCP-EXT-8037 | 404 | 引用资源实例不存在 | 核对 payload 引用的资源编号（如表单单据号/主数据编码） |
| BCP-EXT-8038 | 403 | 引用资源归属/状态非法 | 不得跨企业引用、不得引用已作废/非现行版本资源 |
| BCP-EXT-8039 | 503 | 资源验证通道不可用 | 退避重试（同幂等键）；持续出现携 `trace_id` 报障 |
| BCP-EXT-8041 | 502 | 派生事件发布失败 | 可重试（同幂等键） |
| BCP-EXT-8042 | 400 | 主数据实体未注册推送 | 平台侧配置问题，携 `trace_id` 联系平台 |
| BCP-EXT-8046 | 400 | biz_key 格式非法 | 须为三段式 `{前缀}:{entity_type}:{entity_code}`，按场景手册修正 |
| BCP-EXT-8047 | 403 | 数据行归属坐标校验失败 | 该行项目/企业坐标与触发方不符——核对你操作的数据归属本企业本项目 |
| BCP-EXT-8030/8031/8033/8040/8043/8044/8045 | 400/500/502 | 平台侧装配/配置类错误 | 携 `trace_id` 联系平台；5xx 可先退避重试一次 |

**状态机段 BCP-FSM-2xxx**（触发动作链路）：

| 错误码 | HTTP | 说明 | 处理建议 |
|--------|------|------|----------|
| BCP-FSM-2001 | 400 | 非法迁移：当前状态无该动作的迁移边 | 对照 catalog 的 `from_status` 核校当前状态后再触发 |
| BCP-FSM-2002 | 409 | 守卫规则拦截（附规则明细） | 按规则编号补正后重提（见 §8.2 范式） |
| BCP-FSM-2003 | 409 | 乐观锁冲突 | 刷新状态后重试 |
| BCP-FSM-2004 | 404 | 状态机实例不存在 | 核对 scene+biz_key；未实例化的对象先走登记动作 |
| BCP-FSM-2007 | 404 | 状态机模型不存在或未发布 | 平台侧配置问题，携 `trace_id` 联系平台 |
| BCP-RULE-3002 | 503 | 规则服务不可用（含阻断类守卫，降级拒绝） | 退避重试；持续出现携 `trace_id` 报障 |

### 8.2 业务校验拒绝（HTTP 200，`success=false`）

不是失败，是**补正信号**。处理范式：

```java
if (!data.getSuccess()) {
    String reason = data.getRejectReason();
    if (reason.contains("r_esc_ref")) {
        // 补数据：回查通知单编号+日期补入 payload，同 client_request_id 重提
        return retry(payloadPatched);
    }
    throw new BizRejectedException(reason);   // 其余上抛人工处理
}
```

常见校验速查（整改场景）：

| 校验规则 | 环节 | 补正指引 |
|----------|------|----------|
| 回复引用合法 | 整改回复 | payload 补 `ref_notice_no` + `ref_notice_date` |
| 签认齐套 | 销号/复工批复 | 签署槽位未齐不可销号/复工——先完成会签 |
| 未闭环不得复工 | 复工批复 | 先走完整改链再申请复工 |
| 逾期自动升级 | 整改中 | 逾期后回复动作改走复工链 `resume.apply` |

---

## 九、附录 A：各场景动作与事件目录

> 下表为各场景**全量**动作目录快照。你可触发的子集以 catalog 接口实时返回为准（授权即目录）。角色码：`project_dept` 项目部｜`company` 施工企业本部｜`supervisor` 监理｜`regulator` 主管部门｜`workbench_app` 工作台集成应用（仅订阅，无触发权）。

### A.1 project-info-change / enterprise-info-change 项目与企业基础信息变更（16 动作）

> 双场景合并立项：**project-info-change**（项目基础信息变更协同）/ **enterprise-info-change**（企业基础信息变更协同）。
> 本场景为**数据变更驱动通知协同**：绝大多数动作为主数据写路径触发（无状态迁移语义），变更事实发生后平台派生事件通知订阅方；本场景无关联表单。人员请销假两个动作为状态迁移（`ON_DUTY`↔`ON_LEAVE`）。

| 动作码 | 业务名 | biz_key 锚 | 触发角色 | 派生事件码 | 可订阅角色 |
|--------|--------|-----------|----------|------------|------------|
| `staff.add` | 关键岗位人员新增 | 人员编号 | project_dept | `staff.added` | workbench_app project_dept company |
| `staff.change` | 人员信息变更（含禁用/移除） | 人员编号 | project_dept | `staff.changed` | workbench_app project_dept company |
| `staff.transfer` | 人员调动（岗位变更） | 人员编号 | project_dept | `staff.transferred` | workbench_app project_dept company supervisor |
| `leave.approve` | 请假审批通过生效 | 人员编号 | project_dept | `leave.approved` | workbench_app project_dept company |
| `leave.return` | 销假返岗 | 人员编号 | project_dept | `leave.returned` | workbench_app project_dept company |
| `attend.check` | 考勤打卡登记 | 人员编号 | project_dept | `attend.checked` | workbench_app（**须显式勾选订阅**，不进默认订阅集） |
| `attend.daily_close` | 考勤日结汇总 | 项目编号 | project_dept（工作台日结任务） | `attend.daily_closed` | workbench_app project_dept |
| `subproject.add` | 子项目新增 | 项目编号 | project_dept | `subproject.added` | workbench_app project_dept company |
| `subproject.change` | 子项目信息变更 | 项目编号 | project_dept | `subproject.changed` | workbench_app project_dept company |
| `equip.add` | 项目设备新增 | 项目设备编号 | project_dept | `equip.added` | workbench_app project_dept company supervisor |
| `equip.change` | 项目设备变更 | 项目设备编号 | project_dept | `equip.changed` | workbench_app project_dept company supervisor |
| `project.change` | 项目基础信息变更 | 项目编号 | project_dept | `project.changed` | workbench_app project_dept company supervisor regulator |
| `enterprise.add` | 企业信息登记 | 统一社会信用代码 | company | `enterprise.added` | workbench_app company project_dept |
| `enterprise.change` | 企业基础信息变更 | 统一社会信用代码 | company | `enterprise.changed` | workbench_app company project_dept |

**仅可订阅事件（平台巡检自动派生，外部不可触发动作）**：

| 派生事件码 | 业务名 | 可订阅角色 |
|------------|--------|------------|
| `staff_cert.expiring` / `staff_cert.expired` | 人员证书到期预警 / 已过期 | workbench_app project_dept company |
| `ent_cert.expiring` / `ent_cert.expired` | 企业证书到期预警 / 已过期 | workbench_app company project_dept regulator |

### A.2 safety-rectify-loop 安全隐患整改闭环（10 动作）

| 动作码 | 业务名 | 触发角色 | 派生事件码 |
|--------|--------|----------|------------|
| `rectify.notify` | 整改通知签发 | supervisor / company | `rectify.notified` |
| `rectify.reply` | 整改回复提交 | project_dept | `rectify.replied` |
| `rectify.close` | 复查合格销号 | supervisor / company | `rectify.closed` |
| `rectify.return` | 复查不合格退回 | supervisor / company | `rectify.returned` |
| `rectify.overdue_escalate` | 逾期自动升级 | 平台自动 | `rectify.escalated_overdue` |
| `stop.escalate` | 停工/拒停升级 | supervisor / company | `stop.escalated` |
| `resume.apply` | 复工申请提交 | project_dept | `resume.applied` |
| `resume.approve` | 复工批复签认 | company / supervisor | `resume.approved` |
| `regulator.report` | 重大情况报告 | supervisor | `regulator.reported` |

> `quality-rectify-loop`（质量整改闭环）与 A.2 同构（另含事故专属终态边）。

---

## 十、附录 B：扩展接口（按需接入）

以下接口面向特定协作环节，按需开通 scope 后接入。字段细节以 catalog 与门户场景手册为准。

### B.1 电子签署（多方会签场景）

| 方法 | 地址 | scope | 用途 |
|------|------|-------|------|
| POST | `/bcp/esign/sessions` | `bcp:esign:write` | 对表单实例发起签章会话（幂等键：表单实例 ID） |
| GET | `/bcp/esign/sessions/{id}` | `bcp:esign:read` | 查询会话与槽位状态 |
| POST | `/bcp/esign/slots/{slotId}/sign` | `bcp:esign:write` | 槽位签署（`signMode`: `CA`/`SCAN`） |
| POST | `/bcp/esign/slots/{slotId}/reject` | `bcp:esign:write` | 槽位驳回 |
| POST | `/bcp/esign/sessions/{id}/cancel` | `bcp:esign:write` | 主责方撤销会话 |
| GET | `/bcp/esign/sessions/by-form/{formInstanceId}/seals` | `bcp:esign:read` | 查询表单签章产物 |

纪律：各方仅可签署/驳回**自己的槽位**，不得改他人字段。

### B.2 归档卷宗查询

| 方法 | 地址 | scope | 用途 |
|------|------|-------|------|
| GET | `/bcp/archive/dossiers?scene=&biz_key=` | `bcp:archive:read` | 按 biz_key 查卷宗（卷目录、归档项、完整度） |
| GET | `/bcp/archive/dossiers/{id}` | `bcp:archive:read` | 按卷宗 ID 查询 |
| GET | `/bcp/archive/dossiers/{id}/validate` | `bcp:archive:read` | 完整性校验（缺失必备卷清单） |
| POST | `/bcp/archive/dossiers/{id}/export` | `bcp:archive:export` | 卷宗导出（默认 PDF 打包） |

### B.3 表单打印

| 方法 | 地址 | scope | 用途 |
|------|------|-------|------|
| POST | `/bcp/form/forms/instances/{id}/print` | `bcp:form:write` | 生成打印件（可带签章），返回 `print_oss_key` |
| GET | `/bcp/form/print/docs` | `bcp:form:read` | 可打印单据分页查询 |
| POST | `/bcp/form/print/tasks` | `bcp:form:write` | 批量创建打印任务（`client_request_id` 幂等） |

---

## 十一、接口 4：保障性接口（对账与运维）

> v2.2 新增。面向**服务端到服务端**的自助保障通道：对账拉取、动作回执、订阅核对、连通性自检、失败重放。
> 统一路径前缀 `/bcp/open/assurance`，认证与通用约定同 §二（Bearer 令牌 + 多应用租户携 `X-BCP-App-Code`），scope 一律 `bcp:open:catalog`（读面）。
> **数据边界**：所有接口仅返回/操作**本应用**的数据（平台按令牌解析的应用身份强制过滤，越权即 404/401）。
> 操作范式（对账节奏、缺漏补偿流程）见《03-业务协同（BCP）保障性接口操作指南》。

### 11.1 投递日志增量拉取（对账主接口）

```http
GET {gateway}/bcp/open/assurance/deliveries?start=2026-08-28 00:00:00&cursor=0&size=100
Authorization: Bearer {access_token}
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `start` | 是 | 起始时间点（**含边界**）。支持 `2026-08-28T08:30:00` / `2026-08-28 08:30:00` / `2026-08-28`（=当日 00:00） |
| `cursor` | 否 | 游标=上一页最后一条的流水 `id`（首轮传 0 或省略）。按 id 递增翻页，拉取期间新增不错位 |
| `size` | 否 | 每页条数，默认 50，最大 200 |

**响应 data**：

| 字段 | 说明 |
|------|------|
| `records[]` | 投递流水（id / delivery_id / event_id / event_code / scene_code / channel / format / endpoint / status / attempt_count / http_status / elapsed_ms / error_message / attempt_timeline 重试时间线 / request_headers / request_body 报文快照 / occurred_at / delivered_at） |
| `next_cursor` | 下一页游标（本页最后一条 id；本页为空时回显入参 cursor） |
| `has_more` | 是否还有后续页 |

> 对账建议：以 5~15 分钟节奏增量轮询，`cursor` 本地持久化；`status=failed` 的记录可按 §11.5 自助重放。

### 11.2 动作处理回执查询

```http
GET {gateway}/bcp/open/assurance/actions/{client_request_id}
```

按触发时你自报的 `client_request_id` 查处理回执：`scene_code / action_code / biz_key / result（success/guard_rejected/grant_denied/not_found/system_failed）/ reject_reason / from_status / to_status / idempotent_replay / elapsed_ms / occurred_at`。查无此键返回 `BCP-EXT-8017`（404）。

### 11.3 本应用生效订阅清单

```http
GET {gateway}/bcp/open/assurance/subscriptions
```

返回当前有效订阅：`subscription_id / scene_code / event_family / event_codes / channel / format / endpoint / status / update_time`。**订阅密钥（callback_secret）永不下发**——遗忘密钥请走门户重新发码。

### 11.4 webhook 连通性自检

```http
POST {gateway}/bcp/open/assurance/webhook/test
Content-Type: application/json

{ "subscription_id": 990000000000070036 }
```

平台向该订阅登记的 endpoint 同步发一条 `test.ping` 测试事件（按订阅密钥签名），返回 `{delivery_id, http_status, elapsed_ms, error}`，投递记录落流水（status=test_success/test_failed，可在 §11.1 拉到）。**endpoint 不可自定义**（防探测滥用），改地址请先走门户变更订阅。

### 11.5 失败投递补偿重放

```http
POST {gateway}/bcp/open/assurance/deliveries/{delivery_id}/redeliver
```

对 `status=failed` 的投递，平台按**原报文、同 event_id** 重新投递（你的接收端按 event_id 幂等去重即可安全接收），重放结果返回 `{origin_delivery_id, delivery_id（新流水）, event_id, http_status, elapsed_ms, error}` 并落新流水。

| 错误码 | HTTP | 说明 |
|--------|------|------|
| BCP-EXT-8048 | 404 | 投递流水不存在或不属本应用 |
| BCP-EXT-8049 | 409 | 仅 failed 投递可重放（成功投递重放无意义且增加接收端压力） |
| BCP-EXT-8050 | 400 | 参数非法（start 时间格式 / 缺 subscription_id 等） |

> logging 留痕通道（演练场 logging:// 伪端点）无真实报文快照，不可重放（返回 8050）。

### 11.6 FSM 实例状态查询（按 biz_key 查当前状态与迁移历史）

> 状态对账**唯一权威通道**——禁止据 webhook 事件本地推断状态（§6.1 纪律 3）。路径挂开放面 `/bcp/open/{scene}` 前缀而非 `/assurance`（开放面统一收口 bcp-external 代理，bcp-fsm 内部端点不直接暴露第三方）。

```http
GET {gateway}/bcp/open/{scene}/fsm-instance?biz_key=ZG-2026-0188&history=true
Authorization: Bearer {access_token}
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `scene`（路径） | 是 | 场景码 |
| `biz_key` | 是 | 业务对象主键（各场景取值见 §七） |
| `model_type` | 否 | 模型类型。**可省**：场景单一 model_type 时平台自动解析；多值场景缺省回退 `device` |
| `history` | 否 | 默认 `false`；`true` 时 `data.history` 附全量迁移历史（含失败迁移记录） |

所需 scope：`bcp:fsm:read`。

**响应 data**：

| 字段 | 说明 |
|------|------|
| `id` / `scene_code` / `scene_version` | 实例 ID / 场景码 / 场景版本 |
| `model_type` / `biz_key` | 模型类型 / 业务对象主键（回显） |
| `lifecycle_instance_no` | 生命周期周期号（同一 biz_key 多周期时定位当前周期） |
| `state` | **当前状态**（对账判别字段，如 `RECTIFYING`） |
| `lifecycle_seq` | 当前迁移序号（与事件信封 `seq` 同源，可核对本地事件消费进度） |
| `project_id` / `enterprise_id` | 实例归属项目 / 企业 |
| `context` | 守卫上下文快照 |
| `history[]` | 迁移历史（仅 `history=true` 返回）：`event`（FSM 迁移令牌）/ `from` / `to` / `actor` / `ts` / `success`（1 成功 0 失败）/ `rejectReason`（失败原因） |
| `idempotent_replay` | 创建幂等重放标记（查询场景通常为 null） |

**响应示例**：

```json
{
  "code": "000000",
  "trace_id": "d2e3f4",
  "data": {
    "id": 1024,
    "scene_code": "safety-rectify-loop",
    "scene_version": "v2021",
    "model_type": "doc",
    "biz_key": "ZG-2026-0188",
    "lifecycle_instance_no": 1,
    "state": "REVIEW",
    "lifecycle_seq": 128,
    "project_id": 1052,
    "enterprise_id": 1053,
    "history": [
      { "event": "T1_NOTIFY", "from": null, "to": "RECTIFYING", "actor": "supervisor", "ts": "2026-08-01 09:12:03", "success": 1, "rejectReason": null },
      { "event": "T3_REPLY", "from": "RECTIFYING", "to": "REVIEW", "actor": "project_dept", "ts": "2026-08-06 10:00:00", "success": 1, "rejectReason": null }
    ]
  }
}
```

| 错误码 | HTTP | 说明 |
|--------|------|------|
| BCP-FSM-2004 | 404 | 状态机实例不存在（下游信封原样透传）——核对 scene+biz_key；未实例化的对象先走场景的首次登记动作 |

> **对账用法**：以 `biz_key` 周期性与本地副本比对 `state` + `lifecycle_seq`；触发动作被 `BCP-EXT-8029` 守卫拒绝时，先查本接口确认当前状态（对照 catalog `from_status`）再决定是否重提。

---

*关联文档：《01-业务协同（BCP）集成开发操作指南》（webhook 接收端代码范式、联调清单、平台操作五步走）｜《03-业务协同（BCP）保障性接口操作指南》（对账与运维操作范式）｜门户「API 文档 → 业务协同接口」各场景开发手册*
