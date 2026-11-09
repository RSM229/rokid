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

        <el-form :model="form" label-position="top" @submit.prevent="handleSubmit">
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

        <div class="switch-mode">
          <span>账号由管理员统一创建；成员经「项目邀请码」加入项目</span>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api'
import { DEFAULT_ROLE, ROLE_LABELS, ROLE_KEYS } from '@/constants/role'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
  role: '', // 登录模式默认空 = 服务端按账号真实岗位自动判定
})

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
