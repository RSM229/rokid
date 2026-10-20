<template>
  <div class="force-container">
    <el-card class="force-card" shadow="never">
      <template #header>
        <h2>请设置新密码</h2>
      </template>

      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="管理员已将你的账号重置为一次性临时口令"
        description="为保障账号安全，请先用临时口令登录后立即设置你自己的新密码。设置完成后才能进入系统。"
        class="tip"
      />

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="当前密码（管理员提供的一次性临时口令）">
          <el-input
            v-model="currentPassword"
            type="password"
            placeholder="请输入临时口令"
            show-password
          />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input
            v-model="newPassword"
            type="password"
            placeholder="至少 8 位"
            show-password
          />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input
            v-model="confirmPassword"
            type="password"
            placeholder="请再次输入新密码"
            show-password
            @keyup.enter="submit"
          />
        </el-form-item>
      </el-form>

      <div class="actions">
        <el-button @click="logout">退出登录</el-button>
        <el-button type="primary" :loading="loading" @click="submit">设置并进入系统</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

onMounted(() => {
  // 标记已解除（例如从别的入口误入）→ 直接回工作台
  if (!auth.userInfo?.must_change_password) {
    router.replace('/dashboard')
  }
})

async function submit() {
  if (!currentPassword.value) return ElMessage.warning('请输入当前密码（临时口令）')
  if (newPassword.value.length < 8) return ElMessage.warning('新密码至少 8 位')
  if (newPassword.value !== confirmPassword.value) {
    return ElMessage.warning('两次输入的新密码不一致')
  }
  loading.value = true
  try {
    await authApi.changePassword({
      current_password: currentPassword.value,
      new_password: newPassword.value,
    })
    auth.clearMustChangePassword()
    ElMessage.success('密码设置成功')
    router.replace('/dashboard')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '设置失败，请核对当前密码')
  } finally {
    loading.value = false
  }
}

function logout() {
  auth.logout()
  router.replace('/login')
}
</script>

<style scoped>
.force-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  padding: 20px;
}
.force-card { width: 460px; border-radius: 8px; }
.force-card h2 { margin: 0; text-align: center; font-size: 20px; color: #333; }
.tip { margin-bottom: 18px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
