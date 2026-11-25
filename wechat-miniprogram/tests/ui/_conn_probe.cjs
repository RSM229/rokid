/* 最小连通性：只 connect + systemInfo，不动页面 */
const automator = require('miniprogram-automator')
async function main() {
  console.log('connecting...')
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9420' })
  console.log('connected')
  const info = await mini.systemInfo()
  console.log('SDKVersion:', info.SDKVersion)
  const page = await mini.currentPage()
  console.log('currentPage:', page && page.path)
  await mini.disconnect()
  console.log('done')
  process.exit(0)
}
main().catch(e => { console.error('FAIL:', e.message); process.exit(1) })
setTimeout(() => { console.error('GLOBAL TIMEOUT'); process.exit(2) }, 30000)
