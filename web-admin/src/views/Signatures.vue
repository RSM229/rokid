<template>
  <div>
    <div class="page-header">
      <div>
        <h2>电子签章管理</h2>
        <p>管理个人电子签章，用于文书导出 PDF 时加盖电子签名。</p>
      </div>
      <el-button type="primary" @click="openUpload">上传签章</el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="signatures" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="签章名称" min-width="160" />
        <el-table-column prop="role" label="角色" width="160">
          <template #default="{ row }">
            <el-tag size="small">{{ row.role || '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="签章图片" width="120">
          <template #default="{ row }">
            <el-image
              v-if="row.image_url"
              :src="row.image_url"
              :preview-src-list="[row.image_url]"
              fit="contain"
              style="width: 60px; height: 40px;"
              :preview-teleported="true"
            />
            <span v-else style="color: #999;">无</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active === false ? 'info' : 'success'" size="small">
              {{ row.is_active === false ? '停用' : '可用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="previewSignature(row)">预览</el-button>
            <el-button link type="danger" @click="confirmDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 上传签章对话框 -->
    <el-dialog v-model="uploadVisible" title="上传签章" width="520px">
      <el-form ref="uploadFormRef" :model="uploadForm" :rules="uploadRules" label-width="100px">
        <el-form-item label="签章名称" prop="name">
          <el-input v-model="uploadForm.name" placeholder="如：总监理工程师章" />
        </el-form-item>
        <el-form-item label="签章图片" prop="imageFile">
          <el-upload
            class="signature-uploader"
            action="#"
            :auto-upload="false"
            :show-file-list="false"
            accept="image/png"
            :on-change="handleImageChange"
          >
            <img v-if="imagePreview" :src="imagePreview" class="signature-preview" />
            <div v-else class="uploader-placeholder">
              <el-icon><Plus /></el-icon>
              <span>点击上传 PNG 签章图片</span>
            </div>
          </el-upload>
          <div class="upload-tip">仅支持 PNG 格式，建议透明背景，大小不超过 2MB</div>
        </el-form-item>
        <el-form-item label="保护密码" prop="password">
          <el-input
            v-model="uploadForm.password"
            type="password"
            show-password
            placeholder="签章保护密码，用于签署时校验"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input
            v-model="uploadForm.confirmPassword"
            type="password"
            show-password
            placeholder="再次输入保护密码"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">确认上传</el-button>
      </template>
    </el-dialog>

    <!-- 预览签章对话框 -->
    <el-dialog v-model="previewVisible" title="签章预览" width="480px">
      <div class="preview-container">
        <img v-if="previewItem?.image_url" :src="previewItem.image_url" class="preview-image" />
        <el-empty v-else description="暂无签章图片" />
        <div class="preview-info">
          <p><b>签章名称：</b>{{ previewItem?.name }}</p>
          <p><b>角色：</b>{{ previewItem?.role || '-' }}</p>
          <p><b>创建时间：</b>{{ previewItem?.created_at }}</p>
        </div>
      </div>
    </el-dialog>

    <!-- 删除确认对话框 -->
    <el-dialog v-model="deleteVisible" title="删除签章" width="420px">
      <el-form ref="deleteFormRef" :model="deleteForm" :rules="deleteRules" label-width="100px">
        <el-form-item label="保护密码" prop="password">
          <el-input
            v-model="deleteForm.password"
            type="password"
            show-password
            placeholder="请输入签章保护密码以确认删除"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deleteVisible = false">取消</el-button>
        <el-button type="danger" :loading="deleting" @click="submitDelete">确认删除</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { signatureApi } from '@/api'

const signatures = ref<any[]>([])
const loading = ref(false)

// 上传签章
const uploadVisible = ref(false)
const uploading = ref(false)
const uploadFormRef = ref<FormInstance | null>(null)
const imagePreview = ref('')
const imageFile = ref<File | null>(null)
const uploadForm = reactive({
  name: '',
  password: '',
  confirmPassword: '',
})
const uploadRules: FormRules = {
  name: [{ required: true, message: '请输入签章名称', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入保护密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value, cb) => {
        if (value !== uploadForm.password) cb(new Error('两次输入的密码不一致'))
        else cb()
      },
      trigger: 'blur',
    },
  ],
}

// 预览
const previewVisible = ref(false)
const previewItem = ref<any>(null)

// 删除
const deleteVisible = ref(false)
const deleting = ref(false)
const deleteFormRef = ref<FormInstance | null>(null)
const deleteForm = reactive({ password: '' })
const deleteTarget = ref<any>(null)
const deleteRules: FormRules = {
  password: [{ required: true, message: '请输入保护密码', trigger: 'blur' }],
}

async function loadData() {
  loading.value = true
  try {
    const res: any = await signatureApi.list()
    signatures.value = Array.isArray(res) ? res : res?.items || []
  } catch (e: any) {
    ElMessage.error('签章列表加载失败: ' + (e.response?.data?.detail || e.message || ''))
  } finally {
    loading.value = false
  }
}

function openUpload() {
  uploadForm.name = ''
  uploadForm.password = ''
  uploadForm.confirmPassword = ''
  imagePreview.value = ''
  imageFile.value = null
  uploadVisible.value = true
}

function handleImageChange(file: any) {
  const raw: File = file.raw
  if (!raw) return
  if (raw.type !== 'image/png') {
    ElMessage.error('仅支持 PNG 格式图片')
    return
  }
  if (raw.size > 2 * 1024 * 1024) {
    ElMessage.error('图片大小不能超过 2MB')
    return
  }
  imageFile.value = raw
  imagePreview.value = URL.createObjectURL(raw)
}

async function submitUpload() {
  if (!uploadFormRef.value) return
  await uploadFormRef.value.validate(async (valid) => {
    if (!valid) return
    if (!imageFile.value) {
      ElMessage.error('请选择签章图片')
      return
    }
    uploading.value = true
    try {
      const form = new FormData()
      form.append('name', uploadForm.name.trim())
      form.append('image', imageFile.value)
      form.append('password', uploadForm.password)
      await signatureApi.upload(form)
      ElMessage.success('签章上传成功')
      uploadVisible.value = false
      loadData()
    } catch (e: any) {
      ElMessage.error('签章上传失败: ' + (e.response?.data?.detail || e.message || ''))
    } finally {
      uploading.value = false
    }
  })
}

function previewSignature(row: any) {
  previewItem.value = row
  previewVisible.value = true
}

function confirmDelete(row: any) {
  deleteTarget.value = row
  deleteForm.password = ''
  deleteVisible.value = true
}

async function submitDelete() {
  if (!deleteFormRef.value || !deleteTarget.value) return
  await deleteFormRef.value.validate(async (valid) => {
    if (!valid) return
    deleting.value = true
    try {
      await signatureApi.delete(deleteTarget.value.id, deleteForm.password)
      ElMessage.success('签章已删除')
      deleteVisible.value = false
      loadData()
    } catch (e: any) {
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message || ''))
    } finally {
      deleting.value = false
    }
  })
}

onMounted(loadData)
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 { margin: 0 0 6px; }
.page-header p { margin: 0; color: #7a8580; }

.signature-uploader :deep(.el-upload) {
  border: 1px dashed #d9d9d9;
  border-radius: 6px;
  cursor: pointer;
  width: 220px;
  height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  transition: border-color 0.2s;
}
.signature-uploader :deep(.el-upload):hover { border-color: #409EFF; }
.signature-preview {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.uploader-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: #999;
  font-size: 12px;
}
.upload-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 6px;
}

.preview-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}
.preview-image {
  max-width: 360px;
  max-height: 220px;
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  padding: 8px;
}
.preview-info {
  width: 100%;
}
.preview-info p {
  margin: 6px 0;
  font-size: 14px;
  color: #333;
}
</style>
