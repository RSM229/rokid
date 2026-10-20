<template>
  <div class="forgot-container">
    <div class="forgot-left">
      <div class="brand">
        <div class="brand-logo-window">
          <img src="/baize-logo.png" alt="白泽 Logo" class="brand-logo" />
        </div>
        <h1>白泽</h1>
        <p>AI 工程监理智能体</p>
      </div>
    </div>

    <div class="forgot-right">
      <el-card class="forgot-card" shadow="never">
        <template #header>
          <h2>忘记密码</h2>
        </template>

        <el-steps :active="step" simple class="steps">
          <el-step title="验证账号" />
          <el-step title="回答密保" />
          <el-step title="设置新密码" />
        </el-steps>

        <!-- 第一步：输入用户名 -->
        <template v-if="step === 0">
          <el-form label-position="top" @submit.prevent="fetchQuestions">
            <el-form-item label="用户名">
              <el-input
                v-model="username"
                placeholder="请输入你的登录用户名"
                prefix-icon="User"
                @keyup.enter="fetchQuestions"
              />
            </el-form-item>
          </el-form>
          <div class="hint">
            如果你还没有设置过密保问题，将无法自助重置，请联系管理员重置密码。
          </div>
        </template>

        <!-- 第二步：回答密保问题 -->
        <template v-else-if="step === 1">
          <div class="hint">请回答你预设的密保问题（不区分大小写）。</div>
          <el-form label-position="top" @submit.prevent="verifyAnswers">
            <el-form-item
              v-for="(q, idx) in questions"
              :key="idx"
              :label="`${idx + 1}. ${q}`"
            >
              <el-input
                v-model="answers[idx]"
                placeholder="请输入答案"
                @keyup.enter="verifyAnswers"
              />
            </el-form-item>
          </el-form>
        </template>

        <!-- 第三步：设置新密码 -->
        <template v-else-if="step === 2">
          <el-form label-position="top" @submit.prevent="resetPassword">
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
                @keyup.enter="resetPassword"
              />
            </el-form-item>
          </el-form>
        </template>

        <!-- 完成 -->
        <template v-else>
          <el-result icon="success" title="密码已重置" sub-title="请使用新密码登录">
            <template #extra>
              <el-button type="primary" @click="goLogin">返回登录</el-button>
            </template>
          </el-result>
        </template>

        <div v-if="step < 3" class="actions">
          <el-button v-if="step > 0" @click="step -= 1">上一步</el-button>
          <el-button
            type="primary"
            :loading="loading"
            @click="step === 0 ? fetchQuestions() : step === 1 ? verifyAnswers() : resetPassword()"
          >
            {{ step === 0 ? '下一步' : step === 1 ? '验证' : '重置密码' }}
          </el-button>
        </div>

        <div class="switch-mode">
          <span @click="goLogin">返回登录</span>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api'

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
  router.push('/login')
}

async function fetchQuestions() {
  const name = username.value.trim()
  if (!name) return ElMessage.warning('请输入用户名')
  loading.value = true
  try {
    const res: any = await authApi.forgotQuestions(name)
    if (!res?.available) {
      ElMessage.warning(res?.message || '该账号未设置密保问题，请联系管理员重置密码')
      return
    }
    username.value = name
    questions.value = res.questions || []
    answers.value = new Array(questions.value.length).fill('')
    step.value = 1
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '查询失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function verifyAnswers() {
  if (answers.value.some((a) => !a.trim())) {
    return ElMessage.warning('请填写全部密保答案')
  }
  loading.value = true
  try {
    const res: any = await authApi.forgotVerify(username.value, answers.value)
    resetToken.value = res.reset_token
    step.value = 2
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '密保答案不正确')
  } finally {
    loading.value = false
  }
}

async function resetPassword() {
  if (newPassword.value.length < 8) {
    return ElMessage.warning('新密码至少 8 位')
  }
  if (newPassword.value !== confirmPassword.value) {
    return ElMessage.warning('两次输入的新密码不一致')
  }
  loading.value = true
  try {
    await authApi.forgotReset(resetToken.value, newPassword.value)
    ElMessage.success('密码已重置，请用新密码登录')
    step.value = 3
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '重置失败，请重新验证密保问题')
    // 凭证失效等异常 → 回到密保验证步重新获取
    if ([400, 401].includes(e.response?.status)) {
      resetToken.value = ''
      step.value = 1
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.forgot-container {
  display: flex;
  min-height: 100vh;
}
.forgot-left {
  width: 45%;
  background: linear-gradient(135deg, #1a3a5c 0%, #0d2137 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}
.brand { text-align: center; }
.brand h1 {
  font-size: 48px;
  color: #fff;
  margin: 18px 0 12px;
  letter-spacing: 4px;
}
.brand p { color: rgba(255, 255, 255, 0.7); font-size: 18px; margin: 0; }
.brand-logo-window {
  width: 220px;
  height: 220px;
  margin: 0 auto;
  overflow: hidden;
  border-radius: 32px;
  background: #fff;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.28);
}
.brand-logo { width: 100%; height: 100%; object-fit: cover; object-position: center top; }
.forgot-right {
  width: 55%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f5f7fa;
  padding: 40px;
}
.forgot-card { width: 460px; border-radius: 8px; }
.forgot-card h2 { margin: 0; text-align: center; font-size: 22px; color: #333; }
.steps { margin-bottom: 20px; }
.hint { font-size: 12px; color: #909399; margin-bottom: 12px; line-height: 1.6; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.switch-mode {
  text-align: center;
  margin-top: 16px;
  font-size: 14px;
  color: #999;
}
.switch-mode span { color: #409eff; cursor: pointer; }
.switch-mode span:hover { text-decoration: underline; }
@media (max-width: 768px) {
  .forgot-left { display: none; }
  .forgot-right { width: 100%; padding: 20px; }
}
</style>
