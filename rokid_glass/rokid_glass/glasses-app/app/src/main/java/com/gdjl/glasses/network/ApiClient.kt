// 创建日期: 2026-07-18
package com.gdjl.glasses.network

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.Toast
import com.gdjl.glasses.BuildConfig
import com.gdjl.glasses.network.models.ApiResponse
import com.gdjl.glasses.network.models.ApiResult
import com.google.gson.FieldNamingPolicy
import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.JsonParser
import com.google.gson.reflect.TypeToken
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * HTTP 客户端单例封装
 *
 * 职责：
 * 1. 提供 OkHttp 单例（统一超时、日志、JWT 注入拦截器）
 * 2. 封装同步/异步 GET/POST/PUT 调用
 * 3. 统一响应处理：401 跳登录、5xx 提示、解析 [ApiResponse] 壳
 *
 * baseURL 从 BuildConfig.API_BASE_URL 读取（默认 http://10.0.2.2:8000/api）。
 */
object ApiClient {

    private const val TAG = "ApiClient"

    /**
     * 后端字段使用 snake_case；Android 数据类使用 camelCase。
     * 统一字段命名策略同时作用于请求序列化与响应反序列化。
     */
    val gson: Gson = GsonBuilder()
        .setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES)
        .create()
    private val mainHandler = Handler(Looper.getMainLooper())

    /** 应用级 Context（由 [init] 注入） */
    private var appContext: Context? = null

    /** 401 跳登录回调，由 MainActivity/LoginActivity 注册 */
    var onAuthFailed: (() -> Unit)? = null

    /** OkHttp 单例 */
    val client: OkHttpClient by lazy { buildClient() }

    /** API 根地址，形如 `http://10.0.2.2:8000/api` */
    val baseUrl: String get() = BuildConfig.API_BASE_URL.trimEnd('/')

    /** 注入应用 Context（在 Application/MainActivity.onCreate 中调用一次即可） */
    fun init(context: Context) {
        if (appContext == null) {
            appContext = context.applicationContext
        }
    }

    private fun buildClient(): OkHttpClient {
        val logging = HttpLoggingInterceptor { msg -> Log.d(TAG, msg) }
            .apply { level = if (BuildConfig.DEBUG) HttpLoggingInterceptor.Level.BODY else HttpLoggingInterceptor.Level.NONE }

        return OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .addInterceptor { chain ->
                val original = chain.request()
                val builder = original.newBuilder()
                // 自动注入 JWT（登录接口本身不注入）
                val token = AuthManager.getToken()
                if (token != null && original.header("Authorization") == null) {
                    builder.header("Authorization", "Bearer $token")
                }
                // 幂等键（避免弱网重试产生重复记录）
                if (original.header("Idempotency-Key") == null) {
                    builder.header("Idempotency-Key", java.util.UUID.randomUUID().toString())
                }
                chain.proceed(builder.build())
            }
            .addInterceptor(logging)
            .build()
    }

    // ---------------- 同步调用 ----------------

    /**
     * 同步 GET，返回 [ApiResult]
     */
    fun <T> getSync(path: String, typeToken: TypeToken<T>): ApiResult<T> =
        executeSync(buildGet(path), typeToken)

    /**
     * 同步 POST（JSON body），返回 [ApiResult]
     */
    fun <T> postSync(path: String, body: Any?, typeToken: TypeToken<T>): ApiResult<T> =
        executeSync(buildPost(path, body), typeToken)

    /**
     * 同步 PUT（JSON body），返回 [ApiResult]
     */
    fun <T> putSync(path: String, body: Any?, typeToken: TypeToken<T>): ApiResult<T> =
        executeSync(buildPut(path, body), typeToken)

    /** 同步 DELETE（可带 JSON body），返回 [ApiResult]。 */
    fun <T> deleteSync(path: String, body: Any?, typeToken: TypeToken<T>): ApiResult<T> =
        executeSync(buildDelete(path, body), typeToken)

    // ---------------- 异步调用 ----------------

    /**
     * 异步 GET，回调在主线程
     */
    fun <T> getAsync(path: String, typeToken: TypeToken<T>, callback: (ApiResult<T>) -> Unit) {
        client.newCall(buildGet(path)).enqueue(asCallback(typeToken, callback))
    }

    /**
     * 异步 POST（JSON body），回调在主线程
     */
    fun <T> postAsync(path: String, body: Any?, typeToken: TypeToken<T>, callback: (ApiResult<T>) -> Unit) {
        client.newCall(buildPost(path, body)).enqueue(asCallback(typeToken, callback))
    }

    /**
     * 异步 PUT（JSON body），回调在主线程
     */
    fun <T> putAsync(path: String, body: Any?, typeToken: TypeToken<T>, callback: (ApiResult<T>) -> Unit) {
        client.newCall(buildPut(path, body)).enqueue(asCallback(typeToken, callback))
    }

    /**
     * 裸 Call（用于 multipart 等自定义请求）
     */
    fun newCall(request: Request): Call = client.newCall(request)

    // ---------------- 请求构造 ----------------

    private fun buildGet(path: String): Request =
        Request.Builder().url(fullUrl(path)).get().build()

    private fun buildPost(path: String, body: Any?): Request {
        val rb: RequestBody = if (body == null) {
            "".toRequestBody("application/json".toMediaType())
        } else if (body is RequestBody) {
            body
        } else {
            gson.toJson(body).toRequestBody("application/json; charset=utf-8".toMediaType())
        }
        return Request.Builder().url(fullUrl(path)).post(rb).build()
    }

    private fun buildPut(path: String, body: Any?): Request {
        val rb: RequestBody = if (body == null) {
            "".toRequestBody("application/json".toMediaType())
        } else {
            gson.toJson(body).toRequestBody("application/json; charset=utf-8".toMediaType())
        }
        return Request.Builder().url(fullUrl(path)).put(rb).build()
    }

    private fun buildDelete(path: String, body: Any?): Request {
        val builder = Request.Builder().url(fullUrl(path))
        return if (body == null) {
            builder.delete().build()
        } else {
            val requestBody = gson.toJson(body)
                .toRequestBody("application/json; charset=utf-8".toMediaType())
            builder.delete(requestBody).build()
        }
    }

    /** 拼接完整 URL：baseUrl + path（path 形如 `/auth/login`） */
    fun fullUrl(path: String): String = baseUrl + if (path.startsWith("/")) path else "/$path"

    // ---------------- 响应处理 ----------------

    private fun <T> executeSync(request: Request, typeToken: TypeToken<T>): ApiResult<T> {
        return try {
            client.newCall(request).execute().use { resp -> parseResponse(resp, typeToken) }
        } catch (e: Exception) {
            Log.e(TAG, "请求异常: ${request.url}", e)
            ApiResult.Exception(e)
        }
    }

    private fun <T> asCallback(typeToken: TypeToken<T>, callback: (ApiResult<T>) -> Unit): Callback {
        return object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.e(TAG, "请求失败: ${call.request().url}", e)
                postResult(ApiResult.Exception(e), callback)
            }

            override fun onResponse(call: Call, response: Response) {
                postResult(parseResponse(response, typeToken), callback)
            }
        }
    }

    /**
     * 解析响应：统一处理 401/5xx/业务壳
     *
     * 后端响应形如 `{"code":0,"msg":"ok","data":...}`，
     * 当 HTTP 状态码非 2xx 时直接返回 Error；
     * 当业务 code 非 0 时返回 Error（code 为业务码）。
     */
    private fun <T> parseResponse(response: Response, typeToken: TypeToken<T>): ApiResult<T> {
        val bodyStr = runCatching { response.body?.string() }.getOrNull() ?: ""
        return try {
            when {
                response.code == 401 -> {
                    handleAuthFailed()
                    ApiResult.Error(401, "登录已过期，请重新登录")
                }
                response.code in 500..599 -> {
                    toast("服务器异常(${response.code})，请稍后重试")
                    ApiResult.Error(response.code, "服务器异常")
                }
                response.code !in 200..299 -> {
                    val msg = parseMsg(bodyStr) ?: "请求失败(${response.code})"
                    ApiResult.Error(response.code, msg)
                }
                bodyStr.isBlank() -> ApiResult.Error(response.code, "服务器返回空响应")
                else -> {
                    // 同时兼容统一业务壳与 FastAPI 原生 JSON 响应。
                    val root = JsonParser.parseString(bodyStr)
                    if (root.isJsonObject && root.asJsonObject.has("code") &&
                        root.asJsonObject.has("data")
                    ) {
                        val apiRespType = TypeToken.getParameterized(
                            ApiResponse::class.java,
                            typeToken.type
                        ).type
                        val apiResp: ApiResponse<T> =
                            gson.fromJson(bodyStr, apiRespType) ?: ApiResponse()
                        if (apiResp.isSuccess() && apiResp.data != null) {
                            ApiResult.Success(apiResp.data)
                        } else {
                            ApiResult.Error(apiResp.code, apiResp.msg ?: "业务错误")
                        }
                    } else {
                        val data: T = gson.fromJson(bodyStr, typeToken.type)
                        ApiResult.Success(data)
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "响应解析失败: $bodyStr", e)
            ApiResult.Exception(e)
        }
    }

    /** 从任意错误响应体里尝试取 msg/detail 字段 */
    private fun parseMsg(body: String): String? = try {
        if (body.isBlank()) {
            null
        } else {
            val obj = JsonParser.parseString(body).asJsonObject
            obj.get("msg")?.asString ?: obj.get("detail")?.let {
                if (it.isJsonPrimitive) it.asString else it.toString()
            }
        }
    } catch (_: Exception) {
        null
    }

    private fun handleAuthFailed() {
        AuthManager.clearLocal()
        mainHandler.post { onAuthFailed?.invoke() }
    }

    private fun toast(msg: String) {
        appContext?.let { ctx ->
            mainHandler.post { Toast.makeText(ctx, msg, Toast.LENGTH_SHORT).show() }
        }
    }

    private fun <T> postResult(result: ApiResult<T>, callback: (ApiResult<T>) -> Unit) {
        mainHandler.post { callback(result) }
    }
}

