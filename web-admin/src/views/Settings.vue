<template>
  <div class="settings-page">
    <p class="page-desc">管理通知、隐私、安全及服务端模型调用参数</p>

    <el-card shadow="never">
      <el-container>
        <!-- 左侧菜单 -->
        <el-aside width="190px" style="border-right: 1px solid #f0f0f0;">
          <el-menu :default-active="activeMenu" @select="selectMenu" class="settings-menu">
            <el-menu-item index="grp-regular" disabled class="nav-group">常规</el-menu-item>
            <el-menu-item index="notice"><el-icon><Tools /></el-icon>基础设置</el-menu-item>
            <el-menu-item index="notify"><el-icon><Bell /></el-icon>通知设置</el-menu-item>
            <el-menu-item index="privacy"><el-icon><View /></el-icon>隐私设置</el-menu-item>

            <el-menu-item index="grp-secure" disabled class="nav-group">安全与模型</el-menu-item>
            <el-menu-item index="security"><el-icon><Lock /></el-icon>安全设置</el-menu-item>
            <el-menu-item index="model"><el-icon><Cpu /></el-icon>模型调用设置</el-menu-item>

            <el-menu-item index="grp-about" disabled class="nav-group">关于</el-menu-item>
            <el-menu-item index="about"><el-icon><InfoFilled /></el-icon>关于</el-menu-item>
          </el-menu>
        </el-aside>

        <!-- 右侧内容 -->
        <el-main style="padding: 0 24px;">
          <!-- 基础设置 / 通知设置 -->
          <div v-if="activeMenu === 'notice' || activeMenu === 'notify'">
            <h3 class="section-title">消息通知</h3>
            <div class="setting-list">
              <div class="setting-item" v-for="(item, i) in noticeSettings" :key="'n'+i">
                <div class="setting-info">
                  <strong>{{ item.label }}</strong>
                  <span class="desc">{{ item.desc }}</span>
                </div>
                <el-switch v-model="item.value" />
              </div>
            </div>

            <h3 class="section-title" style="margin-top: 32px;">推送方式</h3>
            <div class="setting-list">
              <div class="setting-item" v-for="(item, i) in pushSettings" :key="'p'+i">
                <div class="setting-info">
                  <strong>{{ item.label }}</strong>
                  <span class="desc">{{ item.desc }}</span>
                </div>
                <el-switch v-model="item.value" />
              </div>
            </div>

            <h3 class="section-title" style="margin-top: 32px;">消息通知</h3>
            <div class="setting-list">
              <div class="setting-item">
                <div class="setting-info">
                  <strong>消息通知</strong>
                  <span class="desc">将接收短信/微信消息通知</span>
                </div>
                <el-switch v-model="msgNotify" />
              </div>
            </div>
          </div>

          <!-- 隐私设置 -->
          <div v-if="activeMenu === 'privacy'">
            <h3 class="section-title">隐私选项</h3>
            <div class="setting-list">
              <div class="setting-item">
                <div class="setting-info">
                  <strong>个人资料可见度</strong>
                </div>
                <el-select v-model="privacyLevel" size="small" style="width: 120px;">
                  <el-option label="仅联系人" value="contact" />
                  <el-option label="全部用户" value="all" />
                </el-select>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <strong>在线状态</strong>
                  <span class="desc">显示您的在线状态给其他用户</span>
                </div>
                <el-switch v-model="onlineStatus" />
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <strong>白板服务</strong>
                  <span class="desc">允许使用协作用白板功能（用于专题管理功能）</span>
                </div>
                <el-switch v-model="whiteboardService" />
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <strong>标签记录</strong>
                  <span class="desc">允许记录发送操作行为作为标签来定案计</span>
                </div>
                <el-switch v-model="tagRecord" />
              </div>
            </div>

            <h3 class="section-title" style="margin-top: 32px;">数据管理</h3>
            <div class="setting-list">
              <div class="setting-item">
                <div class="setting-info">
                  <strong>清理缓存</strong>
                  <span class="desc">清理本地数据缓存，释放存储空间</span>
                </div>
                <el-button size="small" type="primary" plain @click="clearCache">清理</el-button>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <strong>导出个人数据</strong>
                  <span class="desc">下载您的所有个人数据</span>
                </div>
                <el-button size="small" plain @click="exportPersonalData">导出</el-button>
              </div>
            </div>
          </div>

          <!-- 安全设置 -->
          <div v-if="activeMenu === 'security'">
            <h3 class="section-title">登录安全</h3>
            <div class="setting-list">
              <div class="setting-item">
                <div class="setting-info"><strong>修改密码</strong></div>
                <el-button size="small" text type="primary" @click="router.push('/profile')">修改</el-button>
              </div>
              <div class="setting-item">
                <div class="setting-info">
                  <strong>两步验证</strong>
                  <span class="desc">增强账号安全性</span>
                </div>
                <el-switch disabled />
              </div>
            </div>
          </div>

          <!-- 模型调用设置 -->
          <div v-if="activeMenu === 'model'" v-loading="modelLoading">
            <div class="model-heading">
              <div>
                <h3 class="section-title">模型调用设置</h3>
                <p class="model-intro">
                  配置只保存在服务端；API Key 使用加密密文存储，页面始终仅显示 ********。
                </p>
              </div>
              <el-button @click="loadModelSettings" :loading="modelLoading">刷新</el-button>
            </div>

            <el-alert
              title="密钥安全"
              description="保存后真实 API Key 不会再次下发到浏览器。密钥框保持 ******** 时表示沿用现有密钥；输入新值才会替换。"
              type="info"
              :closable="false"
              show-icon
              class="model-alert"
            />

            <div class="model-grid">
              <el-card
                v-for="form in modelForms"
                :key="form.modelType"
                class="model-card"
                shadow="never"
              >
                <template #header>
                  <div class="model-card-header">
                    <div>
                      <strong>{{ form.title }}</strong>
                      <span class="model-purpose">{{ form.purpose }}</span>
                    </div>
                    <el-switch
                      v-model="form.enabled"
                      active-text="启用"
                      inactive-text="停用"
                      inline-prompt
                    />
                  </div>
                </template>

                <el-form label-position="top">
                  <el-form-item label="模型提供商">
                    <el-input
                      v-model="form.provider"
                      maxlength="64"
                      placeholder="例如：天翼云、阿里云、华工"
                    />
                  </el-form-item>
                  <el-form-item label="调用 URL">
                    <el-input
                      v-model="form.baseUrl"
                      maxlength="1024"
                      placeholder="https://..."
                      autocomplete="off"
                    />
                  </el-form-item>
                  <el-form-item label="Model ID">
                    <el-input
                      v-model="form.modelId"
                      maxlength="256"
                      placeholder="请输入模型 ID"
                      autocomplete="off"
                    />
                  </el-form-item>
                  <el-form-item label="API Key">
                    <el-input
                      v-model="form.apiKey"
                      type="password"
                      maxlength="4096"
                      placeholder="未配置；请输入 API Key"
                      autocomplete="new-password"
                    />
                    <span class="field-help">
                      {{ form.hasApiKey ? '已安全配置；保持掩码可沿用原密钥' : '尚未配置密钥' }}
                    </span>
                  </el-form-item>

                  <template v-if="form.modelType === 'tts'">
                    <el-form-item label="音色 ID">
                      <el-input
                        v-model="form.voice"
                        maxlength="256"
                        placeholder="CosyVoice 3.5 的克隆或设计音色 ID"
                      />
                      <span class="field-help warning">
                        CosyVoice 3.5 不支持系统音色，合成前必须配置音色 ID。
                      </span>
                    </el-form-item>
                  </template>

                  <template v-if="form.modelType === 'vlm'">
                    <el-form-item label="组织 ID">
                      <el-input v-model="form.orgId" maxlength="128" />
                    </el-form-item>
                    <el-form-item label="运行环境">
                      <el-input v-model="form.environment" maxlength="64" />
                    </el-form-item>
                  </template>
                </el-form>

                <div class="model-actions">
                  <el-tag :type="form.hasApiKey ? 'success' : 'warning'" effect="plain">
                    {{ form.hasApiKey ? '密钥已配置' : '缺少密钥' }}
                  </el-tag>
                  <div>
                    <el-button
                      size="small"
                      @click="resetModelSetting(form)"
                      :loading="form.resetting"
                    >
                      恢复默认
                    </el-button>
                    <el-button
                      size="small"
                      type="primary"
                      @click="saveModelSetting(form)"
                      :loading="form.saving"
                    >
                      保存
                    </el-button>
                  </div>
                </div>
              </el-card>
            </div>
          </div>

          <!-- 关于 -->
          <div v-if="activeMenu === 'about'">
            <h3 class="section-title">关于广东建设智慧监理</h3>
            <img src="/app-logo.png" alt="广东建设智慧监理 Logo" class="about-logo" />
            <p>广东建设智慧监理 — AI 工程监理智能体</p>
            <p style="color: #999; margin-top: 8px;">版本：v1.0.0</p>
          </div>

          <!-- 底部按钮 -->
          <div class="footer-actions" v-if="activeMenu !== 'about' && activeMenu !== 'model'">
            <el-button type="primary" @click="saveAll">保存设置</el-button>
            <el-button @click="resetAll">恢复默认</el-button>
          </div>
        </el-main>
      </el-container>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import {
  authApi,
  modelSettingsApi,
  type ModelSetting,
  type ModelType,
} from '@/api'

const activeMenu = ref('notice')
const router = useRouter()
const STORAGE_KEY = 'gdjl-ui-settings'

const noticeSettings = reactive([
  { label: '待办提醒', desc: '待办任务状态变更时将收到通知', value: true },
  { label: '紧急公函', desc: '接收系统公函或重要通知', value: true },
  { label: '审批通知', desc: '需要您审批的流程将发通知', value: true },
  { label: '预警通知', desc: '系统预警信息将及时推送到', value: true },
])

const pushSettings = reactive([
  { label: '站内消息', desc: '在站内中心弹窗通知', value: true },
  { label: '邮件通知', desc: '重要邮件将以邮件形式通知到邮箱', value: false },
  { label: '短信通知', desc: '重要通知以短信形式通知到手机', value: false },
])

const msgNotify = ref(true)
const privacyLevel = ref('contact')
const onlineStatus = ref(true)
const whiteboardService = ref(true)
const tagRecord = ref(false)
const modelLoading = ref(false)

interface ModelForm {
  modelType: ModelType
  title: string
  purpose: string
  provider: string
  baseUrl: string
  modelId: string
  apiKey: string
  hasApiKey: boolean
  enabled: boolean
  voice: string
  orgId: string
  environment: string
  saving: boolean
  resetting: boolean
}

const modelForms = reactive<ModelForm[]>([
  {
    modelType: 'llm',
    title: '文本 LLM',
    purpose: '规范问答、文书生成、指令理解',
    provider: '',
    baseUrl: '',
    modelId: '',
    apiKey: '',
    hasApiKey: false,
    enabled: true,
    voice: '',
    orgId: '',
    environment: '',
    saving: false,
    resetting: false,
  },
  {
    modelType: 'tts',
    title: '语音合成（TTS）',
    purpose: '文字播报与语音反馈',
    provider: '',
    baseUrl: '',
    modelId: '',
    apiKey: '',
    hasApiKey: false,
    enabled: true,
    voice: '',
    orgId: '',
    environment: '',
    saving: false,
    resetting: false,
  },
  {
    modelType: 'stt',
    title: '语音识别（ASR / STT）',
    purpose: 'Web、眼镜与小程序录音转文字',
    provider: '',
    baseUrl: '',
    modelId: '',
    apiKey: '',
    hasApiKey: false,
    enabled: true,
    voice: '',
    orgId: '',
    environment: '',
    saving: false,
    resetting: false,
  },
  {
    modelType: 'vlm',
    title: '视觉模型（VLLM）',
    purpose: '施工现场隐患图片识别',
    provider: '',
    baseUrl: '',
    modelId: '',
    apiKey: '',
    hasApiKey: false,
    enabled: true,
    voice: '',
    orgId: '',
    environment: '',
    saving: false,
    resetting: false,
  },
])

function selectMenu(index: string) {
  activeMenu.value = index
}

function applyModelSetting(item: ModelSetting) {
  const form = modelForms.find(current => current.modelType === item.model_type)
  if (!form) return
  form.provider = item.provider
  form.baseUrl = item.base_url
  form.modelId = item.model_id
  form.apiKey = item.api_key_masked || ''
  form.hasApiKey = item.has_api_key
  form.enabled = item.enabled
  form.voice = String(item.options?.voice || '')
  form.orgId = String(item.options?.org_id || '')
  form.environment = String(item.options?.environment || '')
}

async function loadModelSettings() {
  modelLoading.value = true
  try {
    const response = await modelSettingsApi.list()
    response.items.forEach(applyModelSetting)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '模型配置加载失败')
  } finally {
    modelLoading.value = false
  }
}

function modelOptions(form: ModelForm) {
  if (form.modelType === 'tts') {
    return { voice: form.voice.trim() }
  }
  if (form.modelType === 'stt') {
    return { sample_rate: 16000 }
  }
  if (form.modelType === 'vlm') {
    return {
      org_id: form.orgId.trim(),
      environment: form.environment.trim() || 'test',
      input_name: 'image',
    }
  }
  return {}
}

async function saveModelSetting(form: ModelForm) {
  if (!form.provider.trim() || !form.baseUrl.trim() || !form.modelId.trim()) {
    ElMessage.warning('请完整填写模型提供商、调用 URL 和 Model ID')
    return
  }
  form.saving = true
  try {
    const isMasked = /^\*+$/.test(form.apiKey.trim())
    const result = await modelSettingsApi.update(form.modelType, {
      provider: form.provider.trim(),
      base_url: form.baseUrl.trim(),
      model_id: form.modelId.trim(),
      ...(form.apiKey.trim() && !isMasked ? { api_key: form.apiKey.trim() } : {}),
      enabled: form.enabled,
      options: modelOptions(form),
    })
    applyModelSetting(result)
    ElMessage.success(`${form.title}配置已保存`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || `${form.title}配置保存失败`)
  } finally {
    form.saving = false
  }
}

async function resetModelSetting(form: ModelForm) {
  try {
    await ElMessageBox.confirm(
      `确定将${form.title}恢复为服务端默认参数吗？`,
      '恢复默认配置',
      { type: 'warning', confirmButtonText: '恢复默认', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  form.resetting = true
  try {
    const result = await modelSettingsApi.reset(form.modelType)
    applyModelSetting(result)
    ElMessage.success(`${form.title}已恢复默认配置`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || `${form.title}恢复默认失败`)
  } finally {
    form.resetting = false
  }
}

function snapshot() {
  return {
    noticeSettings: noticeSettings.map(item => item.value),
    pushSettings: pushSettings.map(item => item.value),
    msgNotify: msgNotify.value,
    privacyLevel: privacyLevel.value,
    onlineStatus: onlineStatus.value,
    whiteboardService: whiteboardService.value,
    tagRecord: tagRecord.value,
  }
}

function saveAll() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot()))
  ElMessage.success('设置已保存到当前浏览器')
}
function resetAll() {
  noticeSettings.forEach(item => { item.value = true })
  pushSettings.forEach((item, index) => { item.value = index === 0 })
  msgNotify.value = true
  privacyLevel.value = 'contact'
  onlineStatus.value = true
  whiteboardService.value = true
  tagRecord.value = false
  localStorage.removeItem(STORAGE_KEY)
  ElMessage.info('已恢复默认设置')
}

function clearCache() {
  localStorage.removeItem(STORAGE_KEY)
  ElMessage.success('界面设置缓存已清理，登录状态已保留')
}

async function exportPersonalData() {
  try {
    const data = await authApi.me()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `广东建设智慧监理个人数据_${new Date().toISOString().slice(0, 10)}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('个人数据导出失败')
  }
}

onMounted(() => {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (saved) {
      noticeSettings.forEach((item, index) => {
        if (typeof saved.noticeSettings?.[index] === 'boolean') item.value = saved.noticeSettings[index]
      })
      pushSettings.forEach((item, index) => {
        if (typeof saved.pushSettings?.[index] === 'boolean') item.value = saved.pushSettings[index]
      })
      if (typeof saved.msgNotify === 'boolean') msgNotify.value = saved.msgNotify
      if (saved.privacyLevel) privacyLevel.value = saved.privacyLevel
      if (typeof saved.onlineStatus === 'boolean') onlineStatus.value = saved.onlineStatus
      if (typeof saved.whiteboardService === 'boolean') whiteboardService.value = saved.whiteboardService
      if (typeof saved.tagRecord === 'boolean') tagRecord.value = saved.tagRecord
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY)
  }
  void loadModelSettings()
})
</script>

<style scoped>
.page-desc { color: #999; font-size: 13px; margin: 4px 0 16px; }
/* 设置页左导航精修：图标 + 分组 + 与侧栏同款胶囊选中 */
.settings-menu { border-right: none; }
.settings-menu :deep(.el-menu-item) { height: 42px; border-radius: 6px; }
.settings-menu :deep(.el-menu-item.is-active) {
  background-color: rgba(37, 99, 235, 0.12) !important;
  color: #2563eb !important;
  font-weight: 600;
  margin: 2px 8px;
  border-left: none;
}
.settings-menu :deep(.el-icon) { margin-right: 8px; }
.settings-menu :deep(.nav-group) {
  font-size: 11px;
  letter-spacing: 0.5px;
  color: #868e98 !important;
  font-weight: 600;
  opacity: 1 !important;
  height: 30px;
  line-height: 30px;
  margin-top: 14px;
  padding-left: 18px;
  background: transparent !important;
  cursor: default;
  pointer-events: none;
}
.section-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; }
.about-logo {
  width: 120px;
  height: 120px;
  display: block;
  margin: 0 0 18px;
  border-radius: 20px;
  object-fit: cover;
  object-position: center top;
  border: 1px solid #e1e8ef;
}

.setting-list .setting-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 0; border-bottom: 1px solid #f5f5f5;
}
.setting-info strong { display: block; font-size: 14px; color: #333; }
.setting-info .desc { display: block; font-size: 12px; color: #999; margin-top: 4px; }

.footer-actions { margin-top: 32px; padding-top: 20px; border-top: 1px solid #f0f0f0; display: flex; gap: 8px; }
.model-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.model-intro { margin: -8px 0 0; color: #777; font-size: 13px; }
.model-alert { margin: 18px 0; }
.model-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.model-card-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.model-card-header strong { display: block; font-size: 15px; color: #222; }
.model-purpose { display: block; margin-top: 5px; font-size: 12px; color: #999; }
.field-help { display: block; margin-top: 5px; color: #999; font-size: 12px; line-height: 1.5; }
.field-help.warning { color: #b26a00; }
.model-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 4px; }

@media (max-width: 1100px) {
  .model-grid { grid-template-columns: 1fr; }
}
</style>
