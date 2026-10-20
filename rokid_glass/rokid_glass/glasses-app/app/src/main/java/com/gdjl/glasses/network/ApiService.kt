// 创建日期: 2026-07-18
package com.gdjl.glasses.network

import com.gdjl.glasses.network.models.AcceptanceGuide
import com.gdjl.glasses.network.models.AcceptanceGuideRequest
import com.gdjl.glasses.network.models.ApiResult
import com.gdjl.glasses.network.models.ClassificationResult
import com.gdjl.glasses.network.models.DocumentSignRequest
import com.gdjl.glasses.network.models.DailyLogGenerateRequest
import com.gdjl.glasses.network.models.Document
import com.gdjl.glasses.network.models.DocumentGenerateRequest
import com.gdjl.glasses.network.models.Hazard
import com.gdjl.glasses.network.models.HazardDetectRequest
import com.gdjl.glasses.network.models.HazardDetectResponse
import com.gdjl.glasses.network.models.HazardRecheckRequest
import com.gdjl.glasses.network.models.ImageRecord
import com.gdjl.glasses.network.models.InspectionCaptureRequest
import com.gdjl.glasses.network.models.InspectionSession
import com.gdjl.glasses.network.models.InspectionStartRequest
import com.gdjl.glasses.network.models.PdfExportResult
import com.gdjl.glasses.network.models.RectificationDoc
import com.gdjl.glasses.network.models.Regulation
import com.gdjl.glasses.network.models.RegulationSearchRequest
import com.gdjl.glasses.network.models.RegulationSearchResponse
import com.gdjl.glasses.network.models.Signature
import com.gdjl.glasses.network.models.SignatureDeleteRequest
import com.gdjl.glasses.network.models.SignatureUploadResponse
import com.gdjl.glasses.network.models.StatusActionRequest
import com.gdjl.glasses.network.models.TaskNotification
import com.gdjl.glasses.network.models.TaskRespondRequest
import com.gdjl.glasses.network.models.VoiceCommandRequest
import com.gdjl.glasses.network.models.VoiceCommandResponse
import com.gdjl.glasses.network.models.WorkRecord
import com.gdjl.glasses.network.models.WorkRecordCreateRequest
import com.gdjl.glasses.network.models.WorkRecordStats
import com.gdjl.glasses.network.models.WorkSessionCompleteResult
import com.google.gson.reflect.TypeToken
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import java.net.URLEncoder

private data class CreatedIdResponse(
    val recordId: String? = null,
    val docId: String? = null,
)

private data class WorkRecordListResponse(
    val records: List<WorkRecord> = emptyList(),
)

private data class InspectionHistoryResponse(
    val items: List<InspectionSession> = emptyList(),
)

private data class MyTaskListResponse(
    val tasks: List<TaskNotification> = emptyList(),
)

/**
 * 业务 API 服务
 *
 * 集中封装后端各业务模块接口，统一返回 [ApiResult]。
 * 所有方法均为同步方法，调用方需在协程的 IO 调度器中执行。
 *
 * 接口分组：
 * - 工作记录：POST /work-record/inspection、/acceptance、/witness、/station、/quality、/other
 * - 隐患：POST /hazard/detect、/hazard/detect-and-save、/hazard/{id}/rectify-image、
 *        /hazard/{id}/recheck、/hazard/{id}/create-rectification-doc
 * - 文书：POST /document/generate-with-llm、/document/generate-daily-log、GET /document/{id}/export-pdf
 * - 媒体：POST /media/images（multipart，见 [ImageUploader]）
 * - 巡检：POST /inspection/start、/inspection/{id}/stop、/inspection/{id}/capture
 * - 知识：POST /knowledge/regulations/search、/knowledge/acceptance-guide
 * - 派单：GET /task/notifications、POST /task/{id}/respond、/task/{id}/complete
 * - 本地 AI：POST /local/voice-command
 */
object ApiService {

    private fun <T, R> mapResult(
        result: ApiResult<T>,
        mapper: (T) -> R,
    ): ApiResult<R> = when (result) {
        is ApiResult.Success -> ApiResult.Success(mapper(result.data))
        is ApiResult.Error -> result
        is ApiResult.Exception -> result
    }

    // ---------------- 工作记录 ----------------

    /** 创建工作记录（巡视/验收/见证/旁站/质量/其他工作） */
    fun createWorkRecord(req: WorkRecordCreateRequest): ApiResult<WorkRecord> {
        val endpoint = when (req.type) {
            "acceptance" -> "/work-record/acceptance"
            "witness" -> "/work-record/witness"
            "station" -> "/work-record/station"
            "quality" -> "/work-record/quality"
            "other" -> "/work-record/other"
            else -> "/work-record/inspection"
        }
        val payload = when (req.type) {
            "acceptance" -> mapOf(
                "section" to req.section,
                "acceptance_content" to req.content,
            )
            "witness" -> mapOf(
                "section" to req.section,
                "remark" to req.content,
            )
            "station" -> mapOf(
                "section" to req.section,
                "station_content" to req.content,
            )
            "quality" -> mapOf(
                "section" to req.section,
                "quality_content" to req.content,
            )
            "other" -> mapOf(
                "section" to req.section,
                "other_content" to req.content,
            )
            else -> mapOf(
                "section" to req.section,
                "work_content" to req.content,
            )
        }
        val created = ApiClient.postSync(
            endpoint,
            payload,
            object : TypeToken<CreatedIdResponse>() {},
        )
        return when (created) {
            is ApiResult.Success -> {
                val id = created.data.recordId
                    ?: return ApiResult.Error(-1, "后端未返回工作记录 ID")
                val imageIds = req.imageIds.orEmpty()
                if (imageIds.isNotEmpty()) {
                    val linked = ApiClient.postSync(
                        "/media/link",
                        mapOf(
                            "media_ids" to imageIds.mapNotNull { it.toIntOrNull() },
                            "business_type" to "work_record",
                            "business_id" to id.toIntOrNull(),
                        ),
                        object : TypeToken<Map<String, Any?>>() {},
                    )
                    if (linked !is ApiResult.Success) {
                        return when (linked) {
                            is ApiResult.Error -> linked
                            is ApiResult.Exception -> linked
                            else -> ApiResult.Error(-1, "影像关联失败")
                        }
                    }
                }
                getWorkRecord(id)
            }
            is ApiResult.Error -> created
            is ApiResult.Exception -> created
        }
    }

    /** 查询工作记录列表 */
    fun listWorkRecords(type: String? = null): ApiResult<List<WorkRecord>> {
        val backendType = mapOf(
            "inspection" to "巡视",
            "acceptance" to "验收",
            "witness" to "见证",
            "station" to "旁站",
            "quality" to "质量",
            "other" to "其他工作",
        )[type] ?: type
        val path = if (backendType != null) {
            "/work-record/list?record_type=${URLEncoder.encode(backendType, "UTF-8")}"
        } else {
            "/work-record/list"
        }
        val result = ApiClient.getSync(
            path,
            object : TypeToken<WorkRecordListResponse>() {},
        )
        return mapResult(result) { it.records }
    }

    /** 工作台未查看/未处理概览。 */
    fun getWorkRecordStats(): ApiResult<WorkRecordStats> =
        ApiClient.getSync("/work-record/stats", object : TypeToken<WorkRecordStats>() {})

    /** 根据 ID 查询工作记录详情 */
    fun getWorkRecord(recordId: String): ApiResult<WorkRecord> =
        ApiClient.getSync("/work-record/$recordId", object : TypeToken<WorkRecord>() {})

    /**
     * 工作记录状态操作（提交审批/审批通过/驳回）
     *
     * @param action submit/approve/reject
     */
    fun actWorkRecord(recordId: String, req: StatusActionRequest): ApiResult<WorkRecord> {
        val action = req.action.lowercase()
        if (action !in setOf("submit", "approve", "reject", "archive")) {
            return ApiResult.Error(400, "不支持的工作记录操作：$action")
        }
        val body = if (action == "reject") mapOf("reason" to (req.note ?: "语音驳回")) else null
        val changed = ApiClient.postSync(
            "/work-record/$recordId/$action",
            body,
            object : TypeToken<Map<String, Any?>>() {},
        )
        return when (changed) {
            is ApiResult.Success -> getWorkRecord(recordId)
            is ApiResult.Error -> changed
            is ApiResult.Exception -> changed
        }
    }

    // ---------------- 隐患 ----------------

    /** 隐患识别（不落库） */
    fun detectHazard(req: HazardDetectRequest): ApiResult<HazardDetectResponse> =
        ApiClient.postSync(
            "/media/${req.imageId}/detect",
            req,
            object : TypeToken<HazardDetectResponse>() {},
        )

    /** 隐患识别并自动落库 */
    fun detectAndSaveHazard(req: HazardDetectRequest): ApiResult<Hazard> =
        ApiClient.postSync(
            "/media/${req.imageId}/detect-and-save",
            req,
            object : TypeToken<Hazard>() {},
        )

    /** 上传整改影像，绑定至指定隐患 */
    fun uploadRectifyImage(hazardId: String, imageId: String): ApiResult<Hazard> =
        ApiClient.postSync(
            "/hazard/$hazardId/rectify-media",
            mapOf("image_id" to imageId),
            object : TypeToken<Hazard>() {}
        )

    /** 隐患复查（pass=true 合格闭环，false 驳回整改） */
    fun recheckHazard(hazardId: String, req: HazardRecheckRequest): ApiResult<Hazard> {
        val changed = ApiClient.postSync(
            "/hazard/$hazardId/recheck",
            req,
            object : TypeToken<Map<String, Any?>>() {},
        )
        return when (changed) {
            is ApiResult.Success -> getHazard(hazardId)
            is ApiResult.Error -> changed
            is ApiResult.Exception -> changed
        }
    }

    /** 生成整改通知单（PDF） */
    fun createRectificationDoc(hazardId: String): ApiResult<RectificationDoc> =
        ApiClient.postSync(
            "/hazard/$hazardId/create-rectification-doc",
            null,
            object : TypeToken<RectificationDoc>() {}
        )

    /** 隐患列表（按状态筛选，status 可空表示全部） */
    fun listHazards(status: String? = null): ApiResult<List<Hazard>> {
        val backendStatus = mapOf(
            "reported" to "已提报",
            "notified" to "已通知整改",
            "rectifying" to "整改中",
            "recheck" to "待复查",
            "closed" to "已闭环",
            "rejected" to "驳回整改",
        )[status] ?: status
        val path = if (backendStatus != null) {
            "/hazard/list?status_filter=${URLEncoder.encode(backendStatus, "UTF-8")}"
        } else {
            "/hazard/list"
        }
        return ApiClient.getSync(path, object : TypeToken<List<Hazard>>() {})
    }

    // ---------------- 文书 ----------------

    /** LLM 智能生成文书 */
    fun generateDocumentWithLlm(req: DocumentGenerateRequest): ApiResult<Document> {
        val backendDocType = mapDocumentType(req.docType)
        val contextParts = listOfNotNull(
            req.extraContext?.takeIf { it.isNotBlank() },
            req.hazardInfo?.takeIf { it.isNotBlank() }?.let { "隐患信息：$it" },
            req.inspectionInfo?.takeIf { it.isNotBlank() }?.let { "巡检信息：$it" },
        )
        val payload = mapOf(
            "doc_type" to backendDocType,
            "title" to (req.title ?: backendDocType),
            "extra_context" to contextParts.joinToString("\n"),
        )
        val created = ApiClient.postSync(
            "/document/generate-with-llm",
            payload,
            object : TypeToken<CreatedIdResponse>() {},
        )
        return when (created) {
            is ApiResult.Success -> {
                val id = created.data.docId
                    ?: return ApiResult.Error(-1, "后端未返回文书 ID")
                getDocument(id)
            }
            is ApiResult.Error -> created
            is ApiResult.Exception -> created
        }
    }

    /** 生成监理日志 */
    fun generateDailyLog(req: DailyLogGenerateRequest): ApiResult<Document> {
        val date = req.date ?: java.time.LocalDate.now().toString()
        val created = ApiClient.postSync(
            "/document/generate-daily-log?date=${URLEncoder.encode(date, "UTF-8")}",
            null,
            object : TypeToken<CreatedIdResponse>() {},
        )
        return when (created) {
            is ApiResult.Success -> {
                val id = created.data.docId
                    ?: return ApiResult.Error(-1, "后端未返回文书 ID")
                getDocument(id)
            }
            is ApiResult.Error -> created
            is ApiResult.Exception -> created
        }
    }

    /** 导出文书 PDF */
    fun exportPdf(documentId: String): ApiResult<PdfExportResult> =
        ApiClient.getSync("/document/$documentId/export-pdf", object : TypeToken<PdfExportResult>() {})

    /** 文书列表（按状态筛选，status 可空表示全部） */
    fun listDocuments(status: String? = null): ApiResult<List<Document>> {
        val backendStatus = mapOf(
            "draft" to "草稿",
            "pending" to "待审批",
            "approved" to "已审批",
            "rejected" to "已驳回",
            "archived" to "已归档",
        )[status] ?: status
        val path = if (backendStatus != null) {
            "/document/list?status=${URLEncoder.encode(backendStatus, "UTF-8")}"
        } else {
            "/document/list"
        }
        return ApiClient.getSync(path, object : TypeToken<List<Document>>() {})
    }

    /**
     * 文书状态操作（提交审批/审批通过/驳回）
     *
     * @param action submit/approve/reject
     */
    fun actDocument(documentId: String, req: StatusActionRequest): ApiResult<Document> {
        val action = req.action.lowercase()
        if (action !in setOf("submit", "approve", "reject")) {
            return ApiResult.Error(400, "不支持的文书操作：$action")
        }
        val changed = ApiClient.postSync(
            "/document/$documentId/$action",
            if (action == "reject") mapOf("reason" to (req.note ?: "语音驳回")) else null,
            object : TypeToken<Map<String, Any?>>() {},
        )
        return when (changed) {
            is ApiResult.Success -> getDocument(documentId)
            is ApiResult.Error -> changed
            is ApiResult.Exception -> changed
        }
    }

    /** 文书电子签章 */
    fun signDocument(documentId: String, req: DocumentSignRequest): ApiResult<Document> =
        when (
            val result = ApiClient.postSync(
                "/documents/$documentId/sign",
                req,
                object : TypeToken<Map<String, Any?>>() {},
            )
        ) {
            is ApiResult.Success -> getDocument(documentId)
            is ApiResult.Error -> result
            is ApiResult.Exception -> result
        }

    /** 保存文书草稿（手动创建） */
    fun saveDocumentDraft(req: DocumentGenerateRequest): ApiResult<Document> {
        val backendDocType = mapDocumentType(req.docType)
        val payload = mapOf(
            "doc_type" to backendDocType,
            "title" to (req.title ?: backendDocType),
            "content" to mapOf("extra_context" to req.extraContext),
        )
        val created = ApiClient.postSync(
            "/document/create",
            payload,
            object : TypeToken<CreatedIdResponse>() {},
        )
        return when (created) {
            is ApiResult.Success -> {
                val id = created.data.docId
                    ?: return ApiResult.Error(-1, "后端未返回文书 ID")
                getDocument(id)
            }
            is ApiResult.Error -> created
            is ApiResult.Exception -> created
        }
    }

    // ---------------- 巡检 ----------------

    /** 启动巡检会话 */
    fun startInspection(req: InspectionStartRequest): ApiResult<InspectionSession> =
        ApiClient.postSync("/inspection/start", req, object : TypeToken<InspectionSession>() {})

    /** 停止巡检会话 */
    fun stopInspection(sessionId: String): ApiResult<InspectionSession> =
        ApiClient.postSync("/inspection/$sessionId/stop", null, object : TypeToken<InspectionSession>() {})

    /** 巡检会话中上报一张采集影像 */
    fun captureInspection(sessionId: String, req: InspectionCaptureRequest): ApiResult<InspectionSession> =
        ApiClient.postSync("/inspection/$sessionId/capture-media", req, object : TypeToken<InspectionSession>() {})

    /** 巡检历史会话列表 */
    fun listInspectionSessions(): ApiResult<List<InspectionSession>> {
        val result = ApiClient.getSync(
            "/inspection/history",
            object : TypeToken<InspectionHistoryResponse>() {},
        )
        return mapResult(result) { it.items }
    }

    // ---------------- 知识 ----------------

    /** 规范条款检索 */
    fun searchRegulations(req: RegulationSearchRequest): ApiResult<RegulationSearchResponse> =
        ApiClient.postSync("/knowledge/regulations/search", req, object : TypeToken<RegulationSearchResponse>() {})

    /** 验收流程指引 */
    fun acceptanceGuide(req: AcceptanceGuideRequest): ApiResult<AcceptanceGuide> =
        ApiClient.postSync("/knowledge/acceptance-guide", req, object : TypeToken<AcceptanceGuide>() {})

    // ---------------- 派单 ----------------

    /** 拉取当前用户待办通知 */
    fun listTaskNotifications(): ApiResult<List<TaskNotification>> {
        val result = ApiClient.getSync(
            "/task/my-tasks",
            object : TypeToken<MyTaskListResponse>() {},
        )
        return mapResult(result) { it.tasks }
    }

    /** 接单/拒接/完成派单 */
    fun respondTask(taskId: String, req: TaskRespondRequest): ApiResult<TaskNotification> {
        val action = if (req.accept == false) "reject" else "accept"
        val changed = ApiClient.postSync(
            "/task/$taskId/respond",
            mapOf("action" to action, "reject_reason" to req.note),
            object : TypeToken<Map<String, Any?>>() {},
        )
        return when (changed) {
            is ApiResult.Success -> getTask(taskId)
            is ApiResult.Error -> changed
            is ApiResult.Exception -> changed
        }
    }

    fun getTask(taskId: String): ApiResult<TaskNotification> =
        ApiClient.getSync("/task/$taskId", object : TypeToken<TaskNotification>() {})

    fun startTask(taskId: String): ApiResult<TaskNotification> {
        val changed = ApiClient.postSync(
            "/task/$taskId/start",
            null,
            object : TypeToken<Map<String, Any?>>() {},
        )
        return when (changed) {
            is ApiResult.Success -> getTask(taskId)
            is ApiResult.Error -> changed
            is ApiResult.Exception -> changed
        }
    }

    /** 完成派单 */
    fun completeTask(taskId: String, note: String? = null): ApiResult<TaskNotification> {
        val changed = ApiClient.postSync(
            "/task/$taskId/complete",
            mapOf("completion_remark" to note),
            object : TypeToken<Map<String, Any?>>() {}
        )
        return when (changed) {
            is ApiResult.Success -> getTask(taskId)
            is ApiResult.Error -> changed
            is ApiResult.Exception -> changed
        }
    }

    // ---------------- 本地 AI ----------------

    /**
     * 语音指令智能匹配
     *
     * 未命中本地正则的语音文本，送后端 LLM 解析意图。
     */
    fun matchVoiceCommand(req: VoiceCommandRequest): ApiResult<VoiceCommandResponse> =
        ApiClient.postSync("/local/voice-command", req, object : TypeToken<VoiceCommandResponse>() {})

    // ---------------- 工作会话分类（AI自动分类） ----------------

    /**
     * AI自动分类工作记录类型
     *
     * 上传一张照片+语音文本，后端返回分类结果
     * 支持类型：巡视、验收、旁站、见证、质量、其他工作、待定
     *
     * @param image 照片文件
     * @param voiceText 语音转文字内容（可选）
     * @param callback 回调
     */
    fun classifyWorkSession(
        image: java.io.File,
        voiceText: String? = null,
        callback: (ApiResult<ClassificationResult>) -> Unit
    ) {
        val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
        val mediaType = "image/jpeg".toMediaType()
        val filePart = okhttp3.MultipartBody.Part.createFormData(
            "image", image.name, image.asRequestBody(mediaType)
        )

        val builder = okhttp3.MultipartBody.Builder()
            .setType(okhttp3.MultipartBody.FORM)
            .addPart(filePart)

        if (!voiceText.isNullOrBlank()) {
            builder.addFormDataPart("voice_text", voiceText)
        }

        val body = builder.build()
        val request = okhttp3.Request.Builder()
            .url(ApiClient.fullUrl("/work-session/classify"))
            .post(body)
            .build()

        ApiClient.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: java.io.IOException) {
                mainHandler.post { callback(ApiResult.Exception(e)) }
            }

            override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                val bodyStr = response.body?.string() ?: ""
                try {
                    val root = com.google.gson.JsonParser.parseString(bodyStr)
                    val payload = if (
                        root.isJsonObject &&
                        root.asJsonObject.has("code") &&
                        root.asJsonObject.has("data")
                    ) {
                        root.asJsonObject.get("data")
                    } else {
                        root
                    }
                    val result: ApiResult<ClassificationResult> =
                        if (response.code in 200..299) {
                            ApiResult.Success(
                                ApiClient.gson.fromJson(
                                    payload,
                                    ClassificationResult::class.java
                                )
                            )
                        } else {
                            ApiResult.Error(response.code, "分类失败")
                        }
                    mainHandler.post { callback(result) }
                } catch (e: Exception) {
                    mainHandler.post { callback(ApiResult.Exception(e)) }
                }
            }
        })
    }

    // ---------------- 工作会话直建（全程语音自动填表） ----------------

    /**
     * 全程语音会话直建：上传本会话全部照片与合并语音文本，
     * 后端 LLM 自动分类工作类型并抽取表单字段、创建完整工作记录。
     *
     * @param photos 会话照片文件列表（可空）
     * @param voiceText 合并后的语音转写全文（必填）
     * @param callback 回调（主线程）
     */
    fun completeWorkSession(
        photos: List<java.io.File>,
        voiceText: String,
        callback: (ApiResult<WorkSessionCompleteResult>) -> Unit
    ) {
        val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
        val builder = okhttp3.MultipartBody.Builder()
            .setType(okhttp3.MultipartBody.FORM)
            .addFormDataPart("voice_text", voiceText)

        photos.forEach { file ->
            val part = okhttp3.MultipartBody.Part.createFormData(
                "photos",
                file.name,
                file.asRequestBody("image/jpeg".toMediaType())
            )
            builder.addPart(part)
        }

        val body = builder.build()
        val request = okhttp3.Request.Builder()
            .url(ApiClient.fullUrl("/work-session/complete"))
            .post(body)
            .build()

        ApiClient.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: java.io.IOException) {
                mainHandler.post { callback(ApiResult.Exception(e)) }
            }

            override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                val bodyStr = response.body?.string() ?: ""
                try {
                    val root = com.google.gson.JsonParser.parseString(bodyStr)
                    val payload = if (
                        root.isJsonObject &&
                        root.asJsonObject.has("code") &&
                        root.asJsonObject.has("data")
                    ) {
                        root.asJsonObject.get("data")
                    } else {
                        root
                    }
                    val result: ApiResult<WorkSessionCompleteResult> =
                        if (response.code in 200..299) {
                            ApiResult.Success(
                                ApiClient.gson.fromJson(
                                    payload,
                                    WorkSessionCompleteResult::class.java
                                )
                            )
                        } else {
                            ApiResult.Error(response.code, "会话保存失败")
                        }
                    mainHandler.post { callback(result) }
                } catch (e: Exception) {
                    mainHandler.post { callback(ApiResult.Exception(e)) }
                }
            }
        })
    }

    // ---------------- 影像（元信息查询，multipart 见 ImageUploader） ----------------

    /** 根据 ID 查询影像记录 */
    fun getImage(imageId: String): ApiResult<ImageRecord> =
        ApiClient.getSync("/media/$imageId", object : TypeToken<ImageRecord>() {})

    /** 根据 ID 查询隐患详情 */
    fun getHazard(hazardId: String): ApiResult<Hazard> =
        ApiClient.getSync("/hazard/$hazardId", object : TypeToken<Hazard>() {})

    /** 根据 ID 查询文书详情 */
    fun getDocument(documentId: String): ApiResult<Document> =
        ApiClient.getSync("/document/$documentId", object : TypeToken<Document>() {})

    private fun mapDocumentType(type: String): String = mapOf(
        "daily_log" to "监理日志",
        "quality_notice" to "监理工程师通知单（质量类）",
        "rectification" to "安全隐患整改通知单",
        "inspection" to "巡视记录",
        "acceptance" to "验收记录",
        "witness" to "见证取样记录",
        "weekly_report" to "监理周报",
        "monthly_report" to "监理月报",
        "station" to "旁站记录",
    )[type] ?: type

    // ---------------- 签章 ----------------

    /** 我的签章列表 */
    fun listSignatures(): ApiResult<List<Signature>> =
        ApiClient.getSync("/signatures/me", object : TypeToken<List<Signature>>() {})

    /** 删除签章（需密码确认） */
    fun deleteSignature(
        signatureId: String,
        req: SignatureDeleteRequest,
    ): ApiResult<Map<String, Any?>> =
        ApiClient.deleteSync(
            "/signatures/$signatureId",
            req,
            object : TypeToken<Map<String, Any?>>() {},
        )

    /**
     * 上传签章（multipart，由调用方构造 RequestBody）
     *
     * 返回上传后的签章 ID 与 URL。
     */
    fun uploadSignature(
        file: java.io.File,
        password: String,
        name: String = "个人签章",
        callback: (ApiResult<SignatureUploadResponse>) -> Unit,
    ) {
        val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
        val mediaType = "image/png".toMediaType()
        val filePart = okhttp3.MultipartBody.Part.createFormData(
            "file", file.name, file.asRequestBody(mediaType)
        )
        val body = okhttp3.MultipartBody.Builder()
            .setType(okhttp3.MultipartBody.FORM)
            .addPart(filePart)
            .addFormDataPart("name", name)
            .addFormDataPart("password", password)
            .build()
        val request = okhttp3.Request.Builder()
            .url(ApiClient.fullUrl("/signatures/upload"))
            .post(body)
            .build()
        ApiClient.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: java.io.IOException) {
                mainHandler.post { callback(ApiResult.Exception(e)) }
            }

            override fun onResponse(call: okhttp3.Call, response: okhttp3.Response) {
                val bodyStr = response.body?.string() ?: ""
                try {
                    val root = com.google.gson.JsonParser.parseString(bodyStr)
                    val payload = if (
                        root.isJsonObject &&
                        root.asJsonObject.has("code") &&
                        root.asJsonObject.has("data")
                    ) {
                        root.asJsonObject.get("data")
                    } else {
                        root
                    }
                    val result: ApiResult<SignatureUploadResponse> =
                        if (response.code in 200..299) {
                            ApiResult.Success(
                                ApiClient.gson.fromJson(
                                    payload,
                                    SignatureUploadResponse::class.java,
                                )
                            )
                        } else {
                            ApiResult.Error(response.code, "签章上传失败")
                        }
                    mainHandler.post { callback(result) }
                } catch (e: Exception) {
                    mainHandler.post { callback(ApiResult.Exception(e)) }
                }
            }
        })
    }
}
