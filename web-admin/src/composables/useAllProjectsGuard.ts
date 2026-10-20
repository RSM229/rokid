import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { projectApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

/**
 * 「全部项目」视角下的**写入归属选择**。
 *
 * 这个视角本身只解决「看得全」（读接口不带项目过滤），但新建/修改必须落到
 * 一个具体项目 —— 所以超管在全部项目视角下点新建时，先弹窗让他选目标项目，
 * 选完把 id 交给请求拦截器（`X-Project-Id`），后端 `require_project_id()`
 * 优先采用。**视角不变、数据落到用户明确选的项目**，不会静默写进本人项目。
 *
 * 用法（注意是 async）：
 * ```ts
 * if (!(await ensureWritableProject('新建验收记录'))) return
 * ```
 */

/** 弹窗可见性（由 App.vue 挂载的 <ProjectPickDialog /> 消费） */
export const pickerVisible = ref(false)
export const pickerAction = ref('新建')
export const pickerProjects = ref<{ id: number; name: string }[]>([])
export const pickerValue = ref<number | null>(null)

let pickResolver: ((projectId: number | null) => void) | null = null
/** 项目列表缓存：同一会话内不重复拉；切换视角后由调用方按需刷新 */
let projectCache: { id: number; name: string }[] = []

/** 弹窗内部调用：确认传项目 id，取消传 null。 */
export function closePicker(projectId: number | null) {
  const resolve = pickResolver
  pickResolver = null
  pickerVisible.value = false
  resolve?.(projectId)
}

/**
 * 需要写入时调用。单项目视角直接放行；全部项目视角弹窗选项目。
 *
 * @param action 动作名，用于提示文案（如「新建工作记录」「派单」）
 * @returns 允许继续时返回 true
 */
export async function ensureWritableProject(action = '新建'): Promise<boolean> {
  const auth = useAuthStore()
  if (!auth.allProjects) return true

  if (!projectCache.length) {
    try {
      projectCache = await projectApi.list()
    } catch {
      projectCache = []
    }
  }
  if (!projectCache.length) {
    ElMessage.error('没有可用项目，无法新建，请联系管理员')
    return false
  }

  pickerAction.value = action
  pickerProjects.value = projectCache
  // 默认沿用上次选的项目（省一次点击），没有则只有单个项目时自动选中
  const remembered = auth.writeProjectId
  pickerValue.value =
    remembered && projectCache.some((p) => p.id === remembered)
      ? remembered
      : projectCache.length === 1
        ? projectCache[0].id
        : null

  pickerVisible.value = true
  const picked = await new Promise<number | null>((resolve) => {
    pickResolver = resolve
  })
  if (!picked) {
    ElMessage.info('已取消')
    return false
  }
  auth.setWriteProjectId(picked)
  return true
}

/** 让缓存失效（切换账号 / 项目列表变化时调用）。 */
export function resetProjectCache() {
  projectCache = []
}
