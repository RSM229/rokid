# 穗建通应用平台开放API接口集成指南

> **文档版本**：v2.0
> **生成日期**：2026-09-17
> **重要变更（v2.0 破坏性变更）**：所有待办 API 的处理人身份字段由 `assigneeId`（平台用户 ID / 手机号）统一替换为 `assigneeCode`（数据中台用户编码 userCode），旧字段不再受理，详见第十二章《v2.0 迁移指南》。
> **适用对象**：已接入统一应用集成框架的第三方应用开发者、服务窗业务方
> **关联文档**：
> - 《统一待办任务中心-分析报告》v1.1
> - 《统一待办中心-开放 API 分析报告》v1.0
> - 《统一应用集成框架接入指引 V1.0》— `doc-online/public/notes/technical/应用平台框架集成/统一应用集成框架接入指引V1.0.md`

---

## 一、概述

### 1.1 什么是统一待办中心

统一待办中心是**工程全生命周期一体化平台**面向 C 端用户的任务聚合入口，部署在 `yypt-portals-user-api` 工程中。已接入统一应用集成框架的第三方业务系统，可将审批/通知任务推送到本平台，用户无需切换多个系统即可集中处理。

### 1.2 与统一应用集成框架的关系

| 维度 | 统一应用集成框架 | 统一待办开放 API |
|------|------------------|------------------|
| **所在工程** | `apps/01-yypt/kj/backend` | `apps/01-yypt/yypt-portals-user-api` |
| **数据库** | 与 `yypt-portals-user-api` 共用同一套数据库 | 同一套数据库 |
| **应用凭证** | 注册后下发 `AppKey`、`AppSecret`、`windowsId` | **复用同一套 `AppKey` / `AppSecret`** |
| **签名方式** | `SHA1(AppSecret + Nonce + CurTime)` | **与框架完全一致** |
| **接口基地址** | `{API_URI}/yjy/external/open/**` | `yypt-portals-user-api` 基地址 + `/api/open/todo/**` |

> **重要约束**：所有统一待办开放 API 均属于 `yypt-portals-user-api` 工程实现；接入时**不修改、不侵入统一应用集成框架源码**。

### 1.3 接入后效果

- 用户在本平台待办中心看到第三方任务；
- 点击后跳转第三方页面，自动携带临时 token，无需再次登录；
- PC 端新浏览器 Tab 打开；移动端新页面打开，处理完后可无缝退回待办列表；
- 第三方可主动完成/取消待办，避免用户重复处理。

### 1.4 接入流程概览

```
① 已在统一应用集成框架注册，获得 AppKey / AppSecret / windowsId
        ↓
② 通过数据中台/ODS 链路获取处理人用户编码（assigneeCode）
        ↓
③ 按本规范调用 yypt-portals-user-api 开放 API 创建待办
        ↓
④ 用户点击待办 → yypt-portals-user-api 签发一次性 jump ticket
        ↓
⑤ 跳转第三方页面 → 用 ticket 兑换用户身份信息
        ↓
⑥ 第三方处理完成后回调 complete/cancel
```

---

## 二、接入前准备

### 2.1 应用凭证来源

统一待办开放 API **复用统一应用集成框架的应用凭证**，不需要单独申请待办凭证。

接入方需先在统一应用集成框架完成注册，管理员将提供：

| 参数 | 说明 | 示例 |
|------|------|------|
| `AppKey` | 应用标识（公开） | `EXPENSE_APP` |
| `AppSecret` | 应用密钥（**保密**） | `your-secret-here` |
| `windowsId` | 服务窗 ID | `519031479275884544` |

> 凭证管理、服务窗注册、用户同步等流程，详见《统一应用集成框架接入指引 V1.0》。

### 2.2 环境信息

#### 2.2.1 统一应用集成框架环境

| 环境 | 基地址（API_URI） |
|------|------------------|
| 测试环境 | `http://183.6.48.123:30680` |
| 生产环境 | `http://sjt.gz-szcj.com:8000` |

#### 2.2.2 统一待办开放 API 环境

| 环境 | 基地址 | 说明 |
|------|--------|------|
| 测试环境 | `https://api-test.example.com` | 以实际部署的 `yypt-portals-user-api` 地址为准 |
| 生产环境 | `https://api.example.com` | 以实际部署的 `yypt-portals-user-api` 地址为准 |

> 所有接口强制 HTTPS，HTTP 请求会被拒绝。

### 2.3 用户身份映射

创建待办时必须传入处理人的**数据中台用户编码**（`assigneeCode`）。userCode 由数据中台分配并 ODS 同步回写至平台（`portal_user_profile.user_code`），第三方通过数据中台/ODS 链路获取。

> **注意（v2.0 起）**：`assigneeId`（平台用户 ID）与手机号直传**均已废弃**，服务端不再受理；`assigneeCode` 无对应平台用户时接口返回 `assigneeCode 无对应平台用户` 错误。

---

## 三、快速开始

### 3.1 第一步：构造签名

统一待办开放 API 与统一应用集成框架使用**完全相同的请求头鉴权机制**：

| Header | 必填 | 说明 |
|--------|------|------|
| `Nonce` | 是 | 随机数，最大长度 36 个字符 |
| `CurTime` | 是 | 当前 UTC 时间戳，从 1970-01-01 00:00:00 到现在的秒数（String） |
| `AppKey` | 是 | 统一应用集成框架下发的 `AppKey` |
| `CheckSum` | 是 | `SHA1(AppSecret + Nonce + CurTime)`，小写十六进制字符串 |
| `Content-Type` | 是 | `application/json` |

### 3.2 第二步：创建待办

```bash
curl -X POST https://api.example.com/api/open/todo/create \
  -H "AppKey: EXPENSE_APP" \
  -H "Nonce: a1b2c3d4e5f6789012345678abcdef01" \
  -H "CurTime: 1788230400" \
  -H "CheckSum: {checksum}" \
  -H "Content-Type: application/json" \
  -d '{
    "taskCode": "EXPENSE_APPROVAL_V1",
    "taskTitle": "张三提交的差旅费报销待审批",
    "taskContent": "金额：￥5200，类型：差旅费",
    "businessType": "EXPENSE_REIMBURSEMENT",
    "businessId": 20260705001,
    "assigneeCode": "UC1461613321869462235",
    "assigneeName": "李四",
    "priority": 3,
    "endTime": "2026-07-08 18:00:00",
    "extraData": "{\"amount\":5200,\"currency\":\"CNY\"}",
    "jumpConfig": {
      "pcUrl": "https://expense.example.com/approve/20260705001",
      "mobileUrl": "https://expense.example.com/m/approve/20260705001"
    }
  }'
```

### 3.3 第三步：用户点击与跳转

用户在平台待办中心点击该待办后：

- **PC 端**：平台自动弹出新浏览器 Tab，打开 `https://api.example.com/api/open/todo/jump?ticket=xxx`，最终 302 到第三方 `pcUrl`；
- **移动端**：平台通过中转页跳转，最终打开第三方 `mobileUrl`。

### 3.4 第四步：兑换用户信息

第三方页面收到 `ticket` 后，调用：

```bash
POST /api/open/todo/exchange
{
  "ticket": "v1aR9...zKw"
}
```

返回：

```json
{
  "code": 0,
  "data": {
    "todoId": 10234,
    "userId": 1461613321869462235,
    "userName": "李四",
    "phone": "13800138000",
    "appKey": "EXPENSE_APP",
    "businessId": 20260705001,
    "businessType": "EXPENSE_REIMBURSEMENT",
    "issuedAt": 1788230400,
    "expiresIn": 30
  }
}
```

第三方据此签发自身会话 token，完成免登。

### 3.5 第五步：处理完成后回调

第三方审批完成后，调用完成接口关闭平台待办：

```bash
POST /api/open/todo/complete
{
  "businessType": "EXPENSE_REIMBURSEMENT",
  "businessId": 20260705001,
  "assigneeCode": "UC1461613321869462235",
  "result": 1,
  "comment": "已批准"
}
```

---

## 四、鉴权机制详解

### 4.1 签名算法

统一待办开放 API 复用统一应用集成框架的鉴权头：

```
CheckSum = SHA1(AppSecret + Nonce + CurTime)
```

| 参数 | 说明 |
|------|------|
| `AppSecret` | 统一应用集成框架下发的应用密钥 |
| `Nonce` | 随机数，最大长度 36 个字符，每次请求唯一 |
| `CurTime` | 当前 UTC 时间戳，秒级，字符串类型 |
| `SHA1` | 输出小写十六进制字符串 |

### 4.2 Java 签名示例

```java
import java.security.MessageDigest;

public class CheckSumBuilder {

    private static final char[] HEX_DIGITS = {
        '0', '1', '2', '3', '4', '5', '6', '7',
        '8', '9', 'a', 'b', 'c', 'd', 'e', 'f'
    };

    public static String getCheckSum(String appSecret, String nonce, String curTime) {
        return encode("sha1", appSecret + nonce + curTime);
    }

    private static String encode(String algorithm, String value) {
        if (value == null) return null;
        try {
            MessageDigest messageDigest = MessageDigest.getInstance(algorithm);
            messageDigest.update(value.getBytes());
            return getFormattedText(messageDigest.digest());
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private static String getFormattedText(byte[] bytes) {
        int len = bytes.length;
        StringBuilder buf = new StringBuilder(len * 2);
        for (byte b : bytes) {
            buf.append(HEX_DIGITS[(b >> 4) & 0x0f]);
            buf.append(HEX_DIGITS[b & 0x0f]);
        }
        return buf.toString();
    }
}
```

### 4.3 Node.js 签名示例

```javascript
const crypto = require('crypto');

function getCheckSum(appSecret, nonce, curTime) {
  return crypto.createHash('sha1')
    .update(appSecret + nonce + curTime)
    .digest('hex');
}
```

### 4.4 Python 签名示例

```python
import hashlib

def get_checksum(app_secret, nonce, cur_time):
    return hashlib.sha1(f'{app_secret}{nonce}{cur_time}'.encode('utf-8')).hexdigest()
```

### 4.5 GET 请求签名示例

请求：

```
GET /api/open/todo/list?assigneeCode=UC1461613321869462235&status=0&pageNo=1&pageSize=20
```

Header：

```
AppKey: EXPENSE_APP
Nonce: a1b2c3d4e5f6789012345678abcdef01
CurTime: 1788230400
CheckSum: {sha1(AppSecret + Nonce + CurTime)}
```

---

## 五、API 接口详解

### 5.1 创建待办

```
POST /api/open/todo/create
```

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `taskCode` | String | 是 | 应用内唯一分类键，如 `EXPENSE_APPROVAL_V1` |
| `taskTitle` | String | 是 | 待办标题 |
| `taskContent` | String | 否 | 待办内容/摘要 |
| `businessType` | String | 是 | 业务类型，建议前缀为 `APPKEY_`，如 `EXPENSE_REIMBURSEMENT` |
| `businessId` | Long | 是 | 第三方业务主键 |
| `assigneeCode` | String | 是 | 处理人数据中台用户编码（userCode，如 `UC1461613321869462235`） |
| `assigneeName` | String | 否 | 处理人姓名 |
| `priority` | Integer | 否 | 1 低 / 2 中 / 3 高 / 4 紧急，默认 2 |
| `endTime` | String | 否 | 截止时间，`yyyy-MM-dd HH:mm:ss` |
| `extraData` | String | 否 | JSON 字符串，业务扩展字段 |
| `idempotent` | Boolean | 否 | 是否幂等创建，默认 `true` |
| `jumpConfig.pcUrl` | String | 否 | PC 跳转 URL，域名须在白名单 |
| `jumpConfig.mobileUrl` | String | 否 | Mobile 跳转 URL |
| `jumpConfig.fallbackInside` | Boolean | 否 | 无 URL 时是否回退平台内弹窗，默认 `true` |

**响应**：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "todoId": 10234
  }
}
```

> `idempotent=true` 时，同一 `(businessType, businessId, assigneeCode 解析出的处理人, taskCode)` 在待处理状态下不会重复创建，返回已有待办的 `todoId`。

### 5.2 取消待办

```
POST /api/open/todo/cancel
```

**请求体**：

```json
{
  "businessType": "EXPENSE_REIMBURSEMENT",
  "businessId": 20260705001,
  "assigneeCode": "UC1461613321869462235",
  "remark": "申请人撤回"
}
```

> 只能取消本应用（`creator_app_key`）创建的待办。

### 5.3 完成待办

```
POST /api/open/todo/complete
```

**请求体**：

```json
{
  "businessType": "EXPENSE_REIMBURSEMENT",
  "businessId": 20260705001,
  "assigneeCode": "UC1461613321869462235",
  "result": 1,
  "comment": "已批准"
}
```

| 字段 | 说明 |
|------|------|
| `result` | 1 通过 / 2 驳回 / 3 转办 |
| `comment` | 处理意见 |

### 5.4 查询待办列表

```
GET /api/open/todo/list?assigneeCode=UC1461613321869462235&status=0&pageNo=1&pageSize=20
```

仅返回本应用创建的待办。

### 5.5 查询统计

```
GET /api/open/todo/stats?assigneeCode=UC1461613321869462235
```

返回统计：

```json
{
  "code": 0,
  "data": {
    "total": 12,
    "pending": 5,
    "processing": 1,
    "completed": 4,
    "monthCompleted": 3,
    "urgent": 1,
    "overdue": 1,
    "today": 2
  }
}
```

| 字段 | 说明 |
|------|------|
| `total` | 未完成总数（status < 2） |
| `pending` | 待处理 |
| `processing` | 处理中 |
| `completed` | 已完成总数 |
| `monthCompleted` | 本月已完成数 |
| `urgent` | 紧急待办（priority=4 且未完成） |
| `overdue` | 已逾期（截止时间早于今日 0 点且未完成） |
| `today` | 今日截止（截止时间在当天且未完成） |

### 5.6 应用自检

```
GET /api/open/todo/ping
```

用于验证签名算法实现是否正确。

### 5.7 兑换票据

```
POST /api/open/todo/exchange
```

**请求体**：

```json
{
  "ticket": "v1aR9...zKw"
}
```

**响应**：

```json
{
  "code": 0,
  "data": {
    "todoId": 10234,
    "userId": 1461613321869462235,
    "userName": "李四",
    "phone": "13800138000",
    "appKey": "EXPENSE_APP",
    "businessId": 20260705001,
    "businessType": "EXPENSE_REIMBURSEMENT",
    "issuedAt": 1788230400,
    "expiresIn": 30
  }
}
```

> 兑换失败（票据不存在/已使用/已过期）返回 `HTTP 200` 包裹的业务错误，`code=403`，`msg` 为失败原因（如"票据已过期"）。
> `expiresIn` 为票据剩余有效秒数。

### 5.8 配额与限流

所有 `/api/open/**` 接口（除 `/jump`、`/exchange` 外）均受配额与限流保护：

| 维度 | 配置字段 | 默认值 | 说明 |
|------|---------|--------|------|
| 日配额 | `yypt_todo_app.daily_quota` | 10000 | 单应用每日请求总数上限 |
| 分钟限流 | `yypt_todo_app.rate_limit_per_min` | 600 | 单应用每分钟请求数上限 |

超限返回 `HTTP 429`，响应体：

```json
{
  "code": 429,
  "message": "应用请求过于频繁，请稍后再试",
  "data": "OPENAPI_RATE_LIMITED"
}
```

### 5.9 审计日志

平台会异步记录所有 `/api/open/**` 调用日志，存储于 `yypt_todo_open_api_audit` 表，包含：

- `app_key` / `app_name`：调用方应用
- `api_path` / `http_method`：请求路径与方法
- `request_body`：请求体（截断 2000 字符）
- `response_status` / `response_code`：HTTP 与业务响应码
- `client_ip` / `cost_millis` / `created_time`

---

## 六、IM 消息接口

### 6.1 发送 IM 应用消息（按 userCode）

```
POST /api/open/im/sendAppMsg
```

向平台用户发送 IM 应用消息（服务窗消息）。参数与统一应用集成框架接口 `/yjy/external/open/im/sendAppMsg` **完全一致**，唯一差异：`toUsers` 不传手机号，改传**数据中台用户编码 userCode**；平台内部将 userCode 解析为手机号后透传框架接口发送。

**鉴权**：与待办 API 相同的签名头（`AppKey` / `Nonce` / `CurTime` / `CheckSum`），并计入应用配额与限流。

**请求体**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | String | 是 | 消息标题，1~64 字符 |
| `content` | String | 是 | 消息内容，1~512 字符 |
| `coverUrl` | String | 否 | 封面图 URL，≤256 字符；为空为纯文本消息 |
| `toUsers` | String | 是 | 接收人 userCode 列表，英文逗号分隔，≤1024 字符 |
| `andriodJumpUrl` | String | 否 | 安卓端跳转链接（字段名与框架一致，为 andriod 非 android），≤256 字符 |
| `iosJumpUrl` | String | 否 | iOS 端跳转链接，≤256 字符 |
| `pcJumpUrl` | String | 否 | PC 端跳转链接，≤256 字符 |

**请求示例**：

```bash
curl -X POST https://api.example.com/api/open/im/sendAppMsg \
  -H "AppKey: EXPENSE_APP" \
  -H "Nonce: a1b2c3d4e5f6789012345678abcdef01" \
  -H "CurTime: 1788230400" \
  -H "CheckSum: {checksum}" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "审批结果通知",
    "content": "您提交的报销单已审批通过",
    "toUsers": "UC10001,UC10002",
    "pcJumpUrl": "https://expense.example.com/detail/20260705001"
  }'
```

**响应**：

```json
{
  "code": 0,
  "msg": "ok",
  "data": "消息ID"
}
```

**失败场景**：

| 场景 | 响应 |
|------|------|
| 任一 userCode 无对应用户或无手机号 | `code` 非 0，`msg` 列出无效 userCode（整体不发送） |
| 解析后无有效接收人 | `code` 非 0，`msg=无有效接收人` |
| 框架侧发送失败 | 透传框架错误码与消息 |

> **注意**：userCode 为数据中台分配的用户编码（ODS 同步回写至 `portal_user_profile.user_code`），非平台用户 ID、非手机号。若只有手机号，请直接调用框架接口 `/yjy/external/open/im/sendAppMsg`。

---

## 七、跳转协议详解

### 7.1 整体时序

```
用户点击待办
    ↓
平台前端调用 POST /api/todo/jump/sign 获取 ticket
    ↓
平台生成一次性 jump ticket（30 秒有效）
    ↓
PC: window.open('/api/open/todo/jump?ticket=xxx', '_blank')
Mobile: 经 /todo/jump-bridge 中转页 location.replace 跳转
    ↓
/api/open/todo/jump 校验 ticket 后 302 到第三方 URL
    ↓
第三方页面收到 ticket，调用 /api/open/todo/exchange 换取用户信息
    ↓
第三方签发自身会话，展示任务详情
```

> **补充说明**：
> - ticket 签发**不校验待办状态**：已完成/已取消的待办记录同样可签发并跳转（便于用户回看已办详情）。
> - 平台还提供签发+跳转一体接口 `GET /api/todo/jump/sign/{clientType}/{todoId}`（需平台登录态），一次请求直接 302 到第三方 URL，`clientType` 取 `PC` / `MOBILE`。
> - 目标 URL 按 `clientType` 严格选择：PC 用 `pcUrl`，MOBILE 用 `mobileUrl`，**不做跨端回退**；对应端未配置 URL 时签发报"待办未配置对应端跳转 URL"。

### 7.2 PC 端跳转

平台行为：

```ts
window.open(jumpUrl, '_blank', 'noopener')
```

第三方无需特殊处理，按普通浏览器页面开发即可。建议：

- 页面加载时立即用 ticket 兑换 token；
- 兑换失败（ticket 过期/已使用）时展示友好错误页，引导用户回到平台待办中心重新点击。

### 7.3 移动端跳转与退回

移动端采用"中转页"模式，保证用户处理完后能回到待办列表。

#### 7.3.1 平台行为

```ts
// 待办列表点击第三方待办
router.push({
  path: '/todo/jump-bridge',
  query: { todoId: String(todoId) }
})

// 中转页
onMounted(async () => {
  const { jumpUrl } = await signJumpTicket(todoId, 'MOBILE')
  window.location.replace(jumpUrl)
})
```

#### 7.3.2 第三方退回策略（三选一）

| 策略 | 实现方式 | 推荐场景 |
|------|---------|---------|
| **history.back()** | 第三方页面顶部"返回"按钮调用 `window.history.back()` | 独立 H5 |
| **postMessage** | 第三方调用 `window.postMessage({ type: 'todo-jump-back' }, targetOrigin)` | 服务窗容器 |
| **URL 回跳** | 跳转 URL 带 `backUrl` 参数，第三方返回时 `location.href = backUrl` | 第三方完全可控 |

**推荐**：服务窗场景用 `postMessage`，独立 H5 用 `history.back()`。

### 7.4 Token 传递方式

跳转 URL 最终会被追加以下参数（URL 编码）：

```
https://expense.example.com/approve/20260705001?ticket=xxx&todoId=10234&businessType=EXPENSE_REIMBURSEMENT&AppKey=EXPENSE_APP
```

| 参数 | 说明 |
|------|------|
| `ticket` | 一次性跳转票据，用于兑换用户信息 |
| `todoId` | 待办 ID |
| `businessType` | 业务类型 |
| `AppKey` | 来源应用标识（**首字母大写**，与鉴权头一致） |

第三方页面加载后立即调用 `POST /api/open/todo/exchange` 用 `ticket` 兑换用户信息。

> **注意**：`yypt_todo_app.token_exchange_url` 字段当前**未启用**（实体保留但跳转链路不消费）。平台不会代第三方做 ticket→token 预兑换，请一律走 `/api/open/todo/exchange` 自行兑换。

### 7.5 跳转 URL 规范

- 必须为 HTTPS；
- 域名必须在 `callback_whitelist` 中；
- 不支持 iframe 嵌套（避免 X-Frame-Options 冲突）；
- 建议第三方页面设置 `X-Frame-Options: DENY`。

---

## 八、数据模型说明

### 8.1 待办状态

| 状态值 | 含义 | 说明 |
|--------|------|------|
| 0 | 待处理 | 用户未处理 |
| 1 | 处理中 | 预留，本期暂不经过此状态 |
| 2 | 已完成 | 已通过/驳回/转办 |
| 3 | 已取消 | 业务撤回或第三方主动取消 |

### 8.2 优先级

| 优先级值 | 含义 |
|---------|------|
| 1 | 低 |
| 2 | 中（默认） |
| 3 | 高 |
| 4 | 紧急 |

### 8.3 业务类型命名规范

建议格式：

```
{APP_KEY}_{BIZ_NAME}
```

示例：

- `EXPENSE_REIMBURSEMENT`
- `SJT_CONTRACT_APPROVAL`

---

## 九、错误码

| 错误标识 | HTTP | 说明 | 处理建议 |
|----------|------|------|---------|
| `OPENAPI_HEADER_MISSING` | 401 | 缺少必填鉴权头 | 检查 Nonce、CurTime、AppKey、CheckSum 是否完整 |
| `OPENAPI_TIMESTAMP_INVALID` | 401 | 时间戳格式错误或偏差过大 | 校准服务器时间，CurTime 使用秒级 UTC 时间戳 |
| `OPENAPI_NONCE_REPLAYED` | 401 | 请求 nonce 已被使用 | 每次请求使用唯一 Nonce |
| `OPENAPI_APP_NOT_FOUND` | 401 | 应用不存在 | 检查 AppKey 是否拼写正确或是否已在框架注册 |
| `OPENAPI_SIGNATURE_ERROR` | 401 | 签名校验失败 | 检查 CheckSum 算法、AppSecret、CurTime、Nonce |
| `OPENAPI_APP_DISABLED` | 403 | 应用已停用 | 联系平台管理员启用 |
| `OPENAPI_CALLBACK_DOMAIN_FORBIDDEN` | 403 | 跳转 URL 域名未授权 | 检查 `callback_whitelist` 是否包含目标域名 |
| `OPENAPI_RATE_LIMITED` | 429 | 日配额用完或分钟限流 | 联系平台提升配额或降低调用频率 |
| `OPENAPI_INTERNAL_ERROR` | 500 | 服务异常 | 稍后重试，联系平台排查 |
| `403`（业务码） | 200 | 票据兑换失败：`/api/open/todo/exchange` 票据不存在/已使用/已过期 | 引导用户回到平台待办中心重新点击待办 |

> 全局兜底默认值：每日配额 `10000` 次，每分钟限流 `600` 次。可在 `yypt_todo_app.daily_quota` / `rate_limit_per_min` 中按应用调整。
> 时间戳默认容差 ±120 秒（`yypt.openapi.timestamp-tolerance-seconds`）；Nonce 防重放缓存 300 秒。

---

## 十、安全最佳实践

1. **AppSecret 保管**：禁止前端、Git、日志中暴露；建议存入 KMS/密钥管理系统；
2. **HTTPS 强制**：所有接口仅支持 HTTPS；
3. **Nonce 唯一**：每次请求使用全新 UUID，禁止复用；
4. **时间同步**：确保服务器时间与平台误差在容差内（默认 ±120 秒，建议 < 1 分钟）；
5. **域名白名单**：仅配置必要的跳转域名，避免钓鱼风险；
6. **Token 兑换及时**：收到 ticket 后立即兑换，不要缓存或传递；
7. **错误处理**：401 时不要无限重试，先检查签名与配置。

---

## 十一、FAQ

**Q1：第三方如何获取处理人的 assigneeCode？**

A：userCode 由数据中台分配，第三方通过数据中台/ODS 链路同步平台用户时获取（平台侧存于 `portal_user_profile.user_code`）。v2.0 起不再支持平台用户 ID 或手机号直传。

**Q2：一个业务单需要多人审批怎么办？**

A：多次调用 `/api/open/todo/create`，每次传入不同 `assigneeCode`，但保持 `businessType` 和 `businessId` 相同。

**Q3：用户点击后提示 ticket 已过期？**

A：jump ticket 仅 30 秒有效且一次性消费。引导用户回到平台待办中心重新点击。

**Q4：PC 端能否不打开新 Tab？**

A：不能。平台统一在新 Tab 打开第三方待办，避免用户离开本平台。

**Q5：移动端服务窗容器拦截跳转怎么办？**

A：平台中转页已做降级处理，若自动跳转失败会展示"点击继续"按钮，由用户主动触发。

**Q6：应用 secret 泄露如何处置？**

A：立即联系平台运营重置 AppSecret，旧 secret 将在 24 小时内失效。

---

## 十二、v2.0 迁移指南

### 12.1 变更内容

自 v2.0 起，所有待办开放 API 的处理人身份字段统一为**数据中台用户编码 userCode**，平台用户 ID（`assigneeId`）与手机号直传**不再受理**。

### 12.2 受影响接口清单

| 接口 | 方法 | 变更点 |
|------|------|--------|
| `/api/open/todo/create` | POST | 请求体 `assigneeId: Long` → `assigneeCode: String`（必传，userCode） |
| `/api/open/todo/cancel` | POST | 请求体 `assigneeId: Long` → `assigneeCode: String`（必传） |
| `/api/open/todo/complete` | POST | 请求体 `assigneeId: Long` → `assigneeCode: String`（必传） |
| `/api/open/todo/list` | GET | 查询参数 `assigneeId: Long` → `assigneeCode: String`（必传） |
| `/api/open/todo/stats` | GET | 查询参数 `assigneeId: Long` → `assigneeCode: String`（必传） |

不受影响：`/api/open/todo/ping`、`/api/open/todo/jump`、`/api/open/todo/exchange`、`/api/open/im/sendAppMsg`（本即为 userCode 口径）。

### 12.3 第三方改造要点

1. **身份来源**：改造用户同步链路，保存平台用户的 userCode（数据中台分配，平台侧 `portal_user_profile.user_code`）；此前缓存的平台用户 ID / 手机号映射需替换为 userCode。
2. **请求调整**：上述 5 个接口统一改传 `assigneeCode`；服务端先按 userCode 解析为平台用户，解析失败返回 `assigneeCode 无对应平台用户`（`code` 非 0）。
3. **类型变化**：`assigneeId` 为数值，`assigneeCode` 为字符串（含字母前缀），注意序列化与数据库存储类型。
4. **跳转链路**：`/jump`、`/exchange` 票据协议不变，已发出的历史待办跳转与处理不受影响。
5. **联调验证**：先用 `/api/open/todo/ping` 验证签名，再用 `create` 小流量验证 userCode 解析。

---

## 十三、附录

### 13.1 完整 Java 接入示例

```java
public class TodoOpenApiDemo {

    private static final String TODO_BASE_URL = "https://api.example.com";
    private static final String APP_KEY = "EXPENSE_APP";
    private static final String APP_SECRET = "your-secret-here";

    public static void main(String[] args) throws Exception {
        String body = "{"
            + "\"taskCode\":\"EXPENSE_APPROVAL_V1\","
            + "\"taskTitle\":\"报销单待审批\","
            + "\"businessType\":\"EXPENSE_REIMBURSEMENT\","
            + "\"businessId\":20260705001,"
            + "\"assigneeCode\":\"UC1461613321869462235\","
            + "\"jumpConfig\":{"
            +   "\"pcUrl\":\"https://expense.example.com/approve/20260705001\","
            +   "\"mobileUrl\":\"https://expense.example.com/m/approve/20260705001\""
            + "}"
            + "}";

        String curTime = String.valueOf(System.currentTimeMillis() / 1000);
        String nonce = UUID.randomUUID().toString().replace("-", "");
        String checkSum = CheckSumBuilder.getCheckSum(APP_SECRET, nonce, curTime);

        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(TODO_BASE_URL + "/api/open/todo/create"))
            .header("AppKey", APP_KEY)
            .header("Nonce", nonce)
            .header("CurTime", curTime)
            .header("CheckSum", checkSum)
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(body))
            .build();

        HttpClient client = HttpClient.newHttpClient();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
        System.out.println(response.body());
    }
}
```


---

**文档维护**：平台架构组  
**下次评审**：2026-07-12
