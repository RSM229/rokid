# 穗建通开放平台 · 业务协同（BCP）集成开发操作手册

> 版本：v1.1（对外发布版）
> 更新日期：2026-08-08
> 适用对象：以协同角色（项目部/监理/检测/产权/安拆/使用/建设/监管等）接入业务协同场景的第三方应用企业研发
> 配套资料：门户「API 文档 → 业务协同接口」各场景开发手册（动作目录 / 角色×动作矩阵 / webhook 指引）

---

## 目录

1. [概述](#一概述)
2. [接入准备](#二接入准备)
3. [集成模型速览](#三集成模型速览)
4. [开发五步](#四开发五步)
5. [业务对象主键 biz_key](#五业务对象主键-biz_key)
6. [webhook 投递契约与接收端实现](#六webhook-投递契约与接收端实现)
7. [平台操作五步走](#七平台操作五步走)
8. [联调清单与集成纪律](#八联调清单与集成纪律)
9. [附录：已发布场景与资料](#九附录已发布场景与资料)

---

## 一、概述

### 1.1 什么是业务协同集成

业务协同（BCP）让不同企业的系统在**同一个业务流程**里协作：你的系统用**业务动作**推动流程（如"整改回复提交"），平台完成业务裁决并**派生业务事件**，其他协同方的系统通过 **webhook** 实时收到事件（如"整改回复已提交"）。

对集成方而言，全部交互只有两类：

| 方向 | 交互 | 说明 |
|------|------|------|
| 你 → 平台 | **触发业务动作** | `POST /bcp/open/{scene}/actions/{action_code}`，业务语言，简单直接 |
| 平台 → 你 | **接收业务事件** | 平台 POST 标准信封到你的 webhook 地址，验签 + 幂等后消费 |

### 1.2 三个基本概念（一次记住）

- **动作码 action_code**：`{对象域}.{动词}`，如 `rectify.reply`（整改回复提交）。你能触发哪些动作，由你的**协同角色**决定。
- **业务事件码 event_code**：动作的派生结果，如 `rectify.replied`。订阅的基本单元。
- **角色授权**：每个场景按协同角色划定"可触发的动作集｜可订阅的事件集｜可写的主数据/表单范围"。**授权即目录**——你在目录接口看到的，就是你能做的，无第二口径。

---

## 二、接入准备

| # | 事项 | 说明 |
|---|------|------|
| 1 | 申请应用 | 开放平台「我的应用 → 申请新应用」，类型勾选**业务协同服务**，勾选**场景 × 角色**（务必与本企业职责一致——项目部 ≠ 监理） |
| 2 | 注册 webhook | 填 HTTPS 回调地址（业务协同必填）→「测试连接」（平台投递测试事件）→ 保存平台颁发的 **webhook 验签密钥**（`webhook_secret`，仅用于 webhook 验签，**不是**换取令牌的 AppSecret/client_secret） |
| 3 | 获取凭证 | AppKey/AppSecret 换取访问 token，动作触发 scope `bcp:open:action`，目录查询 scope `bcp:open:catalog` |
| 4 | 查开发手册 | 门户「API 文档 → 业务协同接口 → 场景手册」：§2 角色×动作矩阵确认"我可触发什么、可订阅什么" |

> **订阅范围说明**：事件订阅随"场景 × 角色"开通**批量生效**（角色可订阅全集），无需逐主题登记。如需收敛，可在订阅登记时用 `event_codes` 声明子集（必须是本角色可订阅集的子集，越界拒绝并返回可订清单）。

---

## 三、集成模型速览

```
你的系统                          平台                          其他协同方
   │  ① 触发动作（biz_key + 幂等键 + 业务载荷）
   │ ──────────────────────────▶  角色识别 → 授权校验 → 业务裁决
   │  ② 响应（from→to / 拒绝原因 + 规则编号）
   │ ◀──────────────────────────  │
   │                                │  ③ 派生标准事件
   │  ④ webhook 投递（验签 + 幂等）  ▼
   │ ◀────────────────────────   事件分发（订阅 ∩ 授权范围）  ──▶  其他方 webhook
```

**三条硬性纪律**：

1. **对外只用 biz_key**：业务对象的当前状态、历史均由平台维护；平台内部的实例标识、序号等由平台自管理，你的系统**无需也不应感知**。
2. **状态权威在平台**：事件只用于刷新本地只读副本，禁止据事件本地推断/改写状态；推动流程一律调动作 API。
3. **幂等三件套**：动作 `client_request_id`、信封 `event_id`、镜像 `idem_key`——重试/重投不重复执行、不重复消费。

---

## 四、开发五步

> 以下以「安全隐患整改闭环」（safety-rectify-loop）项目部（project_dept）为例，其余场景同构。

### 步骤① 目录自描述：先问平台"我能做什么"

```http
GET /bcp/open/safety-rectify-loop/catalog
Authorization: Bearer {token}        # scope: bcp:open:catalog
```

响应按**调用方角色过滤**——只含"我（项目部）"可见的目录：

```json
{
  "scene_code": "safety-rectify-loop",
  "role": "project_dept",
  "actions": [
    { "action_code": "rectify.reply", "biz_name": "整改回复提交",
      "from_to": ["RECTIFYING", "REVIEW"], "payload_ref": "form:GDAQ30309" }
  ],
  "events": [
    { "event_code": "rectify.notified", "biz_name": "整改通知签发" },
    { "event_code": "rectify.closed",   "biz_name": "复查合格销号" }
  ],
  "write_scope": { "md": [], "form": ["GDAQ30309:DRAFT:creator"] }
}
```

**开发动作**：把 catalog 自检接入启动流程——本地动作码/事件码清单与目录不一致即告警（场景升版早知道）。触发前看 `from_to`：业务对象当前状态不在 from 侧，调用必然被拒，**先查状态再触发，省一次往返**。

### 步骤② 触发业务动作（最小闭环）

```http
POST /bcp/open/safety-rectify-loop/actions/rectify.reply
Authorization: Bearer {token}        # scope: bcp:open:action
Content-Type: application/json

{
  "biz_key": "ZG-2026-0188",                    // 整改单号（业务对象主键）
  "client_request_id": "ext-20260806-30125",    // 稳定幂等键（biz_key+动作派生）
  "payload": {                                  // 业务载荷
    "reply_doc_no": "GDAQ30309-2026-0117",      // 30309 回复单
    "ref_notice_no": "GDAQ30308-2026-0042"      // 显式引用通知单编号（结构强制）
  }
}
```

响应（业务化，成功即给出迁移结果）：

```json
{
  "code": "0",
  "data": {
    "action_code": "rectify.reply", "biz_key": "ZG-2026-0188",
    "from_status": "RECTIFYING", "to_status": "REVIEW",
    "derived_event_code": "rectify.replied",
    "idempotent_replay": false, "success": true, "reject_reason": null
  }
}
```

**幂等**：`client_request_id` 是幂等键——网络重试/双击/重发均返回**首个结果**（`idempotent_replay: true`），不重复执行、不重复派生事件。对未存在的 `biz_key` 触发动作将返回 `OPEN_INSTANCE_NOT_FOUND`。

### 步骤③ 业务校验拒绝：不是失败，是补正信号

业务校验不通过时返回 **HTTP 200 + `success=false` + `reject_reason`（规则编号）**：

```json
{ "code": "0", "data": { "success": false, "from_status": "RECTIFYING", "to_status": null,
  "reject_reason": "GUARD[r_esc_ref]: 回复单未引用通知单编号+日期" } }
```

处理范式：**解析规则编号 → 引导补正 → 同幂等键重提**，不重试风暴：

```java
if (!r.getData().getSuccess()) {
    String reason = r.getData().getRejectReason();
    if (reason.contains("r_esc_ref")) {
        // 补数据：回查通知单编号+日期补入 payload，同 client_request_id 重提
        body.put("payload", Map.of("reply_doc_no", replyDocNo,
                "ref_notice_no", noticeNo, "ref_notice_date", noticeDate));
        return openApi.post(ACTION_URL, body);
    }
    throw new BizRejectedException(reason);   // 其余上抛人工处理
}
```

常见校验速查（整改场景）：

| 校验规则 | 环节 | 补正指引 |
|----------|------|----------|
| 回复引用合法 | 整改回复 | payload 补 `ref_notice_no` + `ref_notice_date`（回复单显式引用通知单编号+日期） |
| 签认齐套 | 销号/复工批复 | 签署槽位未齐不可销号/复工——先完成会签，再触发动作 |
| 未闭环不得复工 | 复工批复 | 整改未闭环时复工申请必被拦截——先走完整改链 |
| 逾期自动升级 | 整改中 | 逾期后整改单自动升级，回复动作改走复工链 `resume.apply` |

### 步骤④ 关联表单：表单即数据契约

动作目录中的 `payload_ref: form:GDAQ30309` 表明该动作的载荷结构以对应业务表单为准。**写范围约定**（越权写入平台将拦截并留痕）：

| 状态/方面 | 约定 |
|-----------|------|
| 主责方创建 + 草稿可改 | 每张表单唯一主责角色；草稿状态**仅创建方企业**可改 |
| 会签中只读 + 槽位操作 | 会签中主责方可撤回；各方仅可签署/驳回**自己的槽位**，不得改他人字段 |
| 生效后禁改 | 单据生效后任何方不可改；变更 = 新单据显式引用原单编号（三联单范式） |
| 附件与照片 | 随单据同权限；整改前后对比照片仅责任方上传，复查方不可删改 |

**开发含义**：你的系统创建表单时身份即主责方；不要尝试改别人的字段、不要改已生效的单据。

### 步骤⑤ 事件订阅与 webhook 接收

订阅范围随角色开通批量生效（见 §二）。事件经「订阅 ∩ 授权范围」双重过滤后投递——**项目 A 的事件不会投给只有项目 B 授权的应用**。接收端实现见 §六。

---

## 五、业务对象主键 biz_key

biz_key 是业务协同的**对齐锚点**：企业侧凭它触发动作、平台凭它定位业务对象并派生事件、订阅端凭它串行消费与回拉数据。

**取值**：平台**不生成** biz_key——值取自业务制度既有编号，各场景取值字段：

| 场景 | biz_key 取值 |
|------|--------------|
| crane-lifecycle（起重机械全生命周期管理） | 设备产权**备案编号**（如 `粤AG-备案-0001`） |
| safety-rectify-loop（安全隐患整改闭环） | **隐患编码**（hazard_code，如 `HD202608000185`） |
| quality-rectify-loop（质量整改闭环） | 隐患编码（hazard_code，如 `ZLH202609000031`） |
| concrete-order（混凝土订单协同） | 订单编号 `ddbm` / 生产批次号 `hntscpch`（批次号贯通生产/浇筑/检测/剩退各环节）等 |
| danger-works（危大工程全周期协同） | 危大清单识别登记编号 |

**使用要点**：

- 同一 biz_key 的事件**有序**——顺序敏感业务按 biz_key 串行处理；
- 同一 biz_key 可有多条生命周期（如设备转场后再进场），历史周期只读；
- **查询对账**：`GET /bcp/open/{scene}/fsm-instance?biz_key={key}&history=true` 查当前状态与迁移历史（scope `bcp:fsm:read`，详见《02-集成接口文档》§11.6——bcp-fsm 内部路径 `/bcp/fsm/**` 不经网关开放，勿直连）；`GET /md/{scene}/{entity}/by-key/{bizKey}` 拉取主档快照。

---

## 六、webhook 投递契约与接收端实现

### 6.1 投递报文（头族 + 标准信封）

```http
POST {你的 webhook 地址}
Content-Type: application/json
X-BCP-Event-Id: 9f3c…            # 幂等键（消费端去重）
X-BCP-Topic: safety-rectify-loop.lifecycle.status.changed
X-BCP-Scene: safety-rectify-loop
X-BCP-Timestamp: 1786000800050
X-BCP-Signature: 601e56…         # hex( HmacSHA256(webhook_secret, timestamp + "." + body) )
                                 # webhook_secret = 平台颁发的 webhook 验签密钥（不是 AppSecret/client_secret）
```

```json
{
  "event_id": "9f3c…",  "event_type": "status.changed",
  "topic": "safety-rectify-loop.lifecycle.status.changed",
  "trace_id": "abc123", "seq": 128,
  "occurred_at": "2026-08-06T10:00:00+08:00",
  "scene_code": "safety-rectify-loop", "scene_version": "v2021",
  "scope": { "project_id": 123 },
  "payload": {
    "biz_key": "ZG-2026-0188",
    "instance_id": 36002, "model_type": "doc",
    "event": "SUBMIT_REPLY", "from_state": "RECTIFYING", "to_state": "REVIEW",
    "lifecycle_seq": 4, "lifecycle_instance_no": 1,
    "project_id": 123, "enterprise_id": 8801,
    "review_result": "合格", "close_date": "2026-08-19"
  }
}
```

信封结构**所有事件同构、场景升版不变**；`payload` 为平铺结构：平台字段（FSM 元数据，固定语义）与触发侧业务参数透传原文（按场景/动作变化，对未知字段宽容处理即可）同层平铺；透传字段与平台字段撞名时以平台字段为准。平台字段语义与使用建议（幂等/乱序/副本刷新/大数 ID 精度）见《02-集成接口文档》§6.2 字段说明表。

### 6.2 接收端三段式纪律：验签 → 幂等 → 200

> 验签密钥 `webhook_secret` 在 webhook 注册时单独颁发，**与换取令牌的 AppSecret/client_secret 不是同一个**，不要混用。

```java
@PostMapping("/bcp/events")
public ResponseEntity<Void> onEvent(@RequestBody String rawBody,   // 原始报文！勿先反序列化
                                    @RequestHeader HttpHeaders h) {
    // ① 验签：用 webhook 验签密钥对原始报文 UTF-8 字节重算 HMAC
    String sig = hmacSha256Hex(webhookSecret,
        h.getFirst("X-BCP-Timestamp") + "." + rawBody);
    if (!sig.equalsIgnoreCase(h.getFirst("X-BCP-Signature")))
        return ResponseEntity.status(401).build();

    // ② 幂等：event_id 去重（建议保留 24h）
    JsonNode evt = objectMapper.readTree(rawBody);
    String eventId = evt.get("event_id").asText();
    if (!idemGuard.markIfFirst("my-app", eventId))
        return ResponseEntity.ok().build();      // 重复投递直接 200

    // ③ 消费：事件只刷新本地只读副本；需业务数据凭 biz_key 回拉平台
    handle(evt);
    return ResponseEntity.ok().build();           // 非 2xx / 超时 → 平台退避重试
}
```

**要点**：验签必须基于**原始报文字节**（先反序列化再拼接会验签失败）；返回 2xx 视为成功，连续失败平台进入补偿重放，排障凭 `event_id` / `delivery_id`。

---

## 七、平台操作五步走

| 步骤 | 操作 |
|------|------|
| 1 | **申请应用**：我的应用 → 申请新应用 → 类型勾选业务协同服务 → 勾选场景×角色 |
| 2 | **注册 webhook**：填 HTTPS 回调地址 → 测试连接 → 配对企业侧验签密钥 |
| 3 | **查开发手册**：API 文档 → 业务协同接口 → 场景手册 §2 角色×动作矩阵 |
| 4 | **开发联调**：catalog 自检 → 动作触发（幂等键）→ webhook 三段式接收 → 校验拒绝按规则编号补正 |
| 5 | **上线盯双日志**：业务协同跟踪日志——**发布日志**看触发结果/拒绝原因；**订阅日志**看投递结果/重试时间线，排障凭 `delivery_id` / `event_id` |

---

## 八、联调清单与集成纪律

### 8.1 上线前八项清单

- [ ] 应用已开通业务协同服务，场景×角色勾选与本系统职责一致（项目部 ≠ 监理）
- [ ] webhook 地址 HTTPS 可达，测试连接通过；验签密钥已配对
- [ ] catalog 自检已接入启动流程：本地动作码/事件码清单与目录一致
- [ ] 动作触发全部走 `/bcp/open/{scene}/actions/{action}`
- [ ] `client_request_id` 稳定生成（biz_key+动作派生），重试同键不重复执行
- [ ] 校验拒绝处理：`success=false` → 按 `reject_reason` 规则编号补正重提，不重试风暴
- [ ] 接收端三段式：验签（原始字节）→ `event_id` 幂等（24h）→ 200；异常返回非 200 进补偿
- [ ] 双日志可自查：发布日志（触发流水）与订阅日志（投递流水）

### 8.2 三条集成纪律

1. **只经业务层交互**：你的系统只使用**业务动作 / 业务事件码**；平台内部的技术接口与标识不对外、无需使用。
2. **授权即目录**：可触发动作与可订阅事件以 catalog 返回为准，门户文档与线上授权同源——以目录为唯一依据，不凭经验硬编码。
3. **范围之外即拒绝**：动作触发、事件订阅、主数据与表单写入均按"场景 × 角色"授权范围校验，越界一律拒绝并留痕——联调期先核对角色开通是否正确。

---

## 九、附录：已发布场景与资料

**已发布业务协同场景**：

| 场景码 | 场景名 |
|--------|--------|
| `crane-lifecycle` | 起重机械全生命周期管理 |
| `danger-works` | 危大工程全周期协同 |
| `safety-rectify-loop` | 安全隐患整改闭环 |
| `quality-rectify-loop` | 质量整改闭环 |
| `concrete-order` | 混凝土订单协同 |

**资料入口**：

- 穗建通开放平台门户 →「API 文档 → 业务协同接口」——各场景开发手册含动作目录（动作码/业务名/迁移/触发角色/关联表单/派生事件码/可订阅角色）、角色×动作矩阵、webhook 接入指引。
- <a href="/docs/index.html" target="_blank">BCP 业务协同场景开发说明文档中心（独立页面）</a>
