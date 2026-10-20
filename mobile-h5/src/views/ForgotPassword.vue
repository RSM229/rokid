<script setup lang="ts">
/**
 * 忘记密码 —— 密保问题自助重置（移动端）
 * 步骤：输入用户名 → 回答密保问题 → 设置新密码
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { showFailToast, showSuccessToast } from 'vant'
import { authApi } from '@/api'
import { hapticSuccess } from '@/utils/haptic'

const router = useRouter()
const step = ref(0)
const loading = ref(false)
const username = ref('')
const questions = ref<string[]>([])
const answers = ref<string[]>([])
const resetToken = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

function goLogin() {
  router.replace('/login')
}

async function fetchQuestions() {
  const name = username.value.trim()
  if (!name) return showFailToast('请输入用户名')
  loading.value = true
  try {
    const res: any = await authApi.forgotQuestions(name)
    if (!res?.available) {
      showFailToast(res?.message || '该账号未设置密保问题，请联系管理员')
      return
    }
    username.value = name
    questions.value = res.questions || []
    answers.value = new Array(questions.value.length).fill('')
    step.value = 1
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '查询失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function verifyAnswers() {
  if (answers.value.some((a) => !a.trim())) return showFailToast('请填写全部密保答案')
  loading.value = true
  try {
    const res: any = await authApi.forgotVerify(username.value, answers.value)
    resetToken.value = res.reset_token
    step.value = 2
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '密保答案不正确')
  } finally {
    loading.value = false
  }
}

async function resetPassword() {
  if (newPassword.value.length < 8) return showFailToast('新密码至少 8 位')
  if (newPassword.value !== confirmPassword.value) return showFailToast('两次输入的密码不一致')
  loading.value = true
  try {
    await authApi.forgotReset(resetToken.value, newPassword.value)
    hapticSuccess()
    showSuccessToast('密码已重置')
    step.value = 3
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '重置失败，请重新验证密保问题')
    if ([400, 401].includes(e?.response?.status)) {
      resetToken.value = ''
      step.value = 1
    }
  } finally {
    loading.value = false
  }
}

function onNext() {
  if (step.value === 0) return fetchQuestions()
  if (step.value === 1) return verifyAnswers()
  return resetPassword()
}
</script>

<template>
  <div class="forgot-page">
    <div class="header">
      <h1 class="title">重置密码</h1>
      <p class="subtitle">通过密保问题自助重置</p>
    </div>

    <div class="card">
      <van-steps :active="step" active-color="#1989fa">
        <van-step>验证账号</van-step>
        <van-step>回答密保</van-step>
        <van-step>设置密码</van-step>
      </van-steps>

      <van-cell-group inset>
        <template v-if="step === 0">
          <van-field
            v-model="username"
            label="用户名"
            placeholder="请输入登录用户名"
            left-icon="manager-o"
            clearable
          />
        </template>
        <template v-else-if="step === 1">
          <van-field
            v-for="(q, idx) in questions"
            :key="idx"
            v-model="answers[idx]"
            :label="`问题${idx + 1}`"
            :placeholder="q"
          />
        </template>
        <template v-else-if="step === 2">
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
        </template>
      </van-cell-group>

      <p v-if="step === 0" class="hint">
        未设置过密保问题将无法自助重置，请联系管理员。
      </p>

      <div v-if="step < 3" class="actions">
        <van-button v-if="step > 0" block round plain @click="step -= 1">上一步</van-button>
        <van-button block round type="primary" :loading="loading" @click="onNext">
          {{ step === 0 ? '下一步' : step === 1 ? '验证' : '重置密码' }}
        </van-button>
      </div>

      <van-empty v-else description="密码已重置，请用新密码登录">
        <van-button round type="primary" class="empty-btn" @click="goLogin">返回登录</van-button>
      </van-empty>

      <div class="back-link" @click="goLogin">返回登录</div>
    </div>
  </div>
</template>

<style scoped>
.forgot-page {
  min-height: 100vh;
  min-height: 100dvh;
  background: linear-gradient(160deg, #1989fa 0%, #0f6fd1 60%, #07c160 140%);
  padding: calc(env(safe-area-inset-top) + 40px) 20px 24px;
}
.header {
  text-align: center;
  color: #fff;
  margin-bottom: 24px;
}
.title { margin: 0 0 6px; font-size: 22px; font-weight: 700; }
.subtitle { margin: 0; font-size: 13px; opacity: 0.85; }
.card {
  background: #fff;
  border-radius: 18px;
  padding: 16px 0;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.08);
}
.hint { margin: 12px 16px 0; font-size: 12px; color: #969799; line-height: 1.6; }
.actions { padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.empty-btn { margin-top: 12px; }
.back-link {
  text-align: center;
  margin: 14px 0 4px;
  font-size: 14px;
  color: #1989fa;
}
</style>
