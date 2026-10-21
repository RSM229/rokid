import { config } from '../config'
import type { UserInfo } from '../types/index'
import { request } from './request'
import { session } from './session'

interface LoginResponse extends Partial<UserInfo> {
  bound: boolean
  bind_token?: string
  access_token?: string
  /** 当前微信绑定的是公司级账号，服务端发 bind_token 让用户改绑项目级账号 */
  rebind_from_org_account?: boolean
  /** 管理员把账号重置为一次性临时口令（需先强制改密） */
  must_change_password?: boolean
  message?: string
}

function wxLoginCode(): Promise<string> {
  if (config.mockWechatLogin) {
    return Promise.resolve(`${config.mockWechatCode}:${Date.now()}:${Math.random()}`)
  }
  return new Promise((resolve, reject) => {
    wx.login({
      success(result) {
        if (result.code) resolve(result.code)
        else reject(new Error('微信登录未返回 code'))
      },
      fail(error) {
        reject(new Error(error.errMsg || '微信登录失败'))
      },
    })
  })
}

function persistLogin(data: LoginResponse): void {
  if (!data.access_token || !data.user_id || !data.username || !data.real_name || !data.role) {
    throw new Error('登录响应不完整')
  }
  session.save(data.access_token, {
    user_id: data.user_id,
    username: data.username,
    real_name: data.real_name,
    role: data.role,
    project_id: data.project_id,
    project_name: data.project_name,
    permissions: data.permissions || [],
    must_change_password: Boolean(data.must_change_password),
  })
}

export async function loginWithWechat(): Promise<LoginResponse> {
  const code = await wxLoginCode()
  const data = await request<LoginResponse>('/miniprogram/auth/login', {
    method: 'POST',
    auth: false,
    data: { code },
  })
  if (data.bound) persistLogin(data)
  else if (data.bind_token) session.saveBindToken(data.bind_token)
  return data
}

export async function bindAccount(
  username: string,
  password: string,
  role: string,
  wxNickname?: string,
): Promise<void> {
  const data = await request<LoginResponse>('/miniprogram/auth/bind', {
    method: 'POST',
    auth: false,
    data: {
      bind_token: session.getBindToken(),
      username,
      password,
      role,
      // 微信平台拿不到真实微信号；绑定表单让用户确认自己的微信昵称，
      // 管理端「微信绑定管理」据此识别绑定者（可空，不影响绑定）。
      wx_nickname: (wxNickname || '').trim() || undefined,
    },
  })
  persistLogin(data)
}

export interface WechatBindingStatus {
  bound: boolean
  bound_at: string | null
  last_login_at: string | null
  wx_nickname?: string | null
  wx_avatar_url?: string | null
}

/** 当前登录账号的微信绑定情况（/miniprogram/auth/status） */
export async function getWechatBindingStatus(): Promise<WechatBindingStatus> {
  return await request<WechatBindingStatus>('/miniprogram/auth/status', { method: 'GET' })
}

/**
 * 更新本人微信资料（昵称/头像），供管理端「微信绑定管理」识别绑定者。
 * 头像走 wx.uploadFile（multipart）；昵称与头像至少提供其一。
 */
export async function updateWechatProfile(params: {
  nickname?: string
  avatarPath?: string
}): Promise<{ wx_nickname: string | null; wx_avatar_url: string | null }> {
  const token = session.getToken()
  if (!params.nickname && !params.avatarPath) {
    throw new Error('昵称与头像至少填写一项')
  }
  const authHeader: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}

  // 只改昵称（不选头像）时走 wx.request 表单编码 —— 后端 Form 参数对
  // urlencoded 同样解析。此前无头像时给 wx.uploadFile 传 'placeholder'
  // 假路径，微信客户端校验 filePath 直接 fail，用户看到「网络异常，更新失败」。
  if (!params.avatarPath) {
    return await new Promise((resolve, reject) => {
      wx.request({
        url: `${config.apiBaseUrl}/miniprogram/auth/wechat-profile`,
        method: 'POST',
        data: params.nickname ? { nickname: params.nickname } : {},
        header: { 'Content-Type': 'application/x-www-form-urlencoded', ...authHeader },
        success(res) {
          const body = (res.data || {}) as {
            detail?: string
            wx_nickname?: string | null
            wx_avatar_url?: string | null
          }
          if (res.statusCode >= 200 && res.statusCode < 300) {
            resolve({ wx_nickname: body.wx_nickname ?? null, wx_avatar_url: body.wx_avatar_url ?? null })
          } else {
            reject(new Error(body.detail || `更新失败（${res.statusCode}）`))
          }
        },
        fail: () => reject(new Error('网络异常，更新失败')),
      })
    })
  }

  return await new Promise((resolve, reject) => {
    // 早退分支已保证 avatarPath 存在；闭包内 TS 无法延续窄化，此处显式断言
    const avatarPath = params.avatarPath as string
    wx.uploadFile({
      url: `${config.apiBaseUrl}/miniprogram/auth/wechat-profile`,
      filePath: avatarPath,
      name: 'avatar',
      formData: params.nickname ? { nickname: params.nickname } : {},
      header: authHeader,
      success(res) {
        let body: { detail?: string; wx_nickname?: string | null; wx_avatar_url?: string | null } = {}
        try {
          body = JSON.parse(res.data) as typeof body
        } catch {
          body = {}
        }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve({ wx_nickname: body.wx_nickname ?? null, wx_avatar_url: body.wx_avatar_url ?? null })
        } else {
          reject(new Error(body.detail || `更新失败（${res.statusCode}）`))
        }
      },
      fail: () => reject(new Error('网络异常，更新失败')),
    })
  })
}

/**
 * 解除本人账号的微信绑定（需输入账号密码二次确认）。
 * 注意：后端只认「当前登录者本人」，绑错微信时可用它自己纠正；
 * 管理员要解绑别人需走 PC 端 `DELETE /auth/users/{id}/wechat-binding`。
 */
export async function unbindWechat(password: string): Promise<string> {
  const data = await request<{ detail?: string }>('/miniprogram/auth/unbind', {
    method: 'POST',
    data: { password },
  })
  return data?.detail || '已解除微信绑定'
}

/**
 * 设置/修改账号密码（用于在 Web 端用「账号 + 密码」登录）。
 *
 * 已绑定微信的账号免旧密码 —— 微信登录本身就证明了身份；
 * 未绑定微信则必须提供 currentPassword。
 */
export async function setPassword(
  newPassword: string,
  currentPassword?: string,
): Promise<string> {
  const data = await request<{ detail?: string }>('/miniprogram/auth/password', {
    method: 'POST',
    data: currentPassword
      ? { new_password: newPassword, current_password: currentPassword }
      : { new_password: newPassword },
  })
  return data?.detail || '密码设置成功'
}

/** 注册 / 微信自助注册 返回：与登录相同的态（含 access_token） */
interface RegisterResponse {
  access_token?: string
  user_id?: number
  username?: string
  real_name?: string
  role?: string
  project_id?: number
  project_name?: string
  permissions?: string[]
  bound?: boolean
  must_change_password?: boolean
  message?: string
}

/**
 * 路径 A：微信授权即注册（零短信成本）。
 * 用 /miniprogram/auth/login 拿到的 bind_token（含 openid）一键建裸用户并自动登录；
 * 后端自动生成用户名（wx_<openid>）、随机密码，不依赖手机号/短信。
 * 注册产出零权限裸用户，项目岗位由「加入项目」动作授予（见 joinProjectByInvite）。
 */
export async function wechatRegister(params?: {
  real_name?: string
  wx_nickname?: string
}): Promise<RegisterResponse> {
  const data = await request<RegisterResponse>('/miniprogram/auth/wechat-register', {
    method: 'POST',
    auth: false,
    data: {
      bind_token: session.getBindToken() || undefined,
      real_name: params?.real_name ? params.real_name.trim() : undefined,
      wx_nickname: params?.wx_nickname ? params.wx_nickname.trim() : undefined,
    },
  })
  if (data.access_token) {
    persistLogin(data as unknown as LoginResponse)
  }
  return data
}

export interface JoinProjectResult {
  detail: string
  project_id: number
  project_name?: string
  role: string
  role_label?: string
}

/**
 * 已登录用户用项目邀请码加入项目（后端 /auth/invite-codes/{code}/join）。
 * 与 Web 同口径：项目负责人发码（带 project_id + role），成员填写即获该岗位。
 * 成功后同步本地登录态中的 project_id / project_name / role。
 */
export async function joinProjectByInvite(code: string): Promise<JoinProjectResult> {
  const data = await request<JoinProjectResult>(
    `/auth/invite-codes/${encodeURIComponent(code.trim().toUpperCase())}/join`,
    { method: 'POST' },
  )
  const token = session.getToken()
  const user = session.getUser()
  if (token && user && data.project_id) {
    session.save(token, {
      ...user,
      project_id: data.project_id,
      project_name: data.project_name || user.project_name,
      role: data.role,
    })
  }
  return data
}

// ============ 「忘记密码」自助重置（公开接口）与密保问题 ============

export interface ForgotQuestionsResult {
  available: boolean
  questions: string[]
  message?: string
}

/** ① 按用户名取回密保题目（不含答案） */
export async function forgotQuestions(username: string): Promise<ForgotQuestionsResult> {
  return await request<ForgotQuestionsResult>('/auth/forgot/questions', {
    method: 'POST',
    auth: false,
    data: { username },
  })
}

export interface ForgotVerifyResult {
  reset_token: string
  expires_in: number
  username: string
}

/** ② 提交密保答案换取一次性重置凭证 */
export async function forgotVerify(
  username: string,
  answers: string[],
): Promise<ForgotVerifyResult> {
  return await request<ForgotVerifyResult>('/auth/forgot/verify', {
    method: 'POST',
    auth: false,
    data: { username, answers },
  })
}

/** ③ 凭重置凭证设置新密码 */
export async function forgotReset(resetToken: string, newPassword: string): Promise<string> {
  const data = await request<{ detail?: string }>('/auth/forgot/reset', {
    method: 'POST',
    auth: false,
    data: { reset_token: resetToken, new_password: newPassword },
  })
  return data?.detail || '密码已重置'
}

export interface SecurityQuestionsResult {
  is_set: boolean
  questions: string[]
  max_questions: number
}

/** 查看本账号已设置的密保题目（只回题目） */
export async function getSecurityQuestions(): Promise<SecurityQuestionsResult> {
  return await request<SecurityQuestionsResult>('/auth/me/security-questions', { method: 'GET' })
}

/** 设置 / 更新密保问题（需当前密码；传空数组表示清除） */
export async function setSecurityQuestions(
  currentPassword: string,
  questions: Array<{ question: string; answer: string }>,
): Promise<string> {
  const data = await request<{ detail?: string }>('/auth/me/security-questions', {
    method: 'PUT',
    data: { current_password: currentPassword, questions },
  })
  return data?.detail || '密保问题已保存'
}
