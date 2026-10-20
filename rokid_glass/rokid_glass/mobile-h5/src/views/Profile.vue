<script setup lang="ts">
/**
 * 创建日期：2026-07-18
 * 个人中心：用户信息、角色与权限、项目切换、个人信息编辑、修改密码、我的签章、离线同步、退出登录
 * 更新日期：2026-07-19 补齐个人信息编辑/改密/签章/台账查询入口
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  showConfirmDialog,
  showSuccessToast,
  showFailToast,
} from 'vant'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { authApi, projectApi } from '@/api'
import { ROLES, isChief, isChiefOrDeputy } from '@/utils/permission'
import { hapticLight, hapticSuccess } from '@/utils/haptic'

const router = useRouter()
const auth = useAuthStore()
const appStore = useAppStore()

const projects = ref<any[]>([])
const showProjectPicker = ref(false)
const syncing = ref(false)

const roleShort = ref('')

// 个人信息编辑
const showProfileEdit = ref(false)
const profileForm = ref({ name: '', phone: '', email: '' })
const savingProfile = ref(false)

// 修改密码
const showPasswordEdit = ref(false)
const passwordForm = ref({ current_password: '', new_password: '', confirm_password: '' })
const savingPassword = ref(false)

function computeRoleShort() {
  const map: Record<string, string> = {
    [ROLES.CHIEF]: '总监',
    [ROLES.DEPUTY]: '总代',
    [ROLES.PROFESSIONAL]: '专监',
    [ROLES.SUPERVISOR]: '监理员',
  }
  roleShort.value = map[auth.user?.role ?? ''] ?? auth.user?.role ?? '-'
}

async function loadProjects() {
  try {
    const res: any = await projectApi.list()
    projects.value = Array.isArray(res) ? res : res?.items ?? []
  } catch {
    projects.value = []
  }
}

// 修复：项目列表异步加载，使用 computed 保证 Picker 列随数据更新
const projectColumns = computed(() =>
  projects.value.map((p) => ({
    text: p.name || p.project_name,
    value: p.id,
  })),
)

async function onProjectConfirm({ selectedOptions }: { selectedOptions: any[] }) {
  showProjectPicker.value = false
  const sel = selectedOptions[0]
  if (!sel) return
  try {
    await projectApi.switch(sel.value)
    auth.setProject(sel.value, sel.text)
    showSuccessToast(`已切换到：${sel.text}`)
  } catch {
    showFailToast('项目切换失败')
  }
}

// 打开个人信息编辑
function openProfileEdit() {
  profileForm.value = {
    name: auth.user?.name || '',
    phone: (auth.user as any)?.phone || '',
    email: (auth.user as any)?.email || '',
  }
  showProfileEdit.value = true
}

async function onSaveProfile() {
  if (!profileForm.value.name.trim()) {
    showFailToast('请填写姓名')
    return
  }
  savingProfile.value = true
  try {
    await authApi.updateProfile({
      real_name: profileForm.value.name,
      phone: profileForm.value.phone,
      email: profileForm.value.email,
    })
    // 更新本地用户信息
    if (auth.user) {
      auth.user = {
        ...auth.user,
        name: profileForm.value.name,
        ...(profileForm.value.phone ? { phone: profileForm.value.phone } as any : {}),
        ...(profileForm.value.email ? { email: profileForm.value.email } as any : {}),
      }
      localStorage.setItem('mobileUser', JSON.stringify(auth.user))
    }
    showSuccessToast('已保存')
    showProfileEdit.value = false
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '保存失败')
  } finally {
    savingProfile.value = false
  }
}

// 修改密码
function openPasswordEdit() {
  passwordForm.value = { current_password: '', new_password: '', confirm_password: '' }
  showPasswordEdit.value = true
}

async function onSavePassword() {
  const { current_password, new_password, confirm_password } = passwordForm.value
  if (!current_password || !new_password) {
    showFailToast('请填写原密码和新密码')
    return
  }
  if (new_password.length < 6) {
    showFailToast('新密码至少 6 位')
    return
  }
  if (new_password !== confirm_password) {
    showFailToast('两次输入的密码不一致')
    return
  }
  savingPassword.value = true
  try {
    await authApi.changePassword({ current_password, new_password })
    showSuccessToast('密码已修改')
    showPasswordEdit.value = false
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '修改失败')
  } finally {
    savingPassword.value = false
  }
}

// 密保问题（「忘记密码」自助重置的依据）
const showSecurityEdit = ref(false)
const savingSecurity = ref(false)
const securityIsSet = ref(false)
const securityQuestions = ref<string[]>([])
const securityForm = ref({
  current_password: '',
  questions: [{ question: '', answer: '' }] as { question: string; answer: string }[],
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
  securityForm.value.current_password = ''
  securityForm.value.questions = securityQuestions.value.length
    ? securityQuestions.value.map((q) => ({ question: q, answer: '' }))
    : [{ question: '', answer: '' }]
  showSecurityEdit.value = true
}

function addSecurityQuestion() {
  if (securityForm.value.questions.length >= 3) return
  securityForm.value.questions.push({ question: '', answer: '' })
}

function removeSecurityQuestion() {
  if (securityForm.value.questions.length <= 1) return
  securityForm.value.questions.pop()
}

async function onSaveSecurity() {
  const { current_password, questions } = securityForm.value
  if (!current_password) return showFailToast('请输入当前密码')
  const items = questions
    .map((item) => ({ question: item.question.trim(), answer: item.answer.trim() }))
    .filter((item) => item.question && item.answer)
  if (!items.length) return showFailToast('请至少完整填写一组问题与答案')
  if (items.length !== questions.length) return showFailToast('每个密保问题都必须填写答案')
  const qs = items.map((item) => item.question)
  if (new Set(qs).size !== qs.length) return showFailToast('密保问题不能重复')
  savingSecurity.value = true
  try {
    await authApi.setSecurityQuestions({ current_password, questions: items })
    showSuccessToast('密保问题已保存')
    showSecurityEdit.value = false
    await loadSecurityQuestions()
  } catch (e: any) {
    showFailToast(e?.response?.data?.detail || '保存失败')
  } finally {
    savingSecurity.value = false
  }
}

// 我的签章（如权限允许）
const canManageSignature = computed(() => isChief() || isChiefOrDeputy())

function goSignature() {
  hapticLight()
  // 签章管理暂无独立页面，提示信息
  showFailToast('签章管理请在 PC 端操作')
}

async function onSync() {
  if (syncing.value) return
  syncing.value = true
  try {
    await appStore.trySync()
    showSuccessToast('同步完成')
  } catch {
    showFailToast('同步失败')
  } finally {
    syncing.value = false
  }
}

function onLogout() {
  hapticLight()
  showConfirmDialog({
    title: '退出登录',
    message: '确定要退出当前账号吗？',
  })
    .then(() => {
      auth.logout()
      hapticSuccess()
      router.replace('/login')
    })
    .catch(() => {})
}

onMounted(() => {
  computeRoleShort()
  loadProjects()
  loadSecurityQuestions()
  appStore.refreshPendingCount()
})
</script>

<template>
  <div class="profile-page">
    <!-- 用户头部 -->
    <div class="profile-header">
      <van-image round width="64" height="64" :src="auth.user?.avatar_url" fit="cover">
        <template #error>
          <div class="avatar-fallback">{{ auth.user?.name?.charAt(0) || '监' }}</div>
        </template>
      </van-image>
      <div class="profile-name">{{ auth.user?.name || '监理员' }}</div>
      <div class="profile-sub">
        <van-tag type="primary" size="medium">{{ roleShort }}</van-tag>
        <span class="profile-id">@{{ auth.user?.username }}</span>
      </div>
    </div>

    <!-- 角色与权限 -->
    <van-cell-group inset title="角色与权限">
      <van-cell title="当前角色" :value="auth.user?.role" />
      <van-cell title="所属项目" :value="auth.user?.project_name || '未绑定'" is-link @click="showProjectPicker = true" />
      <van-cell title="权限数量" :value="`${auth.permissions.length} 项`" />
    </van-cell-group>

    <!-- 账号管理 -->
    <van-cell-group inset title="账号管理">
      <van-cell title="个人信息" label="姓名、手机号、邮箱" is-link @click="openProfileEdit" />
      <van-cell title="修改密码" is-link @click="openPasswordEdit" />
      <van-cell
        title="密保问题"
        :label="securityIsSet ? `已设置 ${securityQuestions.length} 题` : '未设置，忘记密码时无法自助重置'"
        is-link
        @click="openSecurity"
      />
      <van-cell v-if="canManageSignature" title="我的签章" label="电子签章管理" is-link @click="goSignature" />
    </van-cell-group>

    <!-- 离线数据 -->
    <van-cell-group inset title="离线数据">
      <van-cell title="在线状态">
        <template #value>
          <van-tag :type="appStore.online ? 'success' : 'warning'">
            {{ appStore.online ? '在线' : '离线' }}
          </van-tag>
        </template>
      </van-cell>
      <van-cell title="待同步" :value="`${appStore.pendingSyncCount} 条`" />
      <van-cell
        title="立即同步"
        is-link
        :value="syncing ? '同步中...' : ''"
        @click="onSync"
      />
    </van-cell-group>

    <!-- 功能入口 -->
    <van-cell-group inset title="功能">
      <van-cell title="通知中心" is-link @click="$router.push('/notifications')" />
      <van-cell title="台账查询" is-link @click="$router.push('/ledger')" />
      <van-cell title="验收流程指引" is-link @click="$router.push('/knowledge/guide')" />
    </van-cell-group>

    <!-- 退出登录 -->
    <div class="logout-wrap">
      <van-button block round type="danger" plain @click="onLogout">退出登录</van-button>
    </div>

    <!-- 项目切换 Picker -->
    <van-popup v-model:show="showProjectPicker" position="bottom" round>
      <van-picker
        :columns="projectColumns"
        @confirm="onProjectConfirm"
        @cancel="showProjectPicker = false"
      />
    </van-popup>

    <!-- 个人信息编辑 -->
    <van-popup v-model:show="showProfileEdit" position="bottom" round :style="{ height: '50%' }">
      <div class="popup-header">编辑个人信息</div>
      <van-cell-group inset>
        <van-field v-model="profileForm.name" label="姓名" placeholder="请输入姓名" required />
        <van-field v-model="profileForm.phone" label="手机号" placeholder="请输入手机号" type="tel" />
        <van-field v-model="profileForm.email" label="邮箱" placeholder="请输入邮箱" />
      </van-cell-group>
      <div class="popup-actions">
        <van-button block type="primary" :loading="savingProfile" @click="onSaveProfile">保存</van-button>
      </div>
    </van-popup>

    <!-- 修改密码 -->
    <van-popup v-model:show="showPasswordEdit" position="bottom" round :style="{ height: '50%' }">
      <div class="popup-header">修改密码</div>
      <van-cell-group inset>
        <van-field v-model="passwordForm.current_password" label="原密码" placeholder="请输入原密码" type="password" required />
        <van-field v-model="passwordForm.new_password" label="新密码" placeholder="至少 6 位" type="password" required />
        <van-field v-model="passwordForm.confirm_password" label="确认密码" placeholder="请再次输入新密码" type="password" required />
      </van-cell-group>
      <div class="popup-actions">
        <van-button block type="primary" :loading="savingPassword" @click="onSavePassword">确认修改</van-button>
      </div>
    </van-popup>

    <!-- 密保问题 -->
    <van-popup
      v-model:show="showSecurityEdit"
      position="bottom"
      round
      :style="{ height: '78%', overflowY: 'auto' }"
    >
      <div class="popup-header">设置密保问题</div>
      <van-notice-bar
        wrapable
        :scrollable="false"
        text="密保问题用于「忘记密码」时自助重置密码；答案不区分大小写，已设置的答案不会回显。"
      />
      <van-cell-group inset>
        <van-field
          v-model="securityForm.current_password"
          label="当前密码"
          placeholder="请输入当前登录密码"
          type="password"
          required
        />
        <template v-for="(item, idx) in securityForm.questions" :key="idx">
          <van-field
            v-model="item.question"
            :label="`问题${idx + 1}`"
            placeholder="如：我的小学名称"
            maxlength="256"
          />
          <van-field
            v-model="item.answer"
            label="答案"
            placeholder="请输入答案"
            maxlength="128"
          />
        </template>
      </van-cell-group>
      <div class="security-ops">
        <van-button
          v-if="securityForm.questions.length < 3"
          size="small"
          plain
          type="primary"
          @click="addSecurityQuestion"
        >
          + 添加一个问题
        </van-button>
        <van-button
          v-if="securityForm.questions.length > 1"
          size="small"
          plain
          type="danger"
          @click="removeSecurityQuestion"
        >
          删除最后一个
        </van-button>
      </div>
      <div class="popup-actions">
        <van-button block type="primary" :loading="savingSecurity" @click="onSaveSecurity">保存</van-button>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.profile-page {
  padding: 0 0 24px;
}
.profile-header {
  background: linear-gradient(135deg, #1989fa, #0f6fd1);
  color: #fff;
  padding: 24px 16px calc(env(safe-area-inset-top) + 24px);
  text-align: center;
}
.avatar-fallback {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
  font-size: 28px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.profile-name {
  margin-top: 10px;
  font-size: 18px;
  font-weight: 600;
}
.profile-sub {
  margin-top: 6px;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.profile-id {
  opacity: 0.85;
}
:deep(.van-cell-group) {
  margin-top: 14px;
}
.logout-wrap {
  padding: 24px 16px 0;
}
.popup-header {
  padding: 16px;
  font-size: 16px;
  font-weight: 600;
  text-align: center;
}
.popup-actions {
  padding: 16px;
}
.security-ops {
  display: flex;
  gap: 10px;
  padding: 12px 16px 0;
}
</style>
