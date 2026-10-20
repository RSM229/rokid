// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

/**
 * 用户信息
 *
 * 持久化在 SharedPreferences 中，登录后供业务页面读取。
 */
data class UserInfo(
    val id: String,
    val username: String,
    val realName: String? = null,
    val role: String,
    val projectId: String? = null,
    val projectName: String? = null,
)

/**
 * 登录请求体
 *
 * @param username 监理通账号
 * @param password 明文密码（HTTPS 下传输；开发期 HTTP 仅限模拟器）
 * @param role 角色：admin/chief/deputy/supervisor/inspector
 */
data class LoginRequest(
    val username: String,
    val password: String,
    val role: String,
)

/**
 * 登录响应体
 *
 * 后端 `POST /auth/login` 返回 `access_token` 与用户信息。
 */
data class LoginResponse(
    val accessToken: String,
    val tokenType: String = "bearer",
    val userId: String,
    val username: String,
    val realName: String? = null,
    val role: String,
    val projectId: String? = null,
    val projectName: String? = null,
) {
    val user: UserInfo
        get() = UserInfo(
            id = userId,
            username = username,
            realName = realName,
            role = role,
            projectId = projectId,
            projectName = projectName,
        )
}
