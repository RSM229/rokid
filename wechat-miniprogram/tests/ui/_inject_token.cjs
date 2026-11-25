/* 注入登录态：token + user_info（默认 user 1：csm / chief_supervisor / 项目 15）
 * 用法：
 *   node _inject_token.cjs          # 有项目态（project_id=15）
 *   node _inject_token.cjs no-project  # 无项目态（复刻生产 user 32 卡点现场：
 *                                     inspector、project_id=NULL——仅前端态，
 *                                     后端仍是 user 1，不打写接口就不会撞墙） */
const automator = require('miniprogram-automator')
const WS = 'ws://127.0.0.1:9420'
// 2026-11-25 10:55 远端签发的新 token（ rokide-deploy 容器 create_access_token，6h 有效）
const TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwiZXhwIjoxNzk1NjU3ODE0fQ.mow5YnaptDqpErs8qlsCGgKYg_uPr6Nn-Jo1ClScgGM'
const NO_PROJECT = process.argv[2] === 'no-project'

async function main() {
  const mini = await automator.connect({ wsEndpoint: WS })
  try {
    const out = await Promise.race([
      mini.evaluate((token, noProject) => {
        const apiBaseUrl = 'https://www.baize.gdces.cn/api'
        const scope = encodeURIComponent(apiBaseUrl)
        wx.setStorageSync(`gdjl_${scope}_access_token`, token)
        wx.setStorageSync(`gdjl_${scope}_user_info`, {
          user_id: 1,
          username: 'chief',
          real_name: 'csm',
          role: 'chief_supervisor',
          project_id: noProject ? null : 15,
          project_name: noProject ? '' : '沙盛四路等四条道路工程',
          permissions: ['task_manage', 'doc_manage', 'member_manage'],
          must_change_password: false,
        })
        // 登录页 onLoad 只在有 token 时跳 agent —— 直接 reLaunch
        wx.reLaunch({ url: '/pages/agent/index' })
        return 'INJECTED' + (noProject ? ':no-project' : '')
      }, TOKEN, NO_PROJECT),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 20000)),
    ])
    console.log('result:', out)
    const pages = await mini.pageStack().then(ps => ps.map(p => p.path)).catch(() => ['?'])
    console.log('pages:', JSON.stringify(pages))
  } finally {
    await mini.disconnect().catch(() => {})
  }
}
main().then(() => process.exit(0)).catch(e => { console.error('ERR:', e.message); process.exit(1) })
