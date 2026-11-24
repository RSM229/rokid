/* 注入登录态：token + user_info（生产 user 1：csm / chief_supervisor / 项目 15） */
const automator = require('miniprogram-automator')
const WS = 'ws://127.0.0.1:9420'
const TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwidHlwZSI6ImFjY2VzcyIsImV4cCI6MTc5NTUyODA3MX0.gPRbttozqDzepFPQUeG2ILZZ3VhUP56AqWbBVcjDccA'

async function main() {
  const mini = await automator.connect({ wsEndpoint: WS })
  try {
    const out = await Promise.race([
      mini.evaluate((token) => {
        const apiBaseUrl = 'https://www.baize.gdces.cn/api'
        const scope = encodeURIComponent(apiBaseUrl)
        wx.setStorageSync(`gdjl_${scope}_access_token`, token)
        wx.setStorageSync(`gdjl_${scope}_user_info`, {
          user_id: 1,
          username: 'chief',
          real_name: 'csm',
          role: 'chief_supervisor',
          project_id: 15,
          project_name: '沙盛四路等四条道路工程',
          permissions: ['task_manage', 'doc_manage', 'member_manage'],
          must_change_password: false,
        })
        // 登录页 onLoad 只在有 token 时跳 agent —— 直接 reLaunch
        wx.reLaunch({ url: '/pages/agent/index' })
        return 'INJECTED'
      }, TOKEN),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 20000)),
    ])
    console.log('result:', out)
    // 等页面切过去再读一次栈
    await new Promise(r => setTimeout(r, 4000))
    const pages = await Promise.race([
      mini.evaluate(() => JSON.stringify(getCurrentPages().map(p => p.route))),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout2')), 15000)),
    ])
    console.log('pages:', pages)
    process.exit(0)
  } finally {
    await mini.disconnect().catch(() => {})
  }
}
main().catch(e => { console.error('ERR:', e.message); process.exit(1) })
