<template>
  <div class="profile-page">
    <div class="page-header">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item>首页</el-breadcrumb-item>
        <el-breadcrumb-item>个人中心</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <p class="page-desc">监理端用户个人信息与账号管理</p>

    <el-row :gutter="20">
      <!-- 左侧：头像 + 基本信息 -->
      <el-col :span="8">
        <el-card shadow="never">
          <div class="avatar-section">
            <el-avatar :size="100" :src="userInfo?.avatar_url || ''">
              {{ userInfo?.real_name?.charAt(0) }}
            </el-avatar>
            <h3>{{ userInfo?.real_name }}</h3>
            <p class="id-card">{{ userInfo?.id_card || '**** **** ****' }}</p>
            <el-button type="primary" @click="showEditDialog">编辑资料</el-button>
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：详细信息 -->
      <el-col :span="16">
        <el-card shadow="never">
          <el-tabs v-model="activeTab">
            <!-- 基本资料 -->
            <el-tab-pane label="基本资料" name="basic">
              <el-descriptions :column="2" border>
                <el-descriptions-item label="真实姓名">{{ userInfo?.real_name }}</el-descriptions-item>
                <el-descriptions-item label="手机号">{{ userInfo?.phone }}</el-descriptions-item>
                <el-descriptions-item label="证件类型">{{ userInfo?.certificate_type || '-' }}</el-descriptions-item>
                <el-descriptions-item label="证件编号">{{ userInfo?.certificate_no }}</el-descriptions-item>
                <el-descriptions-item label="性别">{{ userInfo?.gender || '-' }}</el-descriptions-item>
                <el-descriptions-item label="邮箱">{{ userInfo?.email || '-' }}</el-descriptions-item>
                <el-descriptions-item label="个人简介" :span="2">{{ userInfo?.remark || '-' }}</el-descriptions-item>
              </el-descriptions>
            </el-tab-pane>

            <!-- 账号安全 -->
            <el-tab-pane label="账号安全" name="security">
              <div class="security-list">
                <div class="sec-item">
                  <span><el-icon><Lock /></el-icon> 登录密码</span>
                  <el-button size="small" text type="primary" @click="passwordVisible = true">修改密码</el-button>
                </div>
                <div class="sec-item">
                  <span><el-icon><Iphone /></el-icon> 绑定手机</span>
                  <el-tag size="small" :type="userInfo?.phone ? 'success' : 'danger'">
                    {{ userInfo?.phone ? '已绑定' : '未绑定' }}
                  </el-tag>
                </div>
                <div class="sec-item">
                  <span><el-icon><Message /></el-icon> 绑定邮箱</span>
                  <el-tag size="small" :type="userInfo?.email ? 'success' : 'danger'">
                    {{ userInfo?.email ? '已绑定' : '未绑定' }}
                  </el-tag>
                </div>
                <div class="sec-item">
                  <span><el-icon><Key /></el-icon> 密保问题</span>
                  <div class="sec-right">
                    <el-tag size="small" :type="securityIsSet ? 'success' : 'danger'">
                      {{ securityIsSet ? `已设置 ${securityQuestions.length} 题` : '未设置' }}
                    </el-tag>
                    <el-button size="small" text type="primary" @click="openSecurity">
                      {{ securityIsSet ? '修改' : '设置' }}
                    </el-button>
                  </div>
                </div>
                <div class="sec-hint">
                  设置密保问题后，忘记密码时可在登录页自助重置，无需联系管理员。
                </div>
              </div>
            </el-tab-pane>

            <!-- 操作日志 -->
            <el-tab-pane label="操作日志" name="logs">
              <el-empty description="暂无操作记录" />
            </el-tab-pane>

            <!-- 派单日志 -->
            <el-tab-pane label="派单日志" name="tasks">
              <el-empty description="暂无派单记录" />
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-col>
    </el-row>

    <!-- 编辑资料弹窗 -->
    <el-dialog v-model="editVisible" title="编辑资料" width="500px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="姓名"><el-input v-model="editForm.real_name" /></el-form-item>
        <el-form-item label="手机"><el-input v-model="editForm.phone" /></el-form-item>
        <el-form-item label="性别"><el-radio-group v-model="editForm.gender"><el-radio value="男">男</el-radio><el-radio value="女">女</el-radio></el-radio-group></el-form-item>
        <el-form-item label="邮箱"><el-input v-model="editForm.email" /></el-form-item>
        <el-form-item label="简介"><el-input v-model="editForm.remark" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="saveProfile">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="passwordVisible" title="修改密码" width="460px">
      <el-form label-width="90px">
        <el-form-item label="原密码"><el-input v-model="passwordForm.current_password" type="password" show-password /></el-form-item>
        <el-form-item label="新密码"><el-input v-model="passwordForm.new_password" type="password" show-password /></el-form-item>
        <el-form-item label="确认密码"><el-input v-model="passwordForm.confirm_password" type="password" show-password /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordVisible = false">取消</el-button>
        <el-button type="primary" :loading="passwordSaving" @click="changePassword">确认修改</el-button>
      </template>
    </el-dialog>

    <!-- 密保问题（「忘记密码」自助重置的依据） -->
    <el-dialog v-model="securityVisible" title="设置密保问题" width="580px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="sec-tip"
        title="密保问题用于「忘记密码」时自助重置密码"
        description="答案不区分大小写；出于安全考虑，已设置的答案不会回显，修改时需重新填写全部答案。"
      />
      <el-form label-width="90px">
        <el-form-item label="当前密码">
          <el-input v-model="securityForm.current_password" type="password" show-password placeholder="请输入当前登录密码" />
        </el-form-item>
        <div v-for="(item, idx) in securityForm.questions" :key="idx" class="q-row">
          <el-form-item :label="`问题 ${idx + 1}`">
            <el-input v-model="item.question" placeholder="如：我的小学名称" maxlength="256" />
          </el-form-item>
          <el-form-item label="答案">
            <el-input v-model="item.answer" placeholder="请输入答案" maxlength="128" />
          </el-form-item>
          <div v-if="securityForm.questions.length > 1" class="q-del">
            <el-button link type="danger" @click="removeQuestion(idx)">删除本题</el-button>
          </div>
        </div>
        <el-button v-if="securityForm.questions.length < 3" link type="primary" @click="addQuestion">
          + 添加一个问题
        </el-button>
      </el-form>
      <template #footer>
        <el-button @click="securityVisible = false">取消</el-button>
        <el-button type="primary" :loading="securitySaving" @click="saveSecurity">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api'
import { Lock, Iphone, Message, Key } from '@element-plus/icons-vue'

const auth = useAuthStore()
const userInfo = ref<any>(null)
const activeTab = ref('basic')
const editVisible = ref(false)
const passwordVisible = ref(false)
const passwordSaving = ref(false)

// ---- 密保问题（「忘记密码」自助重置的依据）----
const securityVisible = ref(false)
const securitySaving = ref(false)
const securityIsSet = ref(false)
const securityQuestions = ref<string[]>([])
const securityForm = reactive({
  current_password: '',
  questions: [{ question: '', answer: '' }] as { question: string; answer: string }[],
})

const editForm = reactive({
  real_name: '', phone: '', gender: '', email: '', remark: '',
})
const passwordForm = reactive({
  current_password: '', new_password: '', confirm_password: '',
})

onMounted(async () => {
  try {
    const res = await authApi.me() as any
    userInfo.value = res
  } catch {}
  await loadSecurityQuestions()
})

async function loadSecurityQuestions() {
  try {
    const res: any = await authApi.securityQuestions()
    securityIsSet.value = Boolean(res?.is_set)
    securityQuestions.value = res?.questions || []
  } catch {
    securityIsSet.value = false
    securityQuestions.value = []
  }
}

function openSecurity() {
  securityForm.current_password = ''
  // 已设置的答案不回显（后端只回题目）→ 预填题目、答案留空重填
  securityForm.questions = securityQuestions.value.length
    ? securityQuestions.value.map((q) => ({ question: q, answer: '' }))
    : [{ question: '', answer: '' }]
  securityVisible.value = true
}

function addQuestion() {
  if (securityForm.questions.length >= 3) return
  securityForm.questions.push({ question: '', answer: '' })
}

function removeQuestion(idx: number) {
  securityForm.questions.splice(idx, 1)
  if (!securityForm.questions.length) securityForm.questions.push({ question: '', answer: '' })
}

async function saveSecurity() {
  if (!securityForm.current_password) return ElMessage.warning('请输入当前密码')
  const items = securityForm.questions
    .map((item) => ({ question: item.question.trim(), answer: item.answer.trim() }))
    .filter((item) => item.question && item.answer)
  if (!items.length) return ElMessage.warning('请至少完整填写一组问题与答案')
  if (items.length !== securityForm.questions.length) {
    return ElMessage.warning('每个密保问题都必须填写答案')
  }
  const qs = items.map((item) => item.question)
  if (new Set(qs).size !== qs.length) return ElMessage.warning('密保问题不能重复')
  securitySaving.value = true
  try {
    await authApi.setSecurityQuestions({
      current_password: securityForm.current_password,
      questions: items,
    })
    ElMessage.success('密保问题已保存')
    securityVisible.value = false
    await loadSecurityQuestions()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    securitySaving.value = false
  }
}

function showEditDialog() {
  if (userInfo.value) {
    editForm.real_name = userInfo.value.real_name || ''
    editForm.phone = userInfo.value.phone || ''
    editForm.gender = userInfo.value.gender || ''
    editForm.email = userInfo.value.email || ''
    editForm.remark = userInfo.value.remark || ''
  }
  editVisible.value = true
}

async function saveProfile() {
  try {
    await authApi.updateProfile(editForm)
    ElMessage.success('资料更新成功')
    editVisible.value = false
    await auth.fetchUserInfo()
    userInfo.value = auth.userInfo
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '更新失败')
  }
}

async function changePassword() {
  if (!passwordForm.current_password || !passwordForm.new_password) {
    return ElMessage.warning('请完整填写密码')
  }
  if (passwordForm.new_password.length < 8) {
    return ElMessage.warning('新密码至少 8 位')
  }
  if (passwordForm.new_password !== passwordForm.confirm_password) {
    return ElMessage.warning('两次输入的新密码不一致')
  }
  passwordSaving.value = true
  try {
    await authApi.changePassword({
      current_password: passwordForm.current_password,
      new_password: passwordForm.new_password,
    })
    ElMessage.success('密码修改成功')
    passwordVisible.value = false
    Object.assign(passwordForm, { current_password: '', new_password: '', confirm_password: '' })
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '密码修改失败')
  } finally {
    passwordSaving.value = false
  }
}
</script>

<style scoped>
.page-header { margin-bottom: 8px; }
.page-desc { color: #999; font-size: 13px; margin-bottom: 16px; }
.avatar-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 24px 0;
}
.avatar-section h3 { margin: 0; font-size: 18px; }
.id-card { color: #999; font-size: 13px; margin: 0; }

.security-list .sec-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 0; border-bottom: 1px solid #f0f0f0;
}
.security-list .sec-item span { display: flex; align-items: center; gap: 8px; font-size: 14px; }
.sec-right { display: flex; align-items: center; gap: 8px; }
.sec-hint { font-size: 12px; color: #909399; margin-top: 12px; line-height: 1.6; }
.sec-tip { margin-bottom: 16px; }
.q-row { border-top: 1px dashed #ebeef5; padding-top: 12px; margin-top: 4px; }
.q-del { text-align: right; margin: -8px 0 8px; }
</style>
