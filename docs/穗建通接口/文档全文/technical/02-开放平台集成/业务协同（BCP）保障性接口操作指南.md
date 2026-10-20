# 穗建通开放平台 · 业务协同（BCP）保障性接口操作指南

> 版本：v1.0（对外发布版）
> 更新日期：2026-08-28
> 适用对象：已完成业务协同接入、需要**服务端自助对账与运维**的第三方应用研发/运维
> 配套资料：《02-业务协同（BCP）集成接口文档》§十一（接口契约详表）、《01-集成开发操作指南》（接入总流程）

保障性接口解决一类问题：**事件投递是异步的，当"平台说发了、你没收到"或"你收到了但处理失败"时，不等人工、不靠猜——自助查清事实、自助补偿**。所有接口为服务端到服务端调用（client_credentials 令牌），数据边界 = 仅本应用。

---

## 一、保障性 API 总表（一页速查）

统一前缀 `{gateway}/bcp/open/assurance`，scope 一律 `bcp:open:catalog`，认证见接口文档 §二/§三。

| # | 方法 | 地址 | 干什么 | 典型场景 |
|---|------|------|--------|----------|
| 1 | GET | `/deliveries?start=&cursor=&size=` | 拉取某时间点后发给本应用的全部投递流水（游标分页） | **定时对账**、缺漏排查 |
| 2 | GET | `/actions/{client_request_id}` | 按你自报的幂等键查动作处理回执 | 触发后超时未明结果的确认 |
| 3 | GET | `/subscriptions` | 查本应用当前生效订阅清单 | 核对"我该收到哪些事件" |
| 4 | POST | `/webhook/test` | 向已登记订阅的 endpoint 发测试事件 | 改密钥/换地址后的连通性自检 |
| 5 | POST | `/deliveries/{delivery_id}/redeliver` | 失败投递按原报文同 event_id 重投 | 对账发现失败的自助补偿 |

---

## 二、对账操作范式（推荐）

```
每 5~15 分钟跑一次：
  ① 拉增量：GET /deliveries?start={上次对账时刻-5min 重叠带}&cursor={本地持久化游标}
  ② 翻页直到 has_more=false，更新游标 = next_cursor
  ③ 本地比对：
     - 平台记录有、你本地无 → 按 request_body（完整报文快照）补处理，或按 §三.2 重放
     - status=failed → 修复你的接收端后 POST /deliveries/{delivery_id}/redeliver 自助重投
     - status=retrying → 平台自动重投中，无需干预
  ④ 你的接收端必须按 event_id 幂等（重放/平台重投都会带同一 event_id）
```

**游标为什么用 id 递增而不是页码**：拉取期间平台持续产生新投递，页码偏移会错位漏单；id 游标单调递增，多拉不错、可断点续拉。

**start 参数的重叠带**：对账起点回退 5 分钟（如上次对账到 10:00，本轮 start=09:55），重叠部分靠 event_id 幂等去重——宁可多拉，不可漏拉。

---

## 三、常见运维操作流

### 3.1 「收不到事件」自查三步

1. **核对订阅**：`GET /subscriptions`——你的应用是否登记了该场景的订阅？endpoint 是否正确？status 是否启用？
2. **连通性自检**：`POST /webhook/test {subscription_id}`——平台同步发一条 `test.ping`，看返回 `http_status`：
   - 200 → 通道正常，问题在业务事件过滤（核对订阅的 event_family/event_codes 是否覆盖该事件码）
   - 非 200/超时 → 你的 endpoint 不可达或验签失败，修好后重发自检
3. **查投递事实**：`GET /deliveries?start={事件应发生的时间点}`——
   - 有记录且 success → 平台已送达，检查你自己的接收日志（是否验签失败丢弃/幂等表误判）
   - 有记录且 failed/retrying → 看 `error_message` 与 `attempt_timeline` 逐次失败原因
   - 无记录 → 事件未派生或未命中你的订阅——回到第 1 步核对订阅范围；仍不明携 trace_id 联系平台

### 3.2 失败补偿（自助）

```
GET /deliveries?start=...        → 找出 status=failed 的 delivery_id
修复你的接收端（验签/可达性/业务异常）
POST /deliveries/{delivery_id}/redeliver   → 平台按原报文同 event_id 重投
```

- 仅 failed 可重放（成功投递返回 BCP-EXT-8049）；logging 留痕件（演练场伪端点）无快照不可重放（BCP-EXT-8050）
- 重放落新流水（`delivery_id` 前缀 `redeliver-`），可在 §11.1 拉到确认结果

### 3.3 改密钥 / 换接收地址

1. 门户「我的应用」变更 webhook 地址 / 重新发码（密钥遗忘只能重发，平台永不下发存量密钥）
2. 变更生效后：先 `GET /subscriptions` 确认新 endpoint 已生效
3. 再 `POST /webhook/test` 验证连通与验签
4. 窗口期失败的历史投递按 §3.2 逐条重放补齐

### 3.4 动作触发后结果不明（超时/网络抖动）

`GET /actions/{client_request_id}`——用你触发时自报的幂等键查平台侧处理结果：
- `result=success` → 已成功（勿重复触发；如需事件重发用 §3.2）
- `idempotent_replay=true` → 本次是重放命中，返回的是首个结果
- 查无 → 平台未收到该触发，可安全重发（同 client_request_id）

---

## 四、FAQ

| 问 | 答 |
|---|---|
| 能拉到多久以前的投递？ | 投递流水平台长期留存，不限制时间窗口；建议对账窗口控制在 7 天内（更老的携 trace_id 联系平台） |
| 能拉别人的投递吗？ | 不能。所有接口按令牌解析的应用身份强制过滤，越权返回 404/401 |
| MQ 通道（mq-queue）的投递怎么对账？ | 本接口流水覆盖 callback 通道全量；MQ 族以 MQ 自身的重投/死信语义为准（不在本通道） |
| 重放会让接收端重复处理吗？ | 不会——重放携带与原件**相同的 event_id**，你的接收端按 event_id 幂等去重即安全（接口文档 §九.3 接收端三段式纪律） |
| start 时间格式总拉不到数据？ | 支持三种：`2026-08-28T08:30:00`（ISO）/ `2026-08-28 08:30:00`（空格）/ `2026-08-28`（当日零点）。格式非法返回 BCP-EXT-8050 |
| cursor 传错了会怎样？ | cursor 是流水 id 下界（取 id > cursor）。传 0 从头拉；传错大值会跳过中间记录——游标务必本地持久化 |

---

*关联文档：《02-业务协同（BCP）集成接口文档》§十一（接口契约）｜《01-业务协同（BCP）集成开发操作指南》（接入总流程）*
