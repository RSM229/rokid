// 创建日期: 2026-07-18
package com.gdjl.glasses.camera

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.gdjl.glasses.camera.CameraManager
import com.gdjl.glasses.data.sync.SyncManager
import com.gdjl.glasses.network.ApiClient
import com.gdjl.glasses.network.AuthManager
import com.gdjl.glasses.network.models.ImageRecord
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException
import kotlin.coroutines.resume

/**
 * 图片上传器
 *
 * 职责：
 * 1. multipart 上传图片至 `POST /media/images`
 * 2. 支持 [section]（施工部位）、[businessType]（业务类型）、[triggerDetection]（触发 AI 识别）
 * 3. 失败时入 [SyncManager] 离线队列，联网后由 WorkManager 自动补传
 *
 * 上传成功后返回 [ImageRecord]；调用方可进一步触发隐患识别/巡检上报等业务。
 */
class ImageUploader(private val context: Context) {

    companion object {
        private const val TAG = "ImageUploader"
        private const val PART_FILE = "file"
        private const val PART_SECTION = "section"
        private const val PART_BUSINESS_TYPE = "business_type"
        private const val PART_TRIGGER = "trigger_detection"
        private const val PART_SOURCE = "source"
        private const val PART_LOCAL_WM = "local_watermarked"
    }

    private val gson = ApiClient.gson
    private val mainHandler = Handler(Looper.getMainLooper())

    /**
     * 上传结果回调
     */
    interface Callback {
        fun onSuccess(record: ImageRecord)
        fun onError(message: String)
    }

    /**
     * 异步上传图片
     *
     * @param file 本地图片文件
     * @param section 施工部位（可空）
     * @param businessType 业务类型（如 hazard/inspection）
     * @param triggerDetection 是否触发 AI 隐患识别
     * @param callback 主线程回调
     */
    fun uploadImage(
        file: File,
        section: String? = null,
        businessType: String? = null,
        triggerDetection: Boolean = false,
        callback: Callback,
    ) {
        val request = buildMultipartRequest(file, section, businessType, triggerDetection)
        ApiClient.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: IOException) {
                Log.w(TAG, "上传失败入离线队列: ${file.absolutePath}", e)
                enqueueOffline(file, section, businessType, triggerDetection)
                mainHandler.post { callback.onError(e.message ?: "网络异常，已转入离线队列") }
            }

            override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                val bodyStr = response.body?.string() ?: ""
                if (response.code == 401) {
                    mainHandler.post { callback.onError("登录已过期") }
                    return
                }
                if (response.code !in 200..299) {
                    Log.w(TAG, "上传 HTTP ${response.code}: $bodyStr")
                    enqueueOffline(file, section, businessType, triggerDetection)
                    mainHandler.post { callback.onError("上传失败(${response.code})，已转入离线队列") }
                    return
                }
                try {
                    val root = JsonParser.parseString(bodyStr)
                    val payload = if (
                        root.isJsonObject &&
                        root.asJsonObject.has("code") &&
                        root.asJsonObject.has("data")
                    ) {
                        root.asJsonObject.get("data")
                    } else {
                        root
                    }
                    val data = gson.fromJson(payload, ImageRecord::class.java)
                    Log.i(TAG, "上传成功: ${file.absolutePath} -> ${data.id}")
                    mainHandler.post { callback.onSuccess(data) }
                } catch (e: Exception) {
                    Log.e(TAG, "解析上传响应失败: $bodyStr", e)
                    enqueueOffline(file, section, businessType, triggerDetection)
                    mainHandler.post { callback.onError("响应解析失败，已转入离线队列") }
                }
            }
        })
    }

    /**
     * 协程挂起版上传，供 [com.gdjl.glasses.data.sync.SyncWorker] 调用
     *
     * @return true=成功；false=失败（调用方负责重试计数）
     */
    suspend fun uploadImageSync(
        file: File,
        section: String? = null,
        businessType: String? = null,
        triggerDetection: Boolean = false,
    ): Boolean = withContext(Dispatchers.IO) {
        // 离线补传时不再入队，避免无限循环
        suspendCancellableCoroutine<Boolean> { cont ->
            val request = buildMultipartRequest(file, section, businessType, triggerDetection)
            ApiClient.newCall(request).enqueue(object : okhttp3.Callback {
                override fun onFailure(call: okhttp3.Call, e: IOException) {
                    if (cont.isActive) cont.resume(false)
                }

                override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                    val ok = response.code in 200..299
                    response.close()
                    if (cont.isActive) cont.resume(ok)
                }
            })
        }
    }

    // ---------------- 内部 ----------------

    private fun buildMultipartRequest(
        file: File,
        section: String?,
        businessType: String?,
        triggerDetection: Boolean,
    ): Request {
        val mediaType = "image/jpeg".toMediaType()
        val filePart = MultipartBody.Part.createFormData(
            PART_FILE, file.name, file.asRequestBody(mediaType)
        )

        val builder = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addPart(filePart)
            .addFormDataPart(PART_SOURCE, "glasses")
            .addFormDataPart(PART_TRIGGER, triggerDetection.toString())

        // 本地水印标记：通过文件名后缀 [CameraManager.WATERMARKED_SUFFIX] 判断
        // 若已叠加本地水印，上传时设置 local_watermarked=true，后端可跳过水印步骤
        val localWatermarked = file.nameWithoutExtension.endsWith(CameraManager.WATERMARKED_SUFFIX)
        builder.addFormDataPart(PART_LOCAL_WM, localWatermarked.toString())

        section?.takeIf { it.isNotBlank() }?.let { builder.addFormDataPart(PART_SECTION, it) }
        // business_type 与 business_id 必须成对提交。当前拍照发生在业务记录
        // 创建之前，因此只通过 source/trigger_detection 标记来源，避免 400。

        // 项目 ID 自动注入（若已登录）
        AuthManager.getProjectId()?.let {
            builder.addFormDataPart("project_id", it)
        }

        return Request.Builder()
            .url(ApiClient.fullUrl("/media/images"))
            .post(builder.build())
            .build()
    }

    /**
     * 入离线队列
     *
     * 调用 [SyncManager.enqueueUpload]，并立即尝试触发一次同步（联网时即上传）。
     */
    private fun enqueueOffline(
        file: File,
        section: String?,
        businessType: String?,
        triggerDetection: Boolean,
    ) {
        try {
            SyncManager.enqueueUpload(file, section, businessType, triggerDetection)
            SyncManager.triggerSync()
        } catch (e: Exception) {
            Log.w(TAG, "入离线队列失败（SyncManager 未初始化？）", e)
        }
    }
}
