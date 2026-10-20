# PC SDK 集成手册

> **面向对象**：第三方服务窗技术开发人员  
> **版本**：v1.0.0  
> **更新日期**：2026-04

---

## 一、快速集成

### 步骤 1：引入 SDK

在 HTML 文件的 `<head>` 中引入 SDK：

```html
// 测试环境
<script src="http://183.6.48.123:30680/pcsdk/pcsdk.min.js"></script>
// 生产环境
<script src="http://sjt.gz-szcj.com:8000/pcsdk/pcsdk.min.js"></script>
```

或本地引入：

```html
<script src="./libs/pcsdk.min.js"></script>
```

### 步骤 2：使用 SDK

SDK 会自动挂载到 `window.umPCSdk`，直接在业务代码中使用：

```javascript
// 获取当前 token
const token = window.umPCSdk.getToken();

// 获取用户信息
const userInfo = await window.umPCSdk.getUserInfo();
console.log(userInfo.userName);
```

---

## 二、集成前准备（重要）

### 向平台方提供站点信息

**第三方服务窗开发方在集成前，必须向平台方提供以下信息：**

| 信息项 | 说明 | 示例 |
|--------|------|------|
| **服务窗域名** | 服务窗部署后的访问域名 | `https://service-a.example.com` |
| **开发环境地址** | 本地开发调试地址 | `http://localhost:5173` |
| **测试环境地址** | 测试服务器地址 | `https://service-a-test.example.com` |

### 为什么需要提供站点信息？

平台方需要在 **Gateway 网关** 中配置 CORS（跨域资源共享）白名单，允许你的服务窗域名访问后端 API。

**如果不提供站点信息，会出现以下问题：**

1. **浏览器拦截请求** - 出现 CORS 跨域错误
   ```
   Access to fetch at 'http://gateway.com/yjy/...' from origin 'http://your-site.com' 
   has been blocked by CORS policy.
   ```

2. **无法获取用户信息** - `getUserInfo()` 调用失败
3. **无法打开其他服务窗** - `openServerWindow()` 调用失败

### 部署方式对比

| 部署方式 | 是否需要配置 CORS | 说明 |
|---------|------------------|------|
| **同域部署** | ❌ 不需要 | 服务窗与网关同一域名，如 `https://yypt.com/service-a` |
| **跨域部署** | ✅ 必须配置 | 服务窗独立域名，如 `https://service-a.com` |

**建议：** 优先采用同域部署（通过 Nginx 代理），避免跨域问题。

### 联系平台方

将你的站点信息发送给平台技术支持人员，申请添加到 Gateway CORS 白名单。

---

## 三、API 说明

### getToken()

获取当前用户的访问 Token。

```javascript
const token = window.umPCSdk.getToken();
```

### getUserInfo(options?)

获取当前登录用户信息。

```javascript
// Promise 方式
const userInfo = await window.umPCSdk.getUserInfo();

// 回调方式
window.umPCSdk.getUserInfo({
  success: (userInfo) => {
    console.log('用户ID:', userInfo.userId);
    console.log('用户名:', userInfo.userName);
    console.log('手机号:', userInfo.phone);
  },
  fail: (err) => {
    console.error('获取失败:', err);
  }
});
```

**返回字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| userId | string | 用户ID |
| userName | string | 用户姓名 |
| avatar | string | 头像URL |
| phone | string | 手机号 |
| account | string | 登录账号 |
| orgName | string | 组织名称 |

### setData(options?)

存储数据到主进程级别的键值对存储，用于窗口间共享数据。

```javascript
// 存储单个值
window.umPCSdk.setData({
  key: 'token',
  value: 'Bearer xxxxx'
});

// 存储对象
window.umPCSdk.setData({
  key: 'appConfig',
  value: { theme: 'dark', lang: 'zh-CN' }
});

// 不传参数时清空所有存储数据
window.umPCSdk.setData();
```

**参数说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| key | string | 否 | 存储键名 |
| value | any | 否 | 存储值（需可序列化） |

> **注意**：`key` 和 `value` 仅在存储时需要，不传参数表示清空。

### getData(key)

读取已存储的数据，同步返回。

```javascript
const token = window.umPCSdk.getData('token');
const config = window.umPCSdk.getData('appConfig');
```

**典型应用场景：**

```javascript
// 登录成功后存储会话信息
window.umPCSdk.setData({
  key: 'userSession',
  value: {
    token: 'xxx',
    userId: '123',
    userName: '张三'
  }
});

// 其他页面读取
const session = window.umPCSdk.getData('userSession');
axios.defaults.headers['Authorization'] = session.token;
```

**跨服务窗数据共享说明：**

| 环境 | 是否支持跨服务窗 | 说明 |
|------|----------------|------|
| **Electron** | 支持 | 数据存储在主进程，所有窗口共享 |
| **Web / Standalone** | 不支持 | 降级到 `localStorage`，受浏览器同源策略限制，不同域名互相隔离 |

Web 环境下如需跨服务窗传递数据，建议：
- 通过 URL 参数传递
- 使用 `postMessage` 通信
- 后端接口中转
- 统一域名部署（同域 `localStorage` 可共享）

### openServerWindow(options)

打开其他服务窗。

```javascript
// 默认新标签页打开
window.umPCSdk.openServerWindow({
  windowsId: 'target-window-id',
  success: () => {
    console.log('打开成功');
  },
  fail: (err) => {
    if (err === '服务窗不存在或无权限') {
      alert('您没有权限访问该服务窗');
    } else {
      alert('打开失败: ' + err);
    }
  }
});

// 在当前窗口打开（切换当前页面）
window.umPCSdk.openServerWindow({
  windowsId: 'target-window-id',
  target: '_self',  // '_self' 当前窗口，'_blank' 新标签页（默认）
  success: () => console.log('打开成功'),
  fail: (err) => console.error('打开失败:', err)
});

// 传递自定义 URL 参数（跨域场景下向第三方服务窗透传业务数据）
window.umPCSdk.openServerWindow({
  windowsId: 'target-window-id',
  target: '_blank',
  customUrlParams: {
    projectNo: 'PJ20240601001',
    projectName: '某某建筑工程',
    projectCode: '4401002024ABC'
  }
});
```

**参数说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| windowsId | string | 是 | 服务窗ID |
| target | string | 否 | 打开方式：'_self' 当前窗口，'_blank' 新标签页（默认）|
| customUrlParams | object | 否 | 自定义 URL 查询参数，打开服务窗时自动附加到 URL（如 `{ projectNo: 'xxx', projectName: 'xxx' }`）|
| success | function | 否 | 打开成功回调 |
| fail | function | 否 | 打开失败回调，参数为错误信息 |

**`customUrlParams` 使用说明：**

当第三方服务窗与平台部署在不同域名时，浏览器 `localStorage` 同源策略会导致 `getData`/`setData` 无法共享数据。此时可通过 `customUrlParams` 在打开服务窗时把必要的业务 ID 透传到目标 URL。

```javascript
window.umPCSdk.openServerWindow({
  windowsId: 'third-party-service',
  customUrlParams: {
    projectNo: 'PJ20240601001',     // 项目编号
    projectName: '某某建筑工程',     // 项目名称
    projectCode: '4401002024ABC'    // 项目编码
  }
});
```

第三方页面从 URL 中解析即可：

```javascript
const params = new URLSearchParams(window.location.search);
const projectNo = params.get('projectNo');
const projectName = decodeURIComponent(params.get('projectName') || '');
```

### openLink(options)

打开链接。

```javascript
window.umPCSdk.openLink({
  url: 'https://example.com',
  target: '_blank'  // '_self' 或 '_blank'
});
```

### logout(options?)

退出登录。

```javascript
window.umPCSdk.logout({
  redirectUrl: '/login'
});
```

### 事件监听

监听 token 过期事件：

```javascript
window.umPCSdk.on('tokenExpired', () => {
  alert('登录已过期，请重新登录');
});
```

---

## 四、Vue3 完整示例

```vue
<template>
  <div class="user-profile">
    <h1>用户信息</h1>
    <p>用户名: {{ userInfo?.userName }}</p>
    <p>手机号: {{ userInfo?.phone }}</p>
    <button @click="openOtherWindow">打开其他服务窗</button>
    <button @click="logout">退出登录</button>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const userInfo = ref(null);

onMounted(async () => {
  try {
    userInfo.value = await window.umPCSdk.getUserInfo();
  } catch (err) {
    console.error('获取用户信息失败:', err);
  }
  
  window.umPCSdk.on('tokenExpired', () => {
    alert('登录已过期，请重新登录');
  });
});

const openOtherWindow = () => {
  window.umPCSdk.openServerWindow({
    windowsId: 'other-service-id',
    target: '_blank',  // 新标签页打开
    fail: (err) => alert(err)
  });
};

const logout = () => {
  window.umPCSdk.logout({ redirectUrl: '/login' });
};
</script>
```

---

## 五、常见问题

### Q1: 获取不到 Token？

**原因**：必须通过 web-pc 或 electron-im 打开，直接访问服务窗 URL 无法获取。

**解决**：确保从 web-pc 跳转（带 `?token=xxx`）或在 electron-im 中打开。

### Q2: 打开服务窗提示"服务窗不存在或无权限"？

**原因**：
- `windowsId` 拼写错误
- 用户没有该服务窗的访问权限

**解决**：核对服务窗ID，联系管理员确认权限。

### Q3: 如何调试？

开启调试模式：

```javascript
window.pcsdkConfig = { debug: true };
```

在浏览器控制台搜索 `[PCSDK]` 查看日志。

### Q4: 出现"CORS policy"跨域错误？

**错误信息：**
```
Access to fetch at 'http://gateway.com/yjy/...' from origin 'http://your-site.com'
has been blocked by CORS policy.
```

**原因：**
- 你的服务窗域名未添加到平台方 Gateway 的 CORS 白名单中
- 平台方需要你提供站点域名进行配置

**解决：**
1. 联系平台技术支持，提供服务窗域名（如 `https://your-service.com`）
2. 等待平台方配置完成后刷新页面测试
3. 开发环境可临时使用 `http://localhost:5173` 或 `http://localhost:5174`

**建议：** 优先采用同域部署（通过 Nginx 代理），避免跨域问题。

---

## 六、技术支持

如有问题，请联系：平台技术支持
