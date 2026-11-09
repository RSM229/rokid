# 监理通微信小程序端

这是“监理通”的第四个业务终端，已实现微信身份绑定、智能体文字/录音交互、拍照录像、弱网上传队列、四类工作记录、隐患整改复查、待办；另提供「规范知识库」页（规范检索 / 验收指引 / 智能问答，对接 `/api/knowledge`）与「AI 巡检」页（巡检会话启停与拍照识别，对接 `/api/inspection`）。

## 开发检查

```bash
npm install
npm run typecheck
```

`npm install` 会通过 `postinstall` 把 `@babel/runtime` 安装到
`miniprogram/node_modules`。依赖必须位于 `miniprogramRoot` 内，开发者工具才能执行
「工具 → 构建 npm」；构建产物输出到 `miniprogram/miniprogram_npm`。

## 本地后端联调

本地开发运行同一套 FastAPI 后端，使用服务器真实微信、AI 配置，并经 R640 SSH 隧道直接连接服务器业务库 `gdjl`。本地与服务器数据实时共享；本地创建、修改或删除的业务数据会直接影响服务器上的真实数据，请使用已有账号和测试项目，不要写入演示数据。上传与导出文件仍保存在本机 `cloud-backend/.local/`，不与服务器文件目录同步。先在仓库根目录执行：

```powershell
cd cloud-backend
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements-local.txt
cd ..
.\start-local-dev.ps1
```

启动器首次自动同步服务配置到 Git 忽略的 `.local/backend.env`，并将服务器 CPU 向量模型下载到 `.local/models/`，再建立 SSH 数据库转发并启动热重载后端。它跳过数据库自动建表、迁移和种子数据，使用只允许读写既有数据的专用数据库账号。以后只需运行 `start-local-dev.ps1`；服务密钥更新后用 `start-local-dev.ps1 -RefreshConfig` 重新同步。SSH 别名 `rokid-r640` 必须已配置可免密连接 R640。语音/视频转换需要本机安装 ffmpeg 并加入 PATH。

后端地址为 `http://127.0.0.1:8000`，接口文档为 `http://127.0.0.1:8000/docs`。微信开发者工具打开本小程序后重新编译，在「详情 → 本地设置」勾选不校验合法域名。开发版默认连接本地后端，`ext.useLocalBackend=false` 可作为明确的退出开关；无需开发者工具注入 ext 配置。账号和微信绑定读取服务器现有数据。

微信使用真实 `wx.login` 和微信 `jscode2session`；AppID/Secret 从服务器同步并必须对应当前小程序。AI 调用与服务器同一服务；短信使用腾讯云真实服务，缺少凭据会明确失败。凭据仅在后端，不写进小程序或提交 Git。本地启动器不会回退到 SQLite 或模拟登录，也不会创建本地演示账号；请使用已有业务账号。注意：数据库共享不包含文件存储，本地上传产生的文件保存在本机，服务器记录中的既有附件路径在本地可能无法读取。

体验版与正式版继续走 `ext.apiBaseUrl`（当前为正式服务器），本地开关仅在开发版生效。小程序按 API 地址隔离登录态和上传队列。本地启动器监听电脑回环地址，适用于开发者工具模拟器；真机不能访问电脑的 `127.0.0.1`，需要另外配置局域网或测试入口。修改 `cloud-backend/app` 下 Python 文件会自动重载，修改后端配置文件需重启启动器。本地开发默认关闭周期文书自动任务，避免测试时自动生成历史文书。此前新增的「资料核对」页仍为独立交互原型，尚未接真实识图和建单流程。

停止服务在启动终端按 `Ctrl+C`，会同时关闭后端和数据库 SSH 隧道。

随后用微信开发者工具导入本目录：

1. 复制 `project.private.config.json.example` 为 `project.private.config.json`，填写企业主体小程序 AppID。
2. `miniprogram/config.ts` 在 `develop` 开发版默认连接本机 `127.0.0.1:8000`，只在开发者工具本地设置里勾选「不校验合法域名」即可；体验版/正式版不受影响，仍从 `ext.apiBaseUrl` 读取线上服务器地址。真机不能使用电脑的 `127.0.0.1`。
3. 后端本地配置 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`；本地服务与体验/正式服务都使用真实微信登录，保持 `WECHAT_MOCK_LOGIN_ENABLED=false` 和 `mockWechatLogin=false`。
4. 在微信公众平台配置 request、uploadFile、downloadFile、socket 合法域名、业务类目和隐私保护指引（`app.json` 已开启 `__usePrivacyCheck__`）。
   **服务器只开放 4432 时无需改端口**——微信 https 合法域名允许带端口，但不能混用。按当前默认地址 `https://www.baize.gdces.cn:4432/api` 需配置为：

   | 类型 | 后台填写值 |
   | --- | --- |
   | request 合法域名 | `https://www.baize.gdces.cn:4432` |
   | uploadFile 合法域名 | `https://www.baize.gdces.cn:4432` |
   | downloadFile 合法域名 | `https://www.baize.gdces.cn:4432` |
   | socket 合法域名 | `wss://www.baize.gdces.cn`（wss 域名免配端口，默认放行该域名下所有端口；当前版本未使用） |

   要点：带端口域名配置后，**代码里的地址必须带完全相同的端口**，写不带端口或写 `:443` 都会被拦截；反过来后台若不配端口，请求 URL 里也不能带端口（连 `:443` 都失败）。域名必须已完成 ICP 备案，且证书域名匹配、TLS ≥ 1.2。
5. 使用真机验证相机、录音、位置、前后台切换、弱网重试。

弱网上传队列会在小程序回到前台或网络恢复后自动补传，也可在“采集”页手动重试。不同发布环境的地址通过 `ext` 配置隔离，微信 AppSecret 和 AI Token 始终只保存在服务端。
