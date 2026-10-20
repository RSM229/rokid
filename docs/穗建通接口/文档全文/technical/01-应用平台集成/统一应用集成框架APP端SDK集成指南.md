## 概述
sdk是统一应用集成APP面向APP内嵌H5页面开发者提供的基于APP内的网页开发工具包。

（特别注意：调试必须在APP内进行，否则SDK不生效）

通过使用sdk，网页开发者可借助APP高效的使用获取用户信息、打开新页面、修改导航栏等功能。


## umSdk使用方法
### 步骤一：引入JS文件
## Esm模式
如vite + vue3 +ts 

1.  npm install zkjh-app-sdk 或 yarn add zkjh-app-sdk 或 pnpm add zkjh-app-sdk

2. main.ts引入，如下图

<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568559615-220c71d3-2ad0-41dc-810d-885ebf052996.png>)

### Umd模式
在需要调用JS接口的页面引入如下JS文件，（支持https）：

[http://sjt.gz-szcj.com:8000/appsdk/appsdk.min.js](http://sjt.gz-szcj.com:8000/appsdk/appsdk.min.js)

> **注意**：UMD 模式下 `window.umSdk` 为 SDK 实例（可直接调用方法），`window.AppSdk` 为类构造函数（用于手动创建实例）。通常直接使用 `umSdk.xxx()` 即可。

### 步骤二：通过config接口注入权限验证配置
所有需要使用sdk的页面必须先注入配置信息，否则将无法调用。
```javascript
umSdk.config()
```

### 步骤三：通过ready接口处理成功验证
```javascript
umSdk.ready(
  function(bridge){
    // 初始化之后就可以使用umSdk 的方法了
    // eg:获取token 
    umSdk.getToken({
      success:function(res) {
        alert(res.data.token) 
      }, 
  })
})
```

#### _接口调用说明_
所有接口通过umSdk对象来调用，参数是一个对象，除了每个接口本身需要传的参数之外，还有以下通用参数：

success：接口调用成功时执行的回调函数。

fail：接口调用失败时执行的回调函数。

所有接口返回的数据格式参考下面

```javascript
{
  "status":"success",
  "msg":"成功",
  "data":{
    "token":"123456"
  }
}
```

#### _开发者也可以使用promise封装异步请求_
```javascript
function load() {
  return new Promise((resolve, reject) => { 
    umSdk.config({
      id: 20220109 
    }) 
    umSdk.ready(function(bridge) {
      resolve(bridge) 
    }) 
  })
}

function getToken() {
  return new Promise((resolve, reject) => { 
    umSdk.getToken({
      success: function(res) {
        resolve(res.data.token) 
      },
      fail: function() {
        reject() 
      } 
    }) 
  })
}

async function main() {
  await load()
  const token = await getToken()
}
```



## 用户相关
### 获取用户token信息
用户token是当前登录用户唯一识别号，用于接口访问

```javascript
umSdk.getToken({
  success: function(res) {
    console.log(res.data.token) 
  },
  fail: function() {}
})
```



### 获取用户信息
```javascript
umSdk.getSessionStorage({
  success: function(res) {
    console.log(res.data.tokenId) 	// 用户token
    console.log(res.data.userName) 	// 用户名
    console.log(res.data.mobile) 		// 用户手机号
    console.log(res.data.eid) 			// 用户当前服务ID
		console.log(res.data.baseUrl) 	// 当前后端API请求地址前缀
			console.log(res.data.userId) 		// 用户ID
			console.log(res.data.platform) 	// 平台类型：pc 或 mobile
  },	
  fail: function(err) {}
})
```



### 获取登录凭证
从url的query中获取loginCredantial，JS示例代码如下

```javascript
const urlParams = new URLSearchParams(window.location.search);
const loginCredentials = urlParams.get('loginCredentials');
```



### 刷新token
```javascript
umSdk.reStartLogin({
  success: function(res) {
    console.log(res.data.token) // 刷新后的token 
  },
  fail: function(err) {}
})
```



### 退出登录
调用App退出当前用户账户至登录页

```javascript
umSdk.logout({
  success: function() {},
  fail: function(err) {}
})
```



## 头部功能栏
集成框架中，最核心的UI操作界面就是APP登录后的服务窗上面的"头部功能栏"这是唯一框架UI交互的区域。

1. "头部功能栏"可由集成的系统，通过SDK方法，进行样式设定（显影、背景颜色、图片、已有按钮的开关等）
2. "头部功能栏"可实现webview内H5内部跳转的自动感知history，并自动判断显示返回。
3. "头部功能栏"可由框架级全局配置参数 "基础配置—子级页面功能栏默认显示"控制是否显示，默认为显示，不显示时，完全交由三方脚本SDK方式控制。
4. 配置权限的级别： jsSDK 高于 默认配置。第三方页面在不调用sdk 时 完全使用配置参数。在此基础上，第三方通过sdk 进行参数修改从而影响UI交互元素。



框架提供三种应用模式，具体适应场景如下：

### 1. 完整展示模式
<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568559830-66922860-7480-45ef-9095-0848e6ba1fe9.png>)<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568559779-be591f03-1fb0-4973-9890-a9465dce69c0.png>)



● 此应用模式为平台默认的应用模式。此时在后台配置"基础配置—子级页面功能栏默认显示"为 True。

● 当服务窗的入口页面（一级页面）打开时，默认展示所有功能按钮，效果为：应用模式一（一级）。

● 当内部链接跳转到二级页面或子级页面时，功能条的左侧 "<"返回按钮显示（有history时），效果为：应用模式一（子级）。

● 此时应用设置参数对应为：type=1，hidden=0

### 2. 最简展示模式
<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568559921-16c644fd-bba8-4ed9-8eb6-59d1bbca69b5.png>)<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568559875-326e1199-6864-4ab0-afd8-552c874d2a9b.png>)



● 此应用模式需要在后台配置"基础配置—子级页面功能栏默认显示"为 False。

● 当服务窗的入口页面（一级页面）打开时，默认展示所有功能按钮，效果为：应用模式二（一级）。

● 当内部链接跳转到二级页面或子级页面时，功能条的左侧 "<"返回按钮显示（有history时），效果为：应应用模式二（子级）。

● 此时应用设置参数对应为：type=2，hidden=0

### 3. 完全隐藏模式
<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568560030-26a27b9b-1aee-46b6-b361-710b9624fda1.png>)<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568560212-bee9d263-27d2-4ad1-ad7f-81719af4e6b5.png>)

● 此模式适用于完全交由第三方进行开发，头部功能栏完全隐藏，全部UI区域交由业务系统，此时一定注意UI设计时需要能够利用SDK方法返回平台主页。

● 提供SDK脚本方法，包括"切换服务窗"、"返回"两个基础方法。

● 此时应用设置参数对应为：hidden=1 （此时 type和其它参数设置无效）

## webview相关
### 修改导航栏
在当前页面调用，修改当前页面导航栏样式。

```javascript
umSdk.setNavigation({
  hidden:0,// 0显示导航栏 1隐藏导航栏，默认显示
  type:1,// 导航栏类型（1:一级导航，2:二级导航）默认：1
  title:"",// 导航栏标题
  titleColor:"#ff00ff",// 导航栏标题颜色
  fontSize:"17",// 导航栏标题文字字号
  backgroundColor:"#ff00ff",// 导航栏背景颜色
  backgroundImage:"http://.png",// 导航栏背景图（和背景颜色只生效一个）
  itemColor:"#ff00ff",// 导航栏图标颜色
  success:function() {},
  fail:function(err) {}
})
```

其中 type 参数 为控制应用模式的参数设置，一般情况无需设置，采用后端平台默认配置。

hidden=0 参数 为开启应用模式三时需要传入。

其它参数均为头部功能栏的样式控制参数。



### 切换服务窗
```javascript
umSdk.openServerWindow({
  windowsId: string,         // 要跳转的服务窗ID，统一框架后台服务窗配置可查看
  closeAlert: boolean,       // 是否关闭默认的Alert提示
  customUrlParams: object,   // 自定义URL参数，键值对会附加到目标服务窗地址（详见下方说明）
  customCallBack: {
    case1: {
            success:(...args:any[])=>void,
            fail:(...args:any[])=>void
          },// 情景1："直接进入服务窗"回调
    case2: {
            success:(...args:any[])=>void,
            fail:(...args:any[])=>void
          },// 情景2："需要申请加入"回调
    case3: {
          success:(...args:any[])=>void,
          fail:(...args:any[])=>void
          },// 情景3："弹窗配置文案"回调
    case4: {
          success:(...args:any[])=>void,
          fail:(...args:any[])=>void
          }// 情景4："非服务窗用户不显示"回调
  }// 自定义CallBack处理
})
```



通过SDK脚本方法 openServerWindow（windowid）可以实现第三方页面切换服务窗的具体功能。

此功能多用于开发"导航页"。

内部功能实现逻辑解释：

1、根据提供的目标windowId，从当前服务窗直接跳转到目标服务窗（一级）

2、目标服务窗打开时，根据目标服务窗的后台配置，进行应用模式参数的设置"type=2，hidden=0"，从后台配置中读取头部功能栏的样式配置参数并设置。

3、如果窗口页面需要二次开发动态定义"头部功能栏"的样式，则可以调用umSdk.setNavigation（）方法，传入相关参数设置

```javascript
// 示例
umSdk.openServerWindow({
  windowsId:"1933117927250898946",
})
```



参数说明：
### 
closeAlert：在服务窗属于第三种情况时，默认会alert提示文案，开发者可以结合closeAlert和customCallBack 对以上4种场景做自定义回调。

### customUrlParams 自定义URL参数（v1.3.0+）

当需要向目标服务窗传递业务数据（如当前工作台上下文）时，可通过 `customUrlParams` 传入自定义键值对。SDK 会自动将这些参数附加到目标服务窗的跳转地址中，第三方页面通过 URL 查询参数即可获取。

**适用场景**：
- 项目工作台打开第三方服务窗时传递 `projectNo`、`projectName`、`projectCode`
- 企业工作台打开第三方服务窗时传递 `enterpriseNo`、`enterpriseName`、`creditCode`
- 个人工作台打开第三方服务窗时传递 `userId`、`userName`、`phone`
- 其他任何需要在服务窗间传递的自定义业务参数

**使用示例**：

```javascript
umSdk.openServerWindow({
  windowsId: "1933117927250898946",
  customUrlParams: {
    projectNo: "PJ20240601001",
    projectName: "某某建筑工程",
    projectCode: "4401002024ABC"
  }
})
```

**第三方页面接收参数**：

```javascript
const urlParams = new URLSearchParams(window.location.search)
const projectNo = urlParams.get('projectNo')
const projectName = urlParams.get('projectName')
const projectCode = urlParams.get('projectCode')
```

**注意事项**：
1. 参数值会自动进行 URL 编码，第三方页面无需手动 `encodeURIComponent`
2. 空字符串、`undefined`、`null` 值会被自动过滤，不会附加到 URL
3. 参数仅在打开服务窗时传递一次快照，已打开的页面不会自动收到更新
4. 若目标服务窗与当前页面跨域，此方式为唯一可靠的数据传递途径

### 打开新H5页面
```javascript
umSdk.openLink({
  title: "",// H5页面的标题
  url: "",// H5页面地址
  isHome: false,// 是否系统H5最底层页面，默认false
  isFunc: 0,// 0业务页面 1服务窗，默认0
  success: function() {},
  fail: function(err) {}
})
```

### 返回上级页面
```javascript
umSdk.preRefresh({
  type: 0,// 0刷新 1不刷新
  success: function() {},
  fail: function(err) {}
})
```

● type默认0即返回上一页

## 权限相关
### 获取手机权限
获取系统相关功能前，请先获取相关功能权限；例如获取手机相册照片，请先获取相册权限；

```javascript
// 相册权限（iOS）
umSdk.getAlbumAuth({
  success: function() {},
  fail: function(err) {}
})
// 相机权限
umSdk.getCameraAuth({
  success: function() {},
  fail: function(err) {}
})
// 麦克风权限
umSdk.getMicrophoneAuth({
  success: function() {},
  fail: function(err) {}
})
// 日历权限
umSdk.getCalendarsAuth({
  success: function() {},
  fail: function(err) {}
})
// 蓝牙权限
umSdk.getBluetoothAuth({
  success: function() {},
  fail: function(err) {}
})
// 通讯录权限
umSdk.getAddressBook({
  success: function() {},
  fail: function(err) {}
})
// 定位权限
umSdk.getLocationAuth({
  success: function() {},
  fail: function(err) {}
})
```



## 存储相关
### 原生暂存 H5 数据		
```javascript
umSdk.setH5Data({ 
  key: "app_name", 
  value: "张三"
})
```

### 获取原生暂存 H5 数据
```javascript
const res = await umSdk.getH5Data("app_name")
console.log('获取数据的内容',res.data.value) // 获取数据的内容
```



## 地图相关
### 调用导航
```javascript
umSdk.navigationto({
  type: "1",// 坐标类型"1":高德,"2":百度,"3":腾讯
  longitude: "",// 经度
  latitude: "",// 纬度
  address: "",// 目的地描述
  success: function() {},
  fail: function(err) {}
})
```



### 获取位置信息（新）
WGS84：为一种大地坐标系，也是目前广泛使用的GPS全球卫星定位系统使用的坐标系。

GCJ02：又称火星坐标系，是由中国国家测绘局制定的地理坐标系统，是由WGS84加密后得到的坐标系。

BD09：为百度坐标系，在GCJ02坐标系基础上再次加密。其中bd09ll表示百度经纬度坐标，bd09mc表示百度墨卡托米制坐标。

umSdk 返回的是BD09，即百度坐标系

```javascript
umSdk.getLocation({
  success: function(res) {
    console.log(res.data.longitude) // 经度
    console.log(res.data.latitude) // 纬度
    console.log(res.data.name) // 地点名
    console.log(res.data.suggestion) // 定位提示信息 
  },
  fail: function(err) {}
})
```



### 获取位置信息（旧）
```javascript
umSdk.mapGetLocation({
  success: function(res) {
    console.log(res.data.longitude) // 经度
    console.log(res.data.latitude) // 纬度
    console.log(res.data.name) // 地点名
    console.log(res.data.suggestion) // 定位提示信息 
  },
  fail: function(err) {}
})
```



### 虚拟定位
```javascript
umSdk.getIsVirtualPositioning({
  success: function(res) {
    console.log(res.data.isVirtualPositioning) // 是否虚拟定位：1、虚拟定位，0、真实定位
    console.log(res.data.latitude) // 纬度
    console.log(res.data.longitude) // 经度 
  },
  fail: function(err) {}
})
```



## 图片处理
### 显示大图（图片预览）
点击查看图片大图，调用App原生查看大图插件；

```javascript
umSdk.showPhotos({
  imgs: [],	// 图片地址数组，必填
  index: 2,	// 从第几个开始浏览，必填
  success: function() {},
  fail: function(err) {}
})
```



### 保存图片
图片保存至手机相册，需要提前获取相册权限

```javascript
umSdk.savePhotoToAlbum({
  downloadurl: "http://xxxx",	// 图片的服务器地址
  base64data: "",// 图片base64
  isAllowRepeat: false,// 可选，默认false。true时同URL重复下载每次生成新图库条目；false时同名覆盖，图库只保留一张
  success: function() {},
  fail: function(err) {}
})
```



### 调起拍照（takePhoto）
直接调起系统相机拍照，返回本地文件路径，不走上传流程。适用于需要获取拍照原图路径后自行处理的场景。

```javascript
umSdk.takePhoto({
  success: function(res) {
    // res.filePath 为拍照后的本地文件绝对路径
    console.log(res.filePath)
  },
  fail: function(err) {}
})
```

**参数说明**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| success | Function | 否 | 拍照成功回调，参数 `res` 包含 `filePath`（本地文件路径）|
| fail | Function | 否 | 拍照失败回调，参数 `err` 为错误信息 |

**返回值说明**

success 回调返回对象：

| 字段 | 类型 | 说明 |
|------|------|------|
| filePath | String | 拍照后图片的本地绝对路径（如 `/storage/emulated/0/.../xxx.jpg`）|

**与 `fileUpload` 的区别**

| 特性 | `takePhoto` | `fileUpload` |
|------|-------------|--------------|
| 是否调起相机 | 是 | 是（作为选项之一） |
| 是否自动上传 | 否 | 是 |
| 返回内容 | 本地文件路径 | 服务端返回的上传结果 |
| 适用场景 | 需要自行处理图片（如本地预览、自定义上传） | 直接上传文件到服务端 |

**注意事项**

1. 调用前需确保 App 已获得相机和存储权限，未授权时会自动弹出权限申请
2. Android 10（API 29）及以上版本受分区存储限制，返回的路径可能位于应用私有目录
3. H5 拿到 `filePath` 后可直接用于 `<img>` 标签展示，或自行调用上传接口



## 其他功能
### 拨打电话
```javascript
umSdk.call({
  phone:"13015588990",// 电话号码
  success: function() {},
  fail: function(err) {}
})
```



### 文件上传
```javascript
umSdk.fileUpload({
  uploaditem: [],// 可选择项 1:拍照 2:从相册中选择 3:文件 4:拍照（支持长按转拍视频）注意：1和4不能同时传
  uploadurl: "",// 文件上传后端接口地址，文件流接收
  itemproperty: {},// key 为 2时：maximagescount代表选择图片时的最大数量
  compress: {// 可选：原生压缩配置，仅对图片生效（视频/文件原样上传），不传则与旧版行为一致
    enable: true,// 是否启用原生压缩，默认 false
    ignorekb: 100// 小于该大小(KB)的图片跳过压缩，默认 100
  },
  success: function() {},//注意：由于ios和安卓处理机制不同返回参数格式ios返回对象，安卓返回json字符串，H5需判断兼容一下
  fail: function(err) {}
})
```

**compress 参数说明**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| enable | Boolean | 否 | 是否启用原生压缩，默认 `false`（不压缩，与旧版行为一致）|
| ignorekb | Number | 否 | 小于该大小（KB）的图片跳过压缩，默认 `100` |

**注意事项**

1. 压缩在原生侧（Android）完成，基于 Luban 算法，压缩后再上传，可显著降低上传耗时与带宽占用，适合现场大批量拍照场景
2. 仅图片参与压缩；视频、普通文件不受影响，按原样上传
3. 压缩失败的单张图片自动回退为原图上传，不影响整体上传流程
4. 大批量图片压缩为顺序执行（每张约 100~300ms），选择图片过多时压缩阶段会有等待
5. 该能力需 Android 端升级至支持版本；iOS 端暂不支持，双端混用时注意行为差异



### 文件预览
用网页打开预览的文件，支持word/excel/ppt/txt/png/jpg等文件格式

```javascript
umSdk.scanFile({
  downloadurl:"",// 文件预览网络地址
  filename:"",// 文件名（需要携带文件后缀）
  success: function() {},
  fail: function(err) {}
})
```

### 文件下载（fileDownLoadNew）
下载文件到本地，支持进度回调。下载完成后自动扫描到系统媒体库。

```javascript
umSdk.fileDownLoadNew({
  downLoadUrl: "https://example.com/file.pdf",  // 文件下载地址，必填
  success: function(res) {
    console.log(res.data.filePath)  // 下载后的本地文件绝对路径
    console.log(res.data.fileName)  // 文件名
  },
  fail: function(err) {
    console.log(err.msg)  // 错误信息，如"下载地址有误"、"请授予存储权限"、"下载失败"
  }
})
```

**参数说明**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| downLoadUrl | String | 是 | 文件的网络下载地址 |
| success | Function | 否 | 下载成功回调 |
| fail | Function | 否 | 下载失败回调 |

**返回值说明**

success 回调返回对象：

| 字段 | 类型 | 说明 |
|------|------|------|
| filePath | String | 下载后文件的本地绝对路径（如 `/storage/emulated/0/Download/xxx.pdf`）|
| fileName | String | 文件名 |

**与 `scanFile` 的区别**

| 特性 | `fileDownLoadNew` | `scanFile` |
|------|-------------------|------------|
| 功能 | 下载文件到本地 | 在线预览文件 |
| 文件存储 | 保存到设备本地 | 不保存，仅预览 |
| 返回内容 | 本地文件路径 + 文件名 | 无 |
| 适用场景 | 需要保存文件到设备 | 仅查看文件内容 |

**注意事项**

1. 调用前需确保 App 已获得存储权限，未授权时会自动弹出权限申请
2. Android 10（API 29）及以上版本受分区存储限制，文件会保存到应用公共下载目录
3. 下载完成后文件会自动扫描到系统媒体库，可在文件管理器中查看
4. 下载过程中会显示加载提示，下载完成后自动关闭



### 扫描二维码
调用App扫描二维码页面，返回扫描二维码结果字符串

```javascript
umSdk.scanCodeCard({
  isShowAlbum: false,// 可选，默认false。true时扫码页显示相册入口，支持从相册选择二维码
  success: function(res) {
    console.log(res.data.scanresult) // 扫描出的URL结果 
  },
  fail: function(err) {}
})
```



### NFC 读标签（巡检场景）
调用手机 NFC 读取巡检标签。三接口配合：先 `checkNfcEnable` 查询状态，再 `startNfcScan` 开启读卡（一次读取，读到即回调并自动关闭），离开页面或取消时调 `stopNfcScan` 兜底。

> 注意：Android Beam 已废弃，本 SDK 仅支持读 NFC 标签，不支持手机对手机传输；读卡仅前台生效，页面销毁自动关闭。

```javascript
// 1. 查询 NFC 状态（不支持/未开启走 fail，具体原因在 fail 回调第二个参数）
umSdk.checkNfcEnable({
  success: function(res) { console.log(res) }, // NFC已开启，可以执行后续操作
  fail: function(msg, reason) { console.log(reason) } // 该设备不支持NFC功能 / NFC未开启...
})

// 2. 开启读卡，贴近标签后回调
umSdk.startNfcScan({
  success: function(res) {
    console.log(res.uid)      // 标签UID（hex大写）
    console.log(res.techList) // 标签技术类型，如 ["NfcA","Ndef"]
    console.log(res.ndefText) // NDEF文本记录内容，无则 null
  },
  fail: function(msg, reason) { console.log(reason) }
})

// 3. 停止读卡（取消/离开页面时调用）
umSdk.stopNfcScan({
  success: function(res) {},
  fail: function(err) {}
})
```



### 个人签名
调用App签名页面，返回签名的base64图片

```javascript
umSdk.startSign({
  success: function(res) {
    console.log(res.data.base64Image) // 图片base64 
  },
  fail: function(err) {}
})
```



### 实名认证
无参拉起支付宝 eKYC 实名认证活体检测，完成后自动落库并同步应用平台个人中心；随后**强制调起百度人脸活体采集电子上岗照片并保存**（必采项，取消/失败则整个认证视为未完成）。Promise 风格，亦支持 success/fail 回调。

```javascript
umSdk.getVerifyInfo({
  success: function(res) {
    console.log(res.realName) // 姓名
    console.log(res.gender)   // 性别
    console.log(res.phone)    // 手机号
    console.log(res.userCode) // 用户编码
  },
  fail: function(err) {
    console.log(err) // 认证失败、实名信息同步失败或电子上岗照片采集/保存失败
  }
})
```

**返回值说明**

success 回调返回对象：

| 字段 | 类型 | 说明 |
|------|------|------|
| realName | String | 实名姓名 |
| gender | String | 性别 |
| phone | String | 手机号 |
| userCode | String | 用户编码 |

> **注意**：旧版 `verifyIdCard`（身份证号核验）已下线，请改用 `getVerifyInfo`。
>
> **流程说明（2026-09 起）**：`getVerifyInfo` 内部链路 = 支付宝 eKYC → confirm 落库 + 同步应用平台 → 百度人脸活体采集 → 保存电子上岗照片（`/yjy/organ/get/face/collection`）。任一环节失败或用户取消活体采集均走 fail，请按"认证未完成"处理并允许用户重试。



### 人脸比对（faceCompare）
调起 APP 原生人脸活体检测，活体通过后将现场采集照片与指定用户已采集的人脸照片比对，全程无确认页，直接返回比对结果。适用于第三方 H5 考勤、核验等刷脸场景。Promise 风格，亦支持 success/fail 回调。

```javascript
umSdk.faceCompare({
  userCode: '', // 选填，数据中台用户编码；留空=比对当前登录人，传值=比对任意人已采集人脸（现场核验场景）
  success: function(res) {
    console.log(res.status)        // 0=未采集人脸/用户不存在，1=比对失败，2=比对成功
    console.log(res.userCode)      // 用户编码（status=0 时为 null）
    console.log(res.msg)           // 结果描述
    console.log(res.score)         // 相似度得分
    console.log(res.livePhotoUrl)  // 本次活体检测采集照片URL（已上传文件服务）
  },
  fail: function(err) {
    console.log(err) // 活体取消/接口异常
  }
})
```

**参数说明**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| userCode | String | 否 | 数据中台用户编码。不传=比对当前登录用户；传值=比对该 userCode 对应用户的已采集人脸（操作员现场核验任意人场景） |

**返回值说明**

success 回调返回对象：

| 字段 | 类型 | 说明 |
|------|------|------|
| status | Number | 0=未采集人脸或用户不存在（userCode=null）；1=比对失败（已采集但比对不过）；2=比对成功 |
| userCode | String | 被比对用户的编码，status=0 时为 null |
| msg | String | 结果描述（对比成功/对比失败/未采集人脸/用户不存在或未同步数据中台） |
| score | String | 相似度得分（0-100） |
| livePhotoUrl | String | 本次活体检测采集照片 URL（比对前已上传文件服务；status=0/1 时同样返回） |

> **前置条件**：被比对用户需先在 APP 完成人脸采集（我的 → 实名认证，认证成功后强制采集电子上岗照片），否则返回 status=0。



### 是否需要拦截页面返回事件
APP对H5页面返回事件进行拦截

```javascript
umSdk.setCanInterceptBackKey({
  canIntercept: true,// true 拦截，false 不拦截
  success: function() {},
  fail: function(err) {}
})
```



### 页面注册拦截事件
当APP拦截页面返回事件后，会调用H5的方法

```javascript
function notifyOnBackPressed(data, responseCallback){
  console.log('APP返回啦----',data) // data 一般为APP提供给H5的参数，此处可以忽略
  responseCallback({isConsumed:true}) // 返回true 则APP不会执行返回操作
  // responseCallback({isConsumed:false}) // 返回false 则APP继续返回操作
}
// 开启监听
umSdk.on('notifyOnBackPressed',notifyOnBackPressed)
// 移除监听
umSdk.off('notifyOnBackPressed',notifyOnBackPressed)
```



### 获取手机网络状态
根据当前手机网络，返回是Wi-Fi、数据流量等网络状态

```javascript
umSdk.getNetworkConnectType({
  success: function(res) {
    console.log(res.data.connectType) // 无线网络：WIFI、 数据流量：MOBILE、未知网络（VPN等）：UNKNOWN、无网络：NO 
  },
  fail: function(err) {}
})
```



### 蓝牙相关方法
检测蓝牙是否可用

```javascript
umSdk.checkBtEnable({
  openAlert: "*",// 是否开启弹框提示 0 不开启 1开启 默认1 非必填
  success: function(res) {},
  fail: function(err) {}
})
```



获取附近蓝牙设备信息

```javascript
umSdk.getBtDeviceList({
  scanClassic: '0',  //默认0，0-只搜索低功耗蓝牙(BLE)，1-经典蓝牙+低功耗蓝牙(BLE)
  success: function(res) {
    console.log(res.data.ypdList) // 已配对设备集合
    console.log(res.data.fujinList) // 附近设备集合
    // 集合内对象字段说明
    // name         设备名称
    // identifier   设备唯一标识符（mac地址）
    // bleType      设备类型：0未知 1经典蓝牙 2BLE 3双模
    // bleSupported 是否支持BLE：0不支持 1支持
  },
  fail: function(err) {}
})
```

**实时发现事件（可选）**

扫描过程中每发现一个新设备，SDK 会通过 `onBtDeviceFound` 事件实时推送。H5 可在调用 `getBtDeviceList` 前注册该事件监听，实现设备列表实时渲染，无需等待扫描结束。

```javascript
// 扫描前注册实时发现事件
umSdk.on('onBtDeviceFound', function(device) {
  console.log('实时发现设备:', device.name, device.identifier)
  // device 字段同 getBtDeviceList 返回集合内对象
})

// 启动扫描（扫描结束后仍会通过 success 返回完整列表）
umSdk.getBtDeviceList({
  scanClassic: '0',
  success: function(res) { /* 完整列表 */ },
  fail: function(err) {}
})
```



连接蓝牙设备获取数据

**注意：success 回调为长连接推送机制，会被多次触发，H5 需按返回 data 内容区分状态。**

```javascript
umSdk.connectToBtDevice({
  identifier: '',    // 设备唯一标识符（mac地址），必填
  serviceUUID: '',   // 服务UUID，支持 short(如 ffe0) 或完整 128-bit 格式，BLE必填/经典蓝牙可空
  characterUUID: '', // 特征值UUID，支持 short(如 ffe1) 或完整 128-bit 格式，BLE必填/经典蓝牙可空
  protocol: '',      // 协议类型：'ble'强制BLE，'classic'强制经典蓝牙，空字符串或不传则自动判断
  success: function(res) {
    // res.data 可能为以下三种类型，需按类型分别处理：
    // 1. "蓝牙已连接"               — GATT 连接建立且 notify 订阅成功（首次触发）
    // 2. JSON 字符串（services）    — 包含设备所有服务与特征值信息，首次触发
    // 3. Base64 字符串              — 设备通过蓝牙推送的原始二进制数据（后续持续触发）
    console.log(res.data)
  },
  fail: function(err) {
    // err.data 可能为：
    // "设备已断开连接"      — 蓝牙连接意外断开
    // "连接失败xxx"         — 连接或 notify 订阅失败
    console.log(err.data)
  }
})
```

**连接数据流程**

1. H5 调用 `connectToBtDevice` 传入 identifier + serviceUUID + characterUUID
2. Native 通过 FastBle 建立 GATT 连接
3. 连接成功后 MTU 协商（默认请求 512），协商完成后订阅设备 notify
4. success 回调首次返回 services JSON（设备所有服务与特征值信息），随后返回 `"蓝牙已连接"`
5. 设备推送数据 → Native 将原始二进制数据编码为 Base64，通过同一 success 回调持续返回
6. 连接断开 → fail 回调返回 `"设备已断开连接"`

> **技术说明：** Native 层通过修改 JS Bridge 的 response callback 机制，移除了单次回调销毁限制，使 `connectToBtDevice` 的 `success` 回调可以持续接收设备推送数据，实现真正的长连接推送。

**数据透传说明**

SDK 对蓝牙传输的数据不做任何格式假设和处理，统一采用 **Base64 无损编码** 透传原始二进制数据。H5 接收后需根据设备实际协议进行解码。

- 若设备协议为十六进制（Hex）：Base64 解码后得到 `Uint8Array`，再逐字节转 Hex 字符串
- 若设备协议为文本：Base64 解码后按对应字符编码（如 UTF-8）转字符串

**Base64 解码为 Hex 参考代码**

```javascript
/**
 * 将 Base64 字符串解码为 Hex 字符串
 * @param {string} base64Str SDK 返回的 Base64 数据
 * @returns {string} 空格分隔的 Hex 字符串，如 "7E 00 05 01..."
 */
function base64ToHex(base64Str) {
  // 1. Base64 解码为二进制字符串
  const binaryStr = atob(base64Str);
  // 2. 逐字符转 Uint8Array
  const bytes = new Uint8Array(binaryStr.length);
  for (let i = 0; i < binaryStr.length; i++) {
    bytes[i] = binaryStr.charCodeAt(i);
  }
  // 3. 逐字节转 Hex，空格分隔
  return Array.from(bytes)
    .map(b => b.toString(16).toUpperCase().padStart(2, '0'))
    .join(' ');
}

// 使用示例
umSdk.connectToBtDevice({
  identifier: 'C3:6D:BF:2B:6A:02',
  serviceUUID: 'ffe0',
  characterUUID: 'ffe1',
  success: function(res) {
    const data = res.data;
    if (data === '蓝牙已连接') {
      console.log('连接成功');
      return;
    }
    // 判断是否为 services JSON
    try {
      const json = JSON.parse(data);
      if (json.type === 'services') {
        console.log('设备服务列表:', json.services);
        return;
      }
    } catch (e) {
      // 不是 JSON，按 Base64 业务数据处理
    }
    // 解码为 Hex
    const hexStr = base64ToHex(data);
    console.log('Hex:', hexStr);
  },
  fail: function(err) {
    console.error('蓝牙错误:', err.data);
  }
});
```



### 数据拼包协议（packetProtocol）

BLE 底层 MTU 限制、经典蓝牙流式传输（RFCOMM/SPP）均可能导致外设发送的一条完整报文被拆分为多次回调。SDK 支持通过 `packetProtocol` 参数在**连接时**指定拼包规则，Native 层拼好完整帧后再回调 H5。BLE 与经典蓝牙均支持此功能。

不传 `packetProtocol` 时保持原有透传行为，每次回调即为一包数据。

```javascript
umSdk.connectToBtDevice({
  identifier: 'XX:XX:XX:XX:XX:XX',
  serviceUUID: 'ffe0',
  characterUUID: 'ffe1',
  protocol: 'ble',
  packetProtocol: JSON.stringify({
    type: 'delimiter',        // 拼包类型
    delimiterBase64: 'DQo=',  // 结束符 Base64（\r\n）
    debug: true               // 输出拼包日志到 logcat
  }),
  success: function(res) {
    // res.data 为拼好的完整帧（Base64）
  }
})
```

#### 拼包类型说明

| type | 说明 | 适用场景 |
|------|------|----------|
| transparent | 直接透传（默认） | 不分包设备，或 H5 自行处理 |
| delimiter | 结束符分割 | GPS、串口透传模块等以 \r\n 结尾 |
| header_length | 帧头 + 长度域 | 自定义协议，帧头固定 + 长度指示 payload |
| fixed_length | 固定帧长 | 工业传感器，每帧长度固定 |
| timeout | 超时聚合 | 协议不明时的兜底，收到数据后 N ms 无新包则 flush |

#### 各类型参数详情

| 参数 | 类型 | 说明 |
|------|------|------|
| type | string | 必填，transparent / delimiter / header_length / fixed_length / timeout |
| delimiterBase64 | string | delimiter 专用：结束符字节数组的 Base64 编码。不传默认 \r\n |
| headerBase64 | string | header_length 专用：帧头字节数组的 Base64 编码 |
| lengthFieldOffset | int | header_length 专用：长度域起始偏移（相对于帧头起始），默认 0 |
| lengthFieldBytes | int | header_length 专用：长度域字节数（1/2/4），默认 2 |
| lengthIncludesHeader | int | header_length 专用：长度值是否包含帧头本身（0=不包含，1=包含），默认 0 |
| lengthAdjustment | int | header_length 专用：帧头之后、长度域之前的固定字节数（额外偏移），默认 0 |
| fixedLength | int | fixed_length 专用：固定帧长度 |
| timeoutMs | int | timeout 专用：超时时间，毫秒，默认 200 |
| maxFrameLength | int | 通用：最大帧长度，防止内存溢出，默认 4096 |
| debug | boolean | 通用：调试模式，打印拼包日志，默认 false |



向蓝牙设备写入数据

部分设备需要先写入启动指令才会开始推送数据。调用 `writeBtData` 前需确保设备已连接。

```javascript
umSdk.writeBtData({
  identifier: '',    // 设备唯一标识符（mac地址），必填
  serviceUUID: '',   // 服务UUID，BLE必填/经典蓝牙可空
  characterUUID: '', // 写特征值UUID，BLE必填/经典蓝牙可空
  protocol: '',      // 协议类型：'ble'强制BLE，'classic'强制经典蓝牙，空字符串或不传则自动判断
  data: 'fgAFAWE=',  // Base64字符串，SDK内部解码为byte[]发送，必填
  success: function(res) {
    console.log('写入成功')
  },
  fail: function(err) {
    console.log('写入失败', err)
  }
})
```

> **说明：** `data` 参数为 **Base64 字符串**（如 `"fgAFAWE="`），与 `connectToBtDevice` 接收的数据格式一致。SDK 内部 Base64 解码为 `byte[]` 后通过 BLE write 发送。

**完整流程示例**

```javascript
umSdk.connectToBtDevice({
  identifier: 'C3:6D:BF:2B:6A:02',
  serviceUUID: 'ffe0',
  characterUUID: 'ffe1',
  success: function(res) {
    if (res.data === '蓝牙已连接') {
      // 连接成功后写入启动指令（Base64编码）
      umSdk.writeBtData({
        identifier: 'C3:6D:BF:2B:6A:02',
        serviceUUID: 'ffe0',
        characterUUID: 'ffe1',
        data: 'fgAFAWE=',  // Base64，对应 Hex 7E00050131
        success: function() {
          console.log('启动指令已发送，等待设备推送数据')
        }
      });
    } else {
      // 接收设备推送的数据（Base64）
      console.log('收到数据:', res.data);
    }
  }
});
```

断开设备连接

```javascript
umSdk.disconnectBtDevice({
  identifier: '',// 设备唯一标识符（mac地址）
  protocol: '',  // 协议类型：'ble'强制BLE，'classic'强制经典蓝牙，空字符串或不传则自动判断
  success: function(res) {},
  fail: function(err) {}
})
```

> **注意：** 调用成功后 Native 会停止设备 notify 订阅并断开 GATT 连接，H5 不再接收该设备推送数据。



#### 经典蓝牙（RFCOMM/SPP）支持说明

SDK 在支持 BLE（低功耗蓝牙）的基础上，同时支持**经典蓝牙**（Classic Bluetooth / RFCOMM / SPP）设备的连接、数据写入与接收。

**如何判断设备类型？**

调用 `getBtDeviceList` 后，设备对象中的 `bleSupported` 字段标识设备类型：

| bleSupported | 设备类型 | 底层协议 |
|-------------|---------|---------|
| `"1"` | 低功耗蓝牙（BLE）/ 双模 | GATT |
| `"0"` | 经典蓝牙 | RFCOMM/SPP |

> `bleType` 字段也可参考：`1` 为经典蓝牙，`2` 为 BLE，`3` 为双模设备。

**双模设备协议选择**

双模设备（`bleType: "3"`，`bleSupported: "1"`）默认走 **BLE** 协议。若需强制使用经典蓝牙，通过 `protocol` 参数指定：

```javascript
// 强制经典蓝牙（双模设备走 SPP）
umSdk.connectToBtDevice({
  identifier: 'XX:XX:XX:XX:XX:XX',
  serviceUUID: '',
  characterUUID: '',
  protocol: 'classic',  // 强制经典蓝牙
  success: function(res) {}
})

// 强制 BLE（显式指定）
umSdk.connectToBtDevice({
  identifier: 'XX:XX:XX:XX:XX:XX',
  serviceUUID: 'ffe0',
  characterUUID: 'ffe1',
  protocol: 'ble',  // 强制 BLE
  success: function(res) {}
})
```

| protocol | 行为 |
|---------|------|
| `"ble"` | 强制走 BLE GATT，不管扫描结果 |
| `"classic"` | 强制走经典蓝牙 RFCOMM/SPP |
| `""` 或不传 | 自动判断（现有逻辑：先查BLE列表，再查经典蓝牙列表） |

**H5 调用方式**

经典蓝牙与 BLE **完全复用同一套接口**，H5 无需区分设备类型：

```javascript
// 1. 扫描设备（scanClassic: '1' 同时扫描经典蓝牙和 BLE）
umSdk.getBtDeviceList({
  scanClassic: '1',
  success: function(res) {
    // res.data.fujinList 中 bleSupported: "0" 的为经典蓝牙设备
  }
})

// 2. 连接设备（经典蓝牙不需要 serviceUUID/characterUUID，可传空字符串）
umSdk.connectToBtDevice({
  identifier: 'XX:XX:XX:XX:XX:XX',
  serviceUUID: '',      // 经典蓝牙可传空
  characterUUID: '',    // 经典蓝牙可传空
  protocol: 'classic',  // 显式指定经典蓝牙（也可不传自动判断）
  success: function(res) {
    // 首次返回 "经典蓝牙已连接"
    // 后续持续返回 Base64 数据（同 BLE）
  }
})

// 3. 写入数据（经典蓝牙不需要 serviceUUID/characterUUID）
umSdk.writeBtData({
  identifier: 'XX:XX:XX:XX:XX:XX',
  serviceUUID: '',      // 经典蓝牙可传空
  characterUUID: '',    // 经典蓝牙可传空
  protocol: 'classic',  // 显式指定经典蓝牙
  data: 'fgAFAWE=',     // Base64 字符串
  success: function(res) { console.log('写入成功') }
})

// 4. 断开设备
umSdk.disconnectBtDevice({
  identifier: 'XX:XX:XX:XX:XX:XX',
  protocol: 'classic',  // 显式指定经典蓝牙
  success: function(res) {}
})
```

**经典蓝牙连接流程**

1. H5 调用 `connectToBtDevice`
2. Native 判断设备为经典蓝牙（`bleSupported: "0"`）
3. 检查设备配对状态，未配对则自动触发系统配对弹窗
4. 配对完成后自动建立 RFCOMM 连接（SPP 标准 UUID）
5. 连接成功后 `success` 回调返回 `"经典蓝牙已连接"`
6. 数据通过 `InputStream`/`OutputStream` 读写，Base64 编码后透传

**注意事项**

- 经典蓝牙首次连接需要**系统配对**，会弹出系统配对对话框，用户需手动确认（部分工业设备配对 PIN 为 `0000` 或 `1234`）
- 经典蓝牙无 Service/Characteristic 概念，`serviceUUID` 和 `characterUUID` 可传空字符串
- 经典蓝牙数据传输为**流式**，无固定帧边界，H5 端需按设备协议处理粘包/分包
- BLE 与经典蓝牙互相独立，断开其中一种不会影响另一种



### 录音相关方法

调用前需先获取麦克风权限 `getMicrophoneAuth`。录音文件以 AAC 格式保存，通过 `content://` URI 返回。

#### 开始录音

```javascript
umSdk.startAudioRecord({
  maxDuration: 60,  // 最大录制时长（秒），默认 60，可选
  success: function(res) {
    console.log(res.data) // "录音开始"
  },
  fail: function(err) {
    console.log(err.data) // 错误信息，如"没有录音权限"、"正在录音中"
  }
})
```

#### 停止录音

```javascript
umSdk.stopAudioRecord({
  success: function(res) {
    console.log(res.data.filePath)  // 录音文件 content:// URI
    console.log(res.data.duration)  // 录音时长（毫秒）
    console.log(res.data.size)      // 文件大小（字节）
  },
  fail: function(err) {
    console.log(err.data) // 错误信息，如"当前没有在录音"
  }
})
```

#### 播放音频文件

通过原生播放器播放录音文件，支持 `content://` URI。

```javascript
umSdk.playAudioFile({
  filePath: 'content://...',  // 录音文件 URI，必填
  success: function(res) {
    console.log(res.data) // "播放开始"
  },
  fail: function(err) {
    console.log(err.data) // 错误信息，如"播放失败"
  }
})
```

#### 停止播放音频

```javascript
umSdk.stopAudioFile({
  success: function(res) {
    console.log(res.data) // "播放已停止"
  },
  fail: function(err) {}
})
```

**录音完整流程示例**

```javascript
// 1. 检查并获取麦克风权限
umSdk.getMicrophoneAuth({
  success: function() {
    // 2. 开始录音
    umSdk.startAudioRecord({
      maxDuration: 60,
      success: function() {
        console.log('录音已开始')
      }
    })
  }
})

// 3. 停止录音（用户主动触发或到达 maxDuration 自动停止）
umSdk.stopAudioRecord({
  success: function(res) {
    console.log('录音文件:', res.data.filePath)
    console.log('时长:', res.data.duration, 'ms')
    console.log('大小:', res.data.size, 'bytes')
    
    // 4. 播放录音
    umSdk.playAudioFile({
      filePath: res.data.filePath,
      success: function() {
        console.log('开始播放')
      }
    })
  }
})

// 5. 停止播放
umSdk.stopAudioFile({
  success: function() {
    console.log('播放已停止')
  }
})
```

**注意事项**

- `startAudioRecord` 与 `stopAudioRecord` 需成对调用，同一时间只能有一段录音在进行中
- 到达 `maxDuration` 后 Native 会自动停止录音并回调 `stopAudioRecord` 的 success
- 录音过程中页面关闭或 Activity 销毁，Native 会自动停止录音并清理资源
- 返回的 `filePath` 为 `content://` URI，H5 `<audio>` 标签无法直接播放，需通过 `playAudioFile` 调用原生播放器
- 录音文件保存在应用缓存目录，应用卸载后自动清理



获取手机设备基础信息

```javascript
umSdk.getMobileDeviceInformation({
  success: function(res) {
    console.log(res.data)  
    // data 对象字段说明
    // deviceManufacturer 设备制造商 
    // name 设备名称 
    // model 设备型号 
    // systemVersion 设备系统版本号 
    // UDID 设备唯一标识符 
    // appVersion 应用版本号 
  },fail: function(err) {}
})
```



## 其他三方平台
### 电信8600平台--废弃
兼容电信8600平台监控的参数，调用海康威视平台原生播放插件，具体需要接入海康威视平台业务功能，请联系海康威视开发者，参考文档：[https://open.hikvision.com/resourceCenter#%E5%B9%B3%E5%8F%B0%E5%BC%80%E6%94%BE](https://open.hikvision.com/resourceCenter#%E5%B9%B3%E5%8F%B0%E5%BC%80%E6%94%BE)

```javascript
umSdk.ivmsplay({
  address:"",// 登录平台地址
  username:"",// 用户名
  password:"",// 密码
  cameraid:"",// 监控点
  iddeviceid:"",// 设备id
  success: function() {},
  fail: function(err) {}
})
```



### 海康威视平台
调用海康威视平台原生播放插件，具体需要接入海康威视平台业务功能，请联系海康威视开发者，参考文档：[https://open.hikvision.com/resourceCenter#%E5%B9%B3%E5%8F%B0%E5%BC%80%E6%94%BE](https://open.hikvision.com/resourceCenter#%E5%B9%B3%E5%8F%B0%E5%BC%80%E6%94%BE)

```javascript
umSdk.ivmsplayv2({
  apiaddress:"",// api请求地址全路径，仅支持Post请求，原生会默认把 {tokenid} 替换成当前用户token
  postdata:"",// 文件上Post请求参数，Json字符串，请求api时传过去（主流码 子流码 key 必须是 streamtype）
  success: function() {},
  fail: function(err) {}
})
```



### 大华视频平台
调用此接口播放大华摄像头视频

[https://open-icc.dahuatech.com/#/home](https://open-icc.dahuatech.com/#/home)

```javascript
umSdk.callHandler('openDaHuaPreVideo',{
  'videoServer': '服务地址',
  'videoPort': 端口号,
  'videoUser': '用户名'，
  'videoPasscode': '用户密码',
  'videoChannelId': '视频通道ID'
  }, function(data){
  	// console.log(data)
  }
)
```



错误码如下图：

<!-- 这是一张图片，ocr 内容为： -->
![](</images/images-technical-app-platform/1766568560424-01fe4656-f1b0-49f5-a637-f0446c4b2af9.png>)



# 附录：Hex 与 Base64 互转工具

蓝牙数据写入接口 `writeBtData` 要求 `data` 参数为 **Base64 字符串**。以下提供前端 Hex 字符串与 Base64 字符串互转的参考实现，供第三方集成方使用。

```javascript
/**
 * Hex 字符串转 Base64 字符串
 * @param {string} hex - Hex 字符串，如 '667B7A7B'
 * @returns {string} Base64 字符串，如 'Znt6ew=='
 */
function hexToBase64(hex) {
  if (!hex || hex.length % 2 !== 0) {
    throw new Error('Invalid hex string');
  }
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  }
  let binary = '';
  bytes.forEach(b => binary += String.fromCharCode(b));
  return btoa(binary);
}

/**
 * Base64 字符串转 Hex 字符串
 * @param {string} base64 - Base64 字符串，如 'Znt6ew=='
 * @returns {string} Hex 字符串，如 '667B7A7B'
 */
function base64ToHex(base64) {
  if (!base64) return '';
  const binary = atob(base64);
  let hex = '';
  for (let i = 0; i < binary.length; i++) {
    const byte = binary.charCodeAt(i).toString(16);
    hex += byte.length === 1 ? '0' + byte : byte;
  }
  return hex.toUpperCase();
}

// 使用示例
const hexCmd = '667B7A7B';          // 设备启动指令（Hex）
const base64Cmd = hexToBase64(hexCmd); // "Znt6ew=="
console.log('Base64:', base64Cmd);

// 写入蓝牙设备
umSdk.writeBtData({
  identifier: 'XX:XX:XX:XX:XX:XX',
  serviceUUID: 'ffe0',
  characterUUID: 'ffe1',
  data: base64Cmd,
  success: (res) => console.log('写入成功', res),
  fail: (err) => console.error('写入失败', err)
});

// 接收到 Base64 数据后转回 Hex
umSdk.on('onBtDeviceSendData', (base64Data) => {
  const hexData = base64ToHex(base64Data);
  console.log('收到 Hex 数据:', hexData);
});
```

> **说明**：`hexToBase64` 将设备指令的 Hex 格式转为 SDK 要求的 Base64 格式；`base64ToHex` 将 SDK 透传回来的 Base64 数据还原为 Hex 格式以便解析。
