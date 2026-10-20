# 工程全周期工作台APP端SDK集成指南

> **面向对象**：工程全生命周期一体化平台移动端开发人员  
> **版本**：v1.9.0  
> **更新日期**：2026-09-02
>
> **更新说明**：
> - v1.4.0 字段命名与Web端SDK完全对齐——项目工作台 `projectCode`(条形码)/`gcbm`(国标码)/`projectSequence`(项目序列)，企业工作台 `enterpriseCode`(企业编号)/`enterpriseType`(企业类型)。
> - v1.5.0 明确区分同域/跨域接入方式，跨域部署无需引入 SDK，仅通过 URL 参数获取数据。
> - v1.6.0 项目工作台新增 `enterpriseName`（承包企业名称）、`positionName`（主岗位名称）。
> - v1.7.0 项目工作台新增 `creditCode`（企业信用代码）、`enterpriseCode`（企业编码）。
> - v1.8.0 `positionName` 支持多岗位——当前用户在当前项目担任多个岗位时，传递全部岗位名称（英文逗号拼接）。
> - v1.9.0 项目工作台新增 `projectCategory`（工程大类）。

---

## 一、先判断部署方式

第三方服务窗的接入方式取决于部署域名是否与平台**同源**。

| 部署方式 | 判定条件 | 数据获取方式 | 是否需要引入SDK |
|---------|---------|------------|---------------|
| **同域部署** | 第三方服务窗与平台使用相同域名 | `window.umSdk.getData(key)` | **需要** |
| **跨域部署** | 第三方服务窗与平台使用不同域名 | **URL 参数解析** | **不需要** |

### 同域/跨域判定基准

判定依据为第三方服务窗页面地址与工程全周期一体化平台页面地址是否同源（协议、域名、端口均相同）。平台域名如下：

| 环境 | 平台域名 |
|------|---------|
| 测试环境 | `http://183.6.48.123:30681` |
| 生产环境 | `http://portal.gz-szcj.com:8000` |

例如，第三方服务窗部署在 `http://183.6.48.123:30681/app/xxx` 属于同域；部署在 `http://third-party.com/xxx` 或 `http://183.6.48.123:30682/xxx` 属于跨域。

> **重要：** 跨域部署时 APP WebView 与 H5 之间的数据通道受限，SDK 无法读取平台数据，**不要引入 SDK**，直接从 URL 参数中解析即可。

---

## 二、同域部署：引入APP-SDK

仅同域部署需要引入 SDK。

### 通过script标签引入（UMD模式）

在HTML文件的 `<head>` 中引入SDK：

```html
<script src="http://sjt.gz-szcj.com:8000/appsdk/appsdk.min.js"></script>
```

### 在Vue3项目中动态加载

```typescript
// utils/appsdk.ts
export function loadAppSdk(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.umSdk) {
      resolve()
      return
    }
    const script = document.createElement('script')
    script.src = 'http://sjt.gz-szcj.com:8000/appsdk/appsdk.min.js'
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('APP-SDK加载失败'))
    document.head.appendChild(script)
  })
}
```

---

## 三、同域部署：通过 getData 读取

SDK挂载成功后，通过 `window.umSdk.getData(key)` 同步读取工作台数据。

### 3.1 项目工作台数据

**存储时机**：用户切换项目成功后自动写入  
**读取Key**：`projectWorkbench`

```javascript
const projectData = window.umSdk.getData('projectWorkbench')
console.log(projectData)
// {
//   projectCode: 'PJ20240601001',    // 项目编号(条形码)
//   projectName: '某某建筑工程',     // 项目名称
//   projectCategory: 'CONSTRUCTION', // 工程大类
//   projectSequence: 'CONSTRUCTION_GENERAL', // 项目序列
//   gcbm: '4401002024ABC',          // 项目编码(工程编码/国标码)
//   enterpriseName: '某某建设集团', // 当前用户在当前项目的承包企业名称
//   positionName: '项目经理,安全员', // 当前用户在当前项目的全部岗位名称（多岗位英文逗号拼接）
//   creditCode: '91440100MA5XXXXXX', // 企业信用代码
//   enterpriseCode: '10086'         // 企业编码
// }
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| projectCode | string | 项目编号，对应条形码 |
| projectName | string | 项目名称 |
| projectCategory | string | 工程大类：`CONSTRUCTION`-施工、`SUPERVISION`-监理、`TESTING`-检测、`MONITORING`-监测 |
| projectSequence | string | 项目序列 |
| gcbm | string | 工程编码（国标码） |
| enterpriseName | string | 当前用户在当前项目的承包企业名称 |
| positionName | string | 当前用户在当前项目的全部岗位名称，多岗位以英文逗号拼接（如 `项目经理,安全员`）；单岗位时仅一个岗位名 |
| creditCode | string | 统一社会信用代码 |
| enterpriseCode | string/number | 企业编码 |

**项目序列说明：**

| 序列名称 | 序列值 |
|------|------|
| 施工总承包序列 | CONSTRUCTION_GENERAL |
| 专业承包序列 | CONSTRUCTION_SPECIAL |
| 施工劳务序列 | CONSTRUCTION_LABOR |
| 监理综合资质 | SUPERVISION_GENERAL |
| 监理专业资质 | SUPERVISION_SPECIAL |
| 监理事务所资质 | SUPERVISION_OFFICE |
| 建设工程质量检测 | INSPECT |
| 工程监测 | MONITOR |

**使用示例：**

```vue
<script setup>
import { onMounted, ref } from 'vue'

const currentProject = ref(null)

onMounted(() => {
  // 读取当前项目信息
  currentProject.value = window.umSdk?.getData('projectWorkbench')
})
</script>

<template>
  <div v-if="currentProject">
    <p>当前项目：{{ currentProject.projectName }}</p>
    <p>项目编号：{{ currentProject.projectCode }}</p>
  </div>
</template>
```

### 3.2 企业工作台数据

**存储时机**：用户切换企业成功后自动写入  
**读取Key**：`enterpriseWorkbench`

```javascript
const enterpriseData = window.umSdk.getData('enterpriseWorkbench')
console.log(enterpriseData)
// {
//   enterpriseCode: '10086',              // 企业编号(条形码)
//   enterpriseName: '某某建设集团',      // 企业名称
//   enterpriseType: 'CONSTRUCTION',      // 企业类型
//   creditCode: '91440100MA5XXXXXX'     // 企业信用代码
// }
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| enterpriseCode | string/number | 企业编号 |
| enterpriseName | string | 企业名称 |
| enterpriseType | string | 企业类型：施工企业CONSTRUCTION、监理企业SUPERVISION、检测企业DETECTION、监测企业MONITORING |
| creditCode | string | 统一社会信用代码 |

**使用示例：**

```javascript
// 在提交表单时自动带上当前企业信息
function submitForm(data) {
  const enterprise = window.umSdk.getData('enterpriseWorkbench')
  return api.submit({
    ...data,
    enterpriseCode: enterprise?.enterpriseCode,
    enterpriseName: enterprise?.enterpriseName,
    enterpriseType: enterprise?.enterpriseType,
    creditCode: enterprise?.creditCode
  })
}
```

### 3.3 个人工作台数据

**存储时机**：进入个人中心成功获取个人信息后自动写入  
**读取Key**：`personalWorkbench`

```javascript
const personalData = window.umSdk.getData('personalWorkbench')
console.log(personalData)
// {
//   userId: 'U20240601001',   // 个人编号(条形码)
//   userName: '张三',          // 个人姓名
//   phone: '13800138000'      // 个人手机号
// }
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| userId | string | 个人编号，优先取条形码编码，无则取用户ID |
| userName | string | 个人姓名 |
| phone | string | 手机号 |

**使用示例：**

```javascript
// 在需要展示当前操作人信息的场景
function getOperatorInfo() {
  const user = window.umSdk.getData('personalWorkbench')
  return {
    operatorId: user?.userId,
    operatorName: user?.userName,
    operatorPhone: user?.phone
  }
}
```

---

## 四、跨域部署：通过 URL 参数获取

跨域部署时**不要引入 SDK**，平台在通过 `openLink` / `openServerWindow` 打开服务窗时，会自动将工作台数据附加到 URL 参数中。

### 4.1 URL 参数格式

打开服务窗时，URL 中自动附加 `token` 及当前工作台类型的对应参数。**不同工作台页面仅传递对应参数**，不会一次性传递全部三类数据。

#### 公共参数

| 参数名 | 说明 | 示例值 |
|--------|------|--------|
| `token` | 访问令牌 | `Bearer eyJhbG...` |

#### 项目工作台页面（仅传递项目参数）

| 参数名 | 说明 | 示例值 |
|--------|------|--------|
| `projectCode` | 项目编号（条形码） | `PJ20240601001` |
| `projectName` | 项目名称 | `某某建筑工程` |
| `projectCategory` | 工程大类 | `CONSTRUCTION` |
| `projectSequence` | 项目序列 | `CONSTRUCTION_GENERAL` |
| `gcbm` | 项目编码（国标码） | `4401002024ABC` |
| `enterpriseName` | 当前用户在当前项目的承包企业名称 | `某某建设集团` |
| `positionName` | 当前用户在当前项目的全部岗位名称（多岗位英文逗号拼接） | `项目经理,安全员` |
| `creditCode` | 企业信用代码 | `91440100MA5XXXXXX` |
| `enterpriseCode` | 企业编码 | `10086` |
| `userCode` | 当前登录用户编号（恒传；用户未同步时可能为空串） | `U20240601001` |
| `realName` | 当前登录用户实名姓名（**仅在已完成实名认证时传递**，未实名不出现该参数） | `张三` |

#### 企业工作台页面（仅传递企业参数）

| 参数名 | 说明 | 示例值 |
|--------|------|--------|
| `enterpriseCode` | 企业编号 | `10086` |
| `enterpriseName` | 企业名称 | `某某建设集团` |
| `enterpriseType` | 企业类型 | `CONSTRUCTION` |
| `creditCode` | 企业信用代码 | `91440100MA5XXXXXX` |
| `userCode` | 当前登录用户编号（恒传；用户未同步时可能为空串） | `U20240601001` |
| `realName` | 当前登录用户实名姓名（**仅在已完成实名认证时传递**，未实名不出现该参数） | `张三` |

> **兼容性要求（PROJ-20260831-001 起生效）**：第三方应用必须容忍 `userCode` 为空串、`realName` 参数缺失的场景，不得将其作为必填校验项；如需强制实名，应自行校验 `realName` 是否存在并给出业务提示。

#### 个人工作台页面（仅传递个人参数）

| 参数名 | 说明 | 示例值 |
|--------|------|--------|
| `userId` | 个人编号 | `U20240601001` |
| `userName` | 个人姓名 | `张三` |
| `phone` | 手机号 | `13800138000` |

### 4.2 获取方式（JavaScript）

第三方页面根据**自身业务场景**选择读取对应参数。若明确只在项目工作台打开，则只读项目参数：

```javascript
const params = new URLSearchParams(window.location.search)

// 项目工作台场景
const projectData = {
  projectCode: params.get('projectCode'),
  projectName: params.get('projectName'),
  projectCategory: params.get('projectCategory'),
  projectSequence: params.get('projectSequence'),
  gcbm: params.get('gcbm'),
  enterpriseName: params.get('enterpriseName'),
  positionName: params.get('positionName'),
  creditCode: params.get('creditCode'),
  enterpriseCode: params.get('enterpriseCode')
}

// 企业工作台场景
const enterpriseData = {
  enterpriseCode: params.get('enterpriseCode'),
  enterpriseName: params.get('enterpriseName'),
  enterpriseType: params.get('enterpriseType'),
  creditCode: params.get('creditCode')
}

// 个人工作台场景
const personalData = {
  userId: params.get('userId'),
  userName: params.get('userName'),
  phone: params.get('phone')
}
```

### 4.3 获取方式（Vue3）

```vue
<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const projectInfo = ref({})
const enterpriseInfo = ref({})
const personalInfo = ref({})

onMounted(() => {
  // 从 URL 查询参数获取
  projectInfo.value = {
    projectCode: route.query.projectCode,
    projectName: route.query.projectName,
    projectCategory: route.query.projectCategory,
    projectSequence: route.query.projectSequence,
    gcbm: route.query.gcbm,
    enterpriseName: route.query.enterpriseName,
    positionName: route.query.positionName,
    creditCode: route.query.creditCode,
    enterpriseCode: route.query.enterpriseCode
  }
  enterpriseInfo.value = {
    enterpriseCode: route.query.enterpriseCode,
    enterpriseName: route.query.enterpriseName,
    enterpriseType: route.query.enterpriseType,
    creditCode: route.query.creditCode
  }
  personalInfo.value = {
    userId: route.query.userId,
    userName: route.query.userName,
    phone: route.query.phone
  }
})
</script>
```

### 4.4 封装为工具函数（推荐）

```typescript
// utils/workbench.ts

export interface ProjectData {
  projectCode: string
  projectName: string
  projectCategory: string
  projectSequence: string
  gcbm: string
  enterpriseName: string
  positionName: string
  creditCode: string
  enterpriseCode: string
}

export interface EnterpriseData {
  enterpriseCode: string
  enterpriseName: string
  enterpriseType: string
  creditCode: string
}

export interface PersonalData {
  userId: string
  userName: string
  phone: string
}

/**
 * 从 URL 获取工作台数据（跨域场景）
 */
export function getWorkbenchDataFromUrl() {
  const urlParams = new URLSearchParams(window.location.search)

  return {
    project: {
      projectCode: urlParams.get('projectCode') || '',
      projectName: urlParams.get('projectName') || '',
      projectCategory: urlParams.get('projectCategory') || '',
      projectSequence: urlParams.get('projectSequence') || '',
      gcbm: urlParams.get('gcbm') || '',
      enterpriseName: urlParams.get('enterpriseName') || '',
      positionName: urlParams.get('positionName') || '',
      creditCode: urlParams.get('creditCode') || '',
      enterpriseCode: urlParams.get('enterpriseCode') || ''
    } as ProjectData,
    enterprise: {
      enterpriseCode: urlParams.get('enterpriseCode') || '',
      enterpriseName: urlParams.get('enterpriseName') || '',
      enterpriseType: urlParams.get('enterpriseType') || '',
      creditCode: urlParams.get('creditCode') || ''
    } as EnterpriseData,
    personal: {
      userId: urlParams.get('userId') || '',
      userName: urlParams.get('userName') || '',
      phone: urlParams.get('phone') || ''
    } as PersonalData
  }
}
```

使用示例：

```typescript
import { getWorkbenchDataFromUrl } from '@/utils/workbench'

const { project, enterprise, personal } = getWorkbenchDataFromUrl()

// 提交表单时自动带上当前上下文
function submitForm(formData: any) {
  return api.submit({
    ...formData,
    projectCode: project.projectCode,
    projectName: project.projectName,
    enterpriseCode: enterprise.enterpriseCode,
    enterpriseName: enterprise.enterpriseName,
    operatorId: personal.userId,
    operatorName: personal.userName
  })
}
```

### 4.5 重要说明

**按工作台类型精确传递**：
- 项目工作台页面打开的服务窗，URL 中仅包含 `projectCode`、`projectName`、`projectCategory`、`projectSequence`、`gcbm`、`enterpriseName`、`positionName`、`creditCode`、`enterpriseCode`
- 企业工作台页面打开的服务窗，URL 中仅包含 `enterpriseCode`、`enterpriseName`、`enterpriseType`、`creditCode`
- 个人工作台页面打开的服务窗，URL 中仅包含 `userId`、`userName`、`phone`

**跨域场景无需引入 SDK**：APP WebView 与 H5 跨域时数据通道受限，SDK 无法读取平台数据，直接解析 URL 参数即可。

**始终传递最新数据**：用户切换项目或企业后，再次打开服务窗时，URL 参数会自动更新为当前选中的最新工作台数据。第三方页面每次被打开时都应重新解析 URL 参数，而非缓存旧值。

**仅传递初始快照**：URL 参数仅在打开服务窗时传递一次。如果用户在我方平台内切换项目/企业后，已打开的第三方页面不会自动收到更新。如需最新数据，需重新打开服务窗。

---

## 五、完整示例

### Vue3 组合式 API 示例（同域场景）

```vue
<template>
  <div class="workbench-info">
    <van-cell-group title="当前上下文">
      <van-cell title="当前项目" :value="projectInfo?.projectName || '未选择'" />
      <van-cell title="项目编号" :value="projectInfo?.projectCode || '-'" />
      <van-cell title="当前企业" :value="enterpriseInfo?.enterpriseName || '未选择'" />
      <van-cell title="企业信用代码" :value="enterpriseInfo?.creditCode || '-'" />
      <van-cell title="当前用户" :value="personalInfo?.userName || '-'" />
    </van-cell-group>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface ProjectWorkbenchData {
  projectCode: string
  projectName: string
  projectCategory: string
  projectSequence: string
  gcbm: string
  enterpriseName: string
  positionName: string
  creditCode: string
  enterpriseCode: string
}

interface EnterpriseWorkbenchData {
  enterpriseCode: string
  enterpriseName: string
  enterpriseType: string
  creditCode: string
}

interface PersonalWorkbenchData {
  userId: string
  userName: string
  phone: string
}

const projectInfo = ref<ProjectWorkbenchData | undefined>()
const enterpriseInfo = ref<EnterpriseWorkbenchData | undefined>()
const personalInfo = ref<PersonalWorkbenchData | undefined>()

onMounted(() => {
  if (window.umSdk) {
    projectInfo.value = window.umSdk.getData('projectWorkbench')
    enterpriseInfo.value = window.umSdk.getData('enterpriseWorkbench')
    personalInfo.value = window.umSdk.getData('personalWorkbench')
  }
})
</script>
```

### Vue3 组合式 API 示例（跨域场景）

```vue
<template>
  <div class="workbench-info">
    <van-cell-group title="当前上下文">
      <van-cell title="当前项目" :value="projectInfo.projectName || '未选择'" />
      <van-cell title="项目编号" :value="projectInfo.projectCode || '-'" />
      <van-cell title="当前企业" :value="enterpriseInfo.enterpriseName || '未选择'" />
      <van-cell title="企业信用代码" :value="enterpriseInfo.creditCode || '-'" />
      <van-cell title="当前用户" :value="personalInfo.userName || '-'" />
    </van-cell-group>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

interface ProjectData {
  projectCode: string
  projectName: string
  projectCategory: string
  projectSequence: string
  gcbm: string
  enterpriseName: string
  positionName: string
  creditCode: string
  enterpriseCode: string
}

interface EnterpriseData {
  enterpriseCode: string
  enterpriseName: string
  enterpriseType: string
  creditCode: string
}

interface PersonalData {
  userId: string
  userName: string
  phone: string
}

const projectInfo = ref<ProjectData>({ projectCode: '', projectName: '', projectCategory: '', projectSequence: '', gcbm: '', enterpriseName: '', positionName: '', creditCode: '', enterpriseCode: '' })
const enterpriseInfo = ref<EnterpriseData>({ enterpriseCode: '', enterpriseName: '', enterpriseType: '', creditCode: '' })
const personalInfo = ref<PersonalData>({ userId: '', userName: '', phone: '' })

onMounted(() => {
  const urlParams = new URLSearchParams(window.location.search)

  projectInfo.value = {
    projectCode: urlParams.get('projectCode') || '',
    projectName: urlParams.get('projectName') || '',
    projectCategory: urlParams.get('projectCategory') || '',
    projectSequence: urlParams.get('projectSequence') || '',
    gcbm: urlParams.get('gcbm') || '',
    enterpriseName: urlParams.get('enterpriseName') || '',
    positionName: urlParams.get('positionName') || '',
    creditCode: urlParams.get('creditCode') || '',
    enterpriseCode: urlParams.get('enterpriseCode') || ''
  }

  enterpriseInfo.value = {
    enterpriseCode: urlParams.get('enterpriseCode') || '',
    enterpriseName: urlParams.get('enterpriseName') || '',
    enterpriseType: urlParams.get('enterpriseType') || '',
    creditCode: urlParams.get('creditCode') || ''
  }

  personalInfo.value = {
    userId: urlParams.get('userId') || '',
    userName: urlParams.get('userName') || '',
    phone: urlParams.get('phone') || ''
  }
})
</script>
```

---

## 六、环境兼容性说明

| 环境 | 数据获取方式 | 说明 |
|------|------------|------|
| **APP WebView（同域）** | `window.umSdk.getData` | SDK 直接读取存储的数据 |
| **APP WebView（跨域）** | URL 查询参数 | 平台自动附加参数到服务窗地址，按工作台类型传递，**无需引入 SDK** |
| **外部浏览器** | 参数为空 | 直接打开URL无参数，需手动处理降级 |

**注意：**
- 同域部署优先使用 `getData`，数据可实时同步更新。
- 跨域场景**不要引入 SDK**，必须使用 URL 参数方式，且**仅传递当前工作台类型对应的参数**。
- 用户切换项目/企业后，重新打开服务窗会获取最新数据。
- 不同 WebView 实例之间的数据不共享（如通过 `openLink` 打开新页面）。

---

## 七、常见问题

### Q1: getData返回undefined？

**原因**：
1. 尚未切换过项目/企业，或未访问过个人中心
2. 跨域部署下使用了 `getData`（应改用 URL 参数）
3. 不在APP WebView环境运行（直接打开服务窗URL）
4. 同域部署下未正确引入 SDK

**排查**：
```javascript
// 检查SDK是否存在
console.log('SDK存在:', !!window.umSdk)
// 检查所有存储的key
console.log('项目数据:', window.umSdk?.getData('projectWorkbench'))
console.log('企业数据:', window.umSdk?.getData('enterpriseWorkbench'))
console.log('个人数据:', window.umSdk?.getData('personalWorkbench'))
```

### Q2: URL参数为空？

**原因**：
1. 直接打开第三方服务窗 URL，未通过统一门户跳转
2. 工作台数据尚未加载（未访问过项目/企业/个人工作台）
3. 参数名拼写错误
4. 跨域部署时未通过 `openLink` / `openServerWindow` 打开服务窗

**排查**：
```javascript
// 打印完整 URL 和参数
console.log('当前URL:', window.location.href)
console.log('项目编号:', new URLSearchParams(window.location.search).get('projectCode'))
console.log('企业编号:', new URLSearchParams(window.location.search).get('enterpriseCode'))
console.log('用户ID:', new URLSearchParams(window.location.search).get('userId'))
```

### Q3: 如何在非Vue页面中使用？

```html
<script>
// 普通HTML页面（同域）
window.addEventListener('load', function() {
  if (window.umSdk) {
    var project = window.umSdk.getData('projectWorkbench')
    document.getElementById('projectName').textContent = project?.projectName || '未选择'
  }
})

// 普通HTML页面（跨域）
window.addEventListener('load', function() {
  var urlParams = new URLSearchParams(window.location.search)
  var projectName = urlParams.get('projectName')
  document.getElementById('projectName').textContent = projectName || '未选择'
})
</script>
```

### Q4: 中文参数乱码？

URL 参数中的中文会自动进行 URL 编码，获取时自动解码：

```javascript
const urlParams = new URLSearchParams(window.location.search)
// projectName=%E6%9F%90%E6%9F%90%E5%BB%BA%E7%AD%91%E5%B7%A5%E7%A8%8B
const projectName = urlParams.get('projectName') // "某某建筑工程"
```

`URLSearchParams` 会自动处理解码，无需手动 `decodeURIComponent`。

### Q5: 数据何时更新？

| 数据类型 | 更新触发时机 |
|---------|------------|
| 项目工作台 | 用户执行"切换项目"操作成功后 |
| 企业工作台 | 用户执行"切换企业"操作成功后 |
| 个人工作台 | 用户进入个人中心页面并获取信息后 |

> **注意**：URL 参数方式不会自动更新，仅在打开页面时传递一次快照。

---

## 八、技术支持

如有问题，请联系：平台技术支持
