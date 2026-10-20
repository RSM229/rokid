# 监理通微信小程序端

这是“监理通”的第四个业务终端，已实现微信身份绑定、智能体文字/录音交互、拍照录像、弱网上传队列、四类工作记录、隐患整改复查、待办、同项目通讯录及一对一音视频通话；另提供「规范知识库」页（规范检索 / 验收指引 / 智能问答，对接 `/api/knowledge`）与「AI 巡检」页（巡检会话启停与拍照识别，对接 `/api/inspection`）。

## 开发检查

```bash
npm install
npm run typecheck
```

随后用微信开发者工具导入本目录：

1. 复制 `project.private.config.json.example` 为 `project.private.config.json`，填写企业主体小程序 AppID。
2. 开发者工具本地联调可直接使用 `miniprogram/config.ts` 的默认地址。体验版/正式版通过第三方平台 `ext` 配置注入 API/WSS 地址，字段格式见 `ext.json.example`；真机必须使用已备案的 HTTPS/WSS 域名，不能使用 `127.0.0.1`。
3. 后端配置 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`。仅本地联调可同时开启后端 `WECHAT_MOCK_LOGIN_ENABLED=true` 和前端 `mockWechatLogin=true`。
4. 在微信公众平台配置 request、uploadFile、downloadFile、socket 合法域名、业务类目和隐私保护指引（`app.json` 已开启 `__usePrivacyCheck__`）。
   **服务器只开放 4432 时无需改端口**——微信 https 合法域名允许带端口，但不能混用。按当前默认地址 `https://www.baize.gdces.cn:4432/api` 需配置为：

   | 类型 | 后台填写值 |
   | --- | --- |
   | request 合法域名 | `https://www.baize.gdces.cn:4432` |
   | uploadFile 合法域名 | `https://www.baize.gdces.cn:4432` |
   | downloadFile 合法域名 | `https://www.baize.gdces.cn:4432` |
   | socket 合法域名 | `wss://www.baize.gdces.cn`（wss 域名免配端口，默认放行该域名下所有端口；当前版本用轮询信令，暂未用到） |

   要点：带端口域名配置后，**代码里的地址必须带完全相同的端口**，写不带端口或写 `:443` 都会被拦截；反过来后台若不配端口，请求 URL 里也不能带端口（连 `:443` 都失败）。域名必须已完成 ICP 备案，且证书域名匹配、TLS ≥ 1.2。
5. **音视频通话依赖 `trtc-wx-sdk`，上传代码前必须在开发者工具执行「工具 → 构建 npm」**，否则通话页会提示“音视频组件未安装”。
6. 使用真机验证相机、录音、位置、前后台切换、弱网重试及双端呼叫。

弱网上传队列会在小程序回到前台或网络恢复后自动补传，也可在“采集”页手动重试。不同发布环境的地址通过 `ext` 配置隔离，微信 AppSecret、RTC Secret 和 AI Token 始终只保存在服务端。

## 音视频

默认 `RTC_PROVIDER=mock`，可完整联调联系人、呼叫状态、来电轮询和通话界面，但不传输真实媒体（来电检测为 2s 轮询，弱网退避上限 8s；后端暂未在 `/api/ws` 广播 call_session）。生产环境配置 `RTC_PROVIDER=generic_signed`、RTC AppID/Secret 以及 `RTC_PUSH_URL_TEMPLATE`、`RTC_PLAY_URL_TEMPLATE` 后，通话页会启用小程序 `live-pusher` / `live-player`。

实际发布前仍需按所选合规 RTC 服务商的要求配置推拉流域名、签名规则和微信实时音视频类目权限，并完成两台真机互通验证。任何 AppSecret、RTC Secret、AI Token 都不得写入小程序源码。
