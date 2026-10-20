<script setup lang="ts">
/**
 * 强制改密 —— 管理员把账号重置为一次性临时口令后，用户须先改密
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showFailToast, showSuccessToast } from 'vant'
import { authApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { hapticSuccess } from '@/utils/haptic'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

onMounted(() => {
  if (!auth.user?.must_change_password) {
    router.replace('/')
  }
})

async function onSubmit() {
  if (!currentPassword.value) return showFailToast('请输入当前密码（临时口令）')
  if (newPassword.value.length < 8) return showFailToast('新密码至少 8 位')
  if (newPassword.value !== confirmPassword.value) return showFailToast('两次输入的密码不一致')
  loading.value = true
  try {
    await authApi.changePassword({
      current_password: currentPassword.value,
      new_password: newPassword.value,
    })
    auth.clearMustChangePassword()
    hapticSuccess()
    showSuccessToast('密码设置成功')
    router.replace('/')
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '设置失败，请核对当前密码')
  } finally {
    loading.value = false
  }
}

function onLogout() {
  auth.logout()
  router.replace('/login')
}
</script>

<template>
  <div class="force-page">
    <div class="header">
      <h1 class="title">请设置新密码</h1>
      <p class="subtitle">管理员已把你的密码重置为一次性临时口令</p>
    </div>

    <div class="card">
      <van-notice-bar
        wrapable
        :scrollable="false"
        text="为保障账号安全，请用临时口令登录后立即设置你自己的新密码；设置完成后才能使用系统。"
      />
      <van-cell-group inset>
        <van-field
          v-model="currentPassword"
          type="password"
          label="当前密码"
          placeholder="请输入临时口令"
        />
        <van-field
          v-model="newPassword"
          type="password"
          label="新密码"
          placeholder="至少 8 位"
        />
        <van-field
          v-model="confirmPassword"
          type="password"
          label="确认密码"
          placeholder="请再次输入新密码"
        />
      </van-cell-group>
      <div class="actions">
        <van-button block round plain @click="onLogout">退出登录</van-button>
        <van-button block round type="primary" :loading="loading" @click="onSubmit">
          设置并进入系统
        </van-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.force-page {
  min-height: 100vh;
  min-height: 100dvh;
  background: linear-gradient(160deg, #1989fa 0%, #0f6fd1 60%, #07c160 140%);
  padding: calc(env(safe-area-inset-top) + 40px) 20px 24px;
}
.header { text-align: center; color: #fff; margin-bottom: 24px; }
.title { margin: 0 0 6px; font-size: 22px; font-weight: 700; }
.subtitle { margin: 0; font-size: 13px; opacity: 0.85; }
.card {
  background: #fff;
  border-radius: 18px;
  padding: 12px 0 16px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.08);
}
.actions { padding: 16px; display: flex; flex-direction: column; gap: 10px; }
</style>
