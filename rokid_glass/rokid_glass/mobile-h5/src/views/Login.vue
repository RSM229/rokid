<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 登录页：用户名 + 密码 + 角色选择（4 角色），调用 auth.login，成功跳转 /
 */
import { computed, ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { showFailToast, showSuccessToast } from 'vant'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api'
import { ROLE_OPTIONS } from '@/utils/permission'
import { hapticSuccess, hapticError } from '@/utils/haptic'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const username = ref('')
const password = ref('')
const role = ref<string>(ROLE_OPTIONS[0].value)
const showRolePicker = ref(false)
const loading = ref(false)
const serverRoles = ref<typeof ROLE_OPTIONS>([])

const currentRoleLabel = ref(ROLE_OPTIONS[0].label)

// ============ 添加 goRegister 方法 ============
const goRegister = () => {
  router.push('/register')
}
// ============ 添加结束 ============

const goForgot = () => {
  router.push('/forgot-password')
}

onMounted(async () => {
  // 拉取后端角色列表，失败则使用本地默认
  try {
    const res: any = await authApi.roles()
    if (Array.isArray(res?.roles) && res.roles.length > 0) {
      serverRoles.value = res.roles.map((r: any) => ({
        value: r.value,
        label: r.label,
        desc: (r.permissions || []).slice(0, 2).join('、') || '登录后查看完整权限',
      }))
    }
  } catch {
    // 忽略，使用默认角色
  }
})

function onRoleConfirm({ selectedValues }: { selectedValues: string[] }) {
  if (selectedValues[0]) {
    role.value = selectedValues[0]
    const found = (serverRoles.value.length ? serverRoles.value : ROLE_OPTIONS).find(
      (r) => r.value === role.value,
    )
    currentRoleLabel.value = found?.label || role.value
  }
  showRolePicker.value = false
}

async function onSubmit() {
  if (!username.value || !password.value) {
    showFailToast('请输入用户名和密码')
    hapticError()
    return
  }
  loading.value = true
  try {
    await auth.login(username.value, password.value, role.value)
    hapticSuccess()
    showSuccessToast('登录成功')
    // 管理员重置为一次性口令时，先强制改密
    if (auth.user?.must_change_password) {
      router.replace('/force-password')
      return
    }
    const redirect = (route.query.redirect as string) || '/'
    router.replace(redirect)
  } catch (e: any) {
    hapticError()
    const msg = e?.response?.data?.detail || '登录失败，请检查账号或角色'
    showFailToast(msg)
  } finally {
    loading.value = false
  }
}

const roleColumns = computed(() =>
  (serverRoles.value.length ? serverRoles.value : ROLE_OPTIONS).map((r) => ({
    text: `${r.label}（${r.value}）`,
    value: r.value,
  })),
)
</script>

<template>
  <div class="login-page">
    <div class="login-header">
      <div class="logo">
        <van-icon name="shop-o" size="48" color="#fff" />
      </div>
      <h1 class="title">监理便携经办端</h1>
      <p class="subtitle">现场影像 · 文书经办 · 隐患整改</p>
    </div>

    <div class="login-card">
      <van-form @submit="onSubmit">
        <van-cell-group inset>
          <van-field
            v-model="username"
            name="username"
            label="用户名"
            placeholder="请输入用户名"
            left-icon="manager-o"
            clearable
            :rules="[{ required: true, message: '请输入用户名' }]"
          />
          <van-field
            v-model="password"
            type="password"
            name="password"
            label="密码"
            placeholder="请输入密码"
            left-icon="lock"
            :rules="[{ required: true, message: '请输入密码' }]"
          />
          <van-field
            v-model="currentRoleLabel"
            is-link
            readonly
            label="角色"
            placeholder="选择登录角色"
            left-icon="friends-o"
            @click="showRolePicker = true"
          />
        </van-cell-group>

        <div class="login-actions">
          <van-button
            round
            block
            type="primary"
            native-type="submit"
            :loading="loading"
            loading-text="登录中..."
          >
            登录
          </van-button>
        
          <div class="register-link">
            还没有账号？
            <span @click="goRegister">立即注册</span>
          </div>
          <div class="forgot-link">
            <span @click="goForgot">忘记密码？</span>
          </div>
          <p class="tip">提示：admin 账号可任意角色登录，拥有全部权限</p>
        </div>
      </van-form>
    </div>

    <van-popup v-model:show="showRolePicker" position="bottom" round>
      <van-picker
        :columns="roleColumns"
        :default-index="0"
        @confirm="onRoleConfirm"
        @cancel="showRolePicker = false"
      />
    </van-popup>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  min-height: 100dvh;
  background: linear-gradient(160deg, #1989fa 0%, #0f6fd1 60%, #07c160 140%);
  padding: calc(env(safe-area-inset-top) + 48px) 20px 24px;
  display: flex;
  flex-direction: column;
}
.login-header {
  text-align: center;
  color: #fff;
  margin-bottom: 32px;
}
.logo {
  width: 88px;
  height: 88px;
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.18);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
  backdrop-filter: blur(6px);
}
.title {
  margin: 0 0 6px;
  font-size: 24px;
  font-weight: 700;
}
.subtitle {
  margin: 0;
  font-size: 13px;
  opacity: 0.85;
}
.login-card {
  background: #fff;
  border-radius: 18px;
  padding: 16px 0 8px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.08);
}
.login-actions {
  padding: 20px 16px 8px;
}
.tip {
  margin: 14px 0 0;
  font-size: 12px;
  color: #969799;
  text-align: center;
}
.register-link {
  text-align: center;
  margin-top: 12px;
  font-size: 14px;
  color: #666;
}
.register-link span {
  color: #1989fa;
  font-weight: 500;
  cursor: pointer;
}
.register-link span:active {
  opacity: 0.7;
}
.forgot-link {
  text-align: center;
  margin-top: 8px;
  font-size: 14px;
}
.forgot-link span {
  color: #1989fa;
  font-weight: 500;
  cursor: pointer;
}
.forgot-link span:active {
  opacity: 0.7;
}
</style>
