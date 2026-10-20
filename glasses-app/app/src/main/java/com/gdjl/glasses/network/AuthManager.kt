// 创建日期: 2026-07-18
package com.gdjl.glasses.network

import android.content.Context
import android.util.Log
import com.gdjl.glasses.network.models.LoginRequest
import com.gdjl.glasses.network.models.LoginResponse
import com.gdjl.glasses.network.models.UserInfo
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken

/**
 * 鉴权管理器
 *
 * 职责：
 * 1. 使用 SharedPreferences 持久化 token、user_id、username、role、project_id
 * 2. 提供 [login]/[logout]/[isLoggedIn]/[getToken]/[getUserInfo] 等能力
 *
 * 由 [ApiClient] 拦截器读取 token 注入 Authorization 头。
 */
object AuthManager {

    private const val TAG = "AuthManager"
    private const val PREFS_NAME = "gdjl_auth"
    private const val KEY_TOKEN = "jwt_token"
    private const val KEY_USER_ID = "user_id"
    private const val KEY_USERNAME = "username"
    private const val KEY_REAL_NAME = "real_name"
    private const val KEY_ROLE = "role"
    private const val KEY_PROJECT_ID = "project_id"
    private const val KEY_PROJECT_NAME = "project_name"

    private val gson = Gson()

    private lateinit var prefs: android.content.SharedPreferences

    /**
     * 初始化（在 Application/MainActivity.onCreate 中调用一次）
     */
    fun init(context: Context) {
        if (!::prefs.isInitialized) {
            prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        }
    }

    private fun ensureInit() {
        check(::prefs.isInitialized) { "AuthManager 未初始化，请先调用 init(context)" }
    }

    /**
     * 登录
     *
     * 调用 `POST /auth/login`，成功后持久化 token 与用户信息。
     *
     * @return true=登录成功
     */
    fun login(username: String, password: String, role: String): Boolean {
        ensureInit()
        val result = ApiClient.postSync(
            "/auth/login",
            LoginRequest(username, password, role),
            object : TypeToken<LoginResponse>() {}
        )
        return when (result) {
            is com.gdjl.glasses.network.models.ApiResult.Success -> {
                saveSession(result.data)
                Log.i(TAG, "登录成功: $username / $role")
                true
            }
            is com.gdjl.glasses.network.models.ApiResult.Error -> {
                Log.w(TAG, "登录失败: ${result.code} ${result.message}")
                false
            }
            is com.gdjl.glasses.network.models.ApiResult.Exception -> {
                Log.e(TAG, "登录异常", result.throwable)
                false
            }
        }
    }

    /**
     * 登出：调用后端登出接口（可选），并清除本地凭证
     */
    fun logout() {
        ensureInit()
        try {
            ApiClient.postSync<Any>("/auth/logout", null, object : TypeToken<Any>() {})
        } catch (e: Exception) {
            Log.w(TAG, "后端登出失败（忽略）", e)
        }
        clearLocal()
    }

    /**
     * 清除本地凭证（401 时由 [ApiClient] 调用）
     */
    fun clearLocal() {
        if (!::prefs.isInitialized) return
        prefs.edit().clear().apply()
    }

    /**
     * 是否已登录（本地存在有效 token）
     *
     * 注意：仅校验本地存在性，真实有效性由后端校验，401 时会触发清空。
     */
    fun isLoggedIn(): Boolean {
        if (!::prefs.isInitialized) return false
        return getToken() != null
    }

    /** 取出 JWT，未登录返回 null */
    fun getToken(): String? {
        if (!::prefs.isInitialized) return null
        return prefs.getString(KEY_TOKEN, null)
    }

    /** 取出持久化的用户信息，未登录返回 null */
    fun getUserInfo(): UserInfo? {
        if (!::prefs.isInitialized) return null
        val id = prefs.getString(KEY_USER_ID, null) ?: return null
        return UserInfo(
            id = id,
            username = prefs.getString(KEY_USERNAME, null) ?: "",
            realName = prefs.getString(KEY_REAL_NAME, null),
            role = prefs.getString(KEY_ROLE, null) ?: "",
            projectId = prefs.getString(KEY_PROJECT_ID, null),
            projectName = prefs.getString(KEY_PROJECT_NAME, null),
        )
    }

    /** 当前用户角色，未登录返回 null */
    fun getRole(): String? = if (::prefs.isInitialized) prefs.getString(KEY_ROLE, null) else null

    /** 当前项目 ID，未登录或无项目返回 null */
    fun getProjectId(): String? = if (::prefs.isInitialized) prefs.getString(KEY_PROJECT_ID, null) else null

    /**
     * 持久化登录会话
     */
    private fun saveSession(resp: LoginResponse) {
        prefs.edit().apply {
            putString(KEY_TOKEN, resp.accessToken)
            putString(KEY_USER_ID, resp.user.id)
            putString(KEY_USERNAME, resp.user.username)
            putString(KEY_REAL_NAME, resp.user.realName)
            putString(KEY_ROLE, resp.user.role)
            putString(KEY_PROJECT_ID, resp.user.projectId)
            putString(KEY_PROJECT_NAME, resp.user.projectName)
            apply()
        }
    }
}
