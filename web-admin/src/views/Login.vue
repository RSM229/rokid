<template>
  <div class="login-container">
    <div class="login-left">
      <div class="brand">
        <div class="brand-logo-window">
          <img src="/app-logo.png" alt="广东建设智慧监理 Logo" class="brand-logo" />
        </div>
        <h1>广东建设智慧监理</h1>
        <p>AI 工程监理智能体</p>
      </div>
    </div>
    <div class="login-right">
      <el-card class="login-card" shadow="never">
        <template #header>
          <h2>登录</h2>
        </template>

        <div class="login-tabs">
          <button
            type="button"
            class="login-tab"
            :class="{ active: mode === 'password' }"
            @click="switchMode('password')"
          >账号登录</button>
          <button
            type="button"
            class="login-tab"
            :class="{ active: mode === 'scan' }"
            @click="switchMode('scan')"
          >扫码登录</button>
        </div>

        <el-form v-if="mode === 'password'" :model="form" label-position="top" @submit.prevent="handleSubmit">
          <el-form-item label="用户名">
            <el-input v-model="form.username" placeholder="请输入用户名" prefix-icon="User" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              prefix-icon="Lock"
              show-password
              @keyup.enter="handleSubmit"
            />
          </el-form-item>
          <div class="forgot-link">
            <span @click="goForgot">忘记密码？</span>
          </div>
          <el-form-item label="选择角色">
            <el-select
              v-model="form.role"
              placeholder="自动识别（推荐）"
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="r in roles"
                :key="r.value"
                :label="r.label"
                :value="r.value"
              />
            </el-select>
            <div class="role-hint">
              留空将按账号实际岗位自动登录；若不确定自己的岗位，请直接留空
            </div>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="loading" style="width: 100%" @click="handleSubmit">
              登录
            </el-button>
          </el-form-item>
        </el-form>

        <div v-else class="scan-panel">
          <div class="scan-qr">
            <img v-if="scan.qrDataUrl && scan.status !== 'expired' && scan.status !== 'used'" :src="scan.qrDataUrl" alt="登录二维码" class="scan-qr-img" />
            <div v-else class="scan-qr-placeholder">
              {{ scan.loading ? '二维码生成中…' : ' ' }}
            </div>
            <div v-if="scan.status === 'expired' || scan.status === 'used'" class="scan-qr-mask">
              <p>{{ scan.status === 'used' ? '二维码已使用' : '二维码已过期' }}</p>
              <el-button size="small" type="primary" plain @click="startScan">刷新二维码</el-button>
            </div>
            <div v-else-if="scan.status === 'confirmed'" class="scan-qr-mask success">
              <p>登录成功，正在进入…</p>
            </div>
          </div>

          <p v-if="scan.code" class="scan-code">网页校验码 <strong>{{ scan.code }}</strong></p>
          <p class="scan-tip">打开「监理通」小程序 → 我的 → 扫一扫登录网页端</p>
          <p class="scan-tip sub">扫描后请核对小程序上显示的校验码与上方一致</p>
          <p v-if="scan.status === 'pending'" class="scan-countdown">二维码有效期 {{ scan.expiresIn }} 秒</p>
          <div class="scan-actions">
            <el-button text size="small" :loading="scan.loading" @click="startScan">刷新二维码</el-button>
          </div>
        </div>

        <div class="switch-mode">
          <span>账号由管理员统一创建；成员经「项目邀请码」加入项目</span>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import QRCode from 'qrcode'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api'
import { ROLE_LABELS, ROLE_KEYS } from '@/constants/role'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)

type LoginMode = 'password' | 'scan'
type ScanStatus = 'idle' | 'loading' | 'pending' | 'confirmed' | 'expired' | 'used'

const mode = ref<LoginMode>('password')

const form = reactive({
  username: '',
  password: '',
  role: '', // 登录模式默认空 = 服务端按账号真实岗位自动判定
})

const scan = reactive({
  loading: false,
  ticket: '',
  secret: '',
  code: '',
  qrDataUrl: '',
  status: 'idle' as ScanStatus,
  expiresIn: 0,
})

let pollTimer: number | undefined
let countdownTimer: number | undefined

interface RoleOption {
  value: string
  label: string
}

const roles = ref<RoleOption[]>([])

onMounted(async () => {
  try {
    const res = await authApi.roles() as any
    roles.value = res.roles || []
  } catch (e) {
    roles.value = ROLE_KEYS.map((key) => ({
      value: key,
      label: ROLE_LABELS[key]
    }))
  }
})

onBeforeUnmount(() => {
  clearScanTimers()
})

function clearScanTimers() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
  if (countdownTimer !== undefined) {
    window.clearInterval(countdownTimer)
    countdownTimer = undefined
  }
}

function switchMode(next: LoginMode) {
  if (next === mode.value) return
  mode.value = next
  clearScanTimers()
  if (next === 'scan') void startScan()
}

async function startScan() {
  clearScanTimers()
  scan.loading = true
  scan.status = 'loading'
  scan.qrDataUrl = ''
  scan.ticket = ''
  scan.secret = ''
  scan.code = ''
  scan.expiresIn = 0
  try {
    const res: any = await authApi.createScanLogin()
    scan.ticket = res.ticket
    scan.secret = res.secret
    scan.code = res.code
    scan.expiresIn = Number(res.expires_in) || 120
    scan.qrDataUrl = await QRCode.toDataURL(String(res.qr_text), {
      margin: 1,
      width: 220,
      color: { dark: '#1f2329', light: '#ffffff' },
    })
    scan.status = 'pending'
    scan.loading = false
    pollTimer = window.setInterval(() => {
      void pollScan()
    }, 2000)
    countdownTimer = window.setInterval(() => {
      if (scan.status !== 'pending') return
      scan.expiresIn -= 1
      if (scan.expiresIn <= 0) {
        scan.status = 'expired'
        clearScanTimers()
      }
    }, 1000)
  } catch (e: any) {
    scan.loading = false
    scan.status = 'expired'
    ElMessage.error(e?.response?.data?.detail || '二维码生成失败，请重试')
  }
}

async function pollScan() {
  if (!scan.ticket || scan.status !== 'pending') return
  try {
    const res: any = await authApi.scanLoginStatus(scan.ticket, scan.secret)
    if (res?.status === 'confirmed') {
      clearScanTimers()
      scan.status = 'confirmed'
      auth.setSession(res)
      ElMessage.success('登录成功')
      router.push(auth.userInfo?.must_change_password ? '/force-password' : '/dashboard')
      return
    }
    if (res?.status === 'expired' || res?.status === 'used') {
      scan.status = res.status === 'used' ? 'used' : 'expired'
      clearScanTimers()
    }
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 403 || status === 404) {
      scan.status = 'expired'
      clearScanTimers()
    }
    // 网络抖动继续轮询，不打断
  }
}

function goForgot() {
  router.push('/forgot-password')
}

async function handleSubmit() {
  if (!form.username) {
    ElMessage.warning('请输入用户名')
    return
  }
  if (!form.password) {
    ElMessage.warning('请输入密码')
    return
  }
  // role 允许留空 —— 服务端按账号真实角色自动判定（消除选错角色登不上）
  loading.value = true
  try {
    await auth.login(form.username, form.password, form.role || undefined)
    ElMessage.success('登录成功')
    // 管理员把账号重置为一次性临时口令时，先强制去改密页
    router.push(auth.userInfo?.must_change_password ? '/force-password' : '/dashboard')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  min-height: 100vh;
}
.login-left {
  width: 45%;
  background: linear-gradient(135deg, #1a3a5c 0%, #0d2137 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}
.brand h1 {
  font-size: 48px;
  color: #fff;
  margin: 18px 0 12px;
  letter-spacing: 4px;
}
.brand {
  text-align: center;
}
.brand-logo-window {
  width: 220px;
  height: 220px;
  margin: 0 auto;
  overflow: hidden;
  border-radius: 32px;
  background: #fff;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
}
.brand-logo {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
}
.brand p {
  color: rgba(255,255,255,0.7);
  font-size: 18px;
  margin: 0;
}
.login-right {
  width: 55%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  padding: 40px;
}
.login-card {
  width: 420px;
  border-radius: 8px;
}
.login-card h2 {
  margin: 0;
  text-align: center;
  font-size: 22px;
  color: #333;
}

/* 登录方式切换 */
.login-tabs {
  display: flex;
  gap: 24px;
  justify-content: center;
  margin: 4px 0 18px;
  border-bottom: 1px solid #ebeef5;
}
.login-tab {
  position: relative;
  padding: 0 0 10px;
  border: 0;
  background: none;
  font-size: 14px;
  color: #909399;
  cursor: pointer;
}
.login-tab.active {
  color: var(--el-color-primary);
  font-weight: 600;
}
.login-tab.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 2px;
  background: var(--el-color-primary);
}

/* 扫码登录 */
.scan-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 2px 0 4px;
}
.scan-qr {
  position: relative;
  width: 220px;
  height: 220px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
.scan-qr-img {
  display: block;
  width: 100%;
  height: 100%;
}
.scan-qr-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-size: 12px;
  color: #c0c4cc;
}
.scan-qr-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(255, 255, 255, 0.92);
  font-size: 13px;
  color: #606266;
}
.scan-qr-mask p {
  margin: 0;
}
.scan-qr-mask.success {
  color: #67c23a;
}
.scan-code {
  margin: 14px 0 0;
  font-size: 13px;
  color: #606266;
}
.scan-code strong {
  margin-left: 4px;
  font-size: 18px;
  letter-spacing: 2px;
  color: #1f2329;
}
.scan-tip {
  margin: 8px 0 0;
  font-size: 12px;
  color: #909399;
  text-align: center;
}
.scan-tip.sub {
  margin-top: 4px;
  color: #b3b8bf;
}
.scan-countdown {
  margin: 8px 0 0;
  font-size: 12px;
  color: #b3b8bf;
}
.scan-actions {
  margin-top: 6px;
}
.switch-mode {
  text-align: center;
  margin-top: 16px;
  font-size: 14px;
  color: #999;
}
.forgot-link {
  text-align: right;
  margin: -8px 0 4px;
  font-size: 13px;
}
.forgot-link span {
  color: var(--el-color-primary);
  cursor: pointer;
}
.forgot-link span:hover {
  text-decoration: underline;
}
.role-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #67c23a;
}
@media (max-width: 768px) {
  .login-left {
    display: none;
  }
  .login-right {
    width: 100%;
    padding: 20px;
  }
}
</style>
