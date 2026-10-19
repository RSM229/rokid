// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 监理文书
 *
 * 对应后端 `POST /document/generate-with-llm`、`generate-daily-log` 等接口。
 */
data class Document(
    val id: String,
    @SerializedName(value = "doc_type", alternate = ["type"])
    val docType: String,
    val title: String? = null,
    val content: Any? = null,
    val status: String? = null,
    val createdAt: String? = null,
    val createdBy: String? = null,
)

/**
 * LLM 文书生成请求
 *
 * @param docType 文书类型（巡视/验收/见证/旁站/监理日志/监理周报/监理月报/整改通知单）
 * @param title 标题
 * @param hazardInfo 关联隐患信息
 * @param inspectionInfo 关联巡检信息
 * @param extraContext 额外上下文（口述文本等）
 */
data class DocumentGenerateRequest(
    val docType: String,
    val title: String? = null,
    val hazardInfo: String? = null,
    val inspectionInfo: String? = null,
    val extraContext: String? = null,
)

/**
 * 监理日志生成请求
 */
data class DailyLogGenerateRequest(
    val date: String? = null,
    val section: String? = null,
    val extraContext: String? = null,
)

/**
 * PDF 导出结果
 */
data class PdfExportResult(
    @SerializedName(value = "download_url", alternate = ["url", "pdf_url"])
    val url: String,
    val filename: String? = null,
)
