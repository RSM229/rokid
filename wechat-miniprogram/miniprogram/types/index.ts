export interface UserInfo {
  user_id: number
  username: string
  real_name: string
  role: string
  project_id?: number
  project_name?: string
  permissions: string[]
  /** 管理员把账号重置为一次性口令后为 true —— 需先强制改密 */
  must_change_password?: boolean
}

export interface ApiError {
  statusCode?: number
  message: string
  detail?: string
}
