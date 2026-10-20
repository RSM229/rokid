// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

/**
 * 统一 API 调用结果（密封类）
 *
 * 用于封装网络请求的成功/失败状态，配合协程使用。
 */
sealed class ApiResult<out T> {
    /** 请求成功，携带响应数据 */
    data class Success<T>(val data: T) : ApiResult<T>()

    /** 业务/HTTP 错误，code 为 HTTP 状态码或 -1，message 为错误信息 */
    data class Error(val code: Int, val message: String) : ApiResult<Nothing>()

    /** 网络异常（IO/超时/解析失败等） */
    data class Exception(val throwable: Throwable) : ApiResult<Nothing>()

    /** 是否成功 */
    fun isSuccess(): Boolean = this is Success

    /** 取出数据，失败返回 null */
    fun getOrNull(): T? = (this as? Success)?.data

    /** 失败时的统一错误文案 */
    fun errorMessage(): String = when (this) {
        is Success -> ""
        is Error -> message
        is Exception -> throwable.message ?: "未知异常"
    }
}

/**
 * 后端统一响应壳
 *
 * 后端 REST 接口统一返回 `{"code": 0, "msg": "...", "data": ...}`，
 * code=0 表示成功，非 0 表示业务错误。
 */
data class ApiResponse<T>(
    val code: Int = 0,
    val msg: String? = null,
    val data: T? = null,
) {
    fun isSuccess(): Boolean = code == 0
}
