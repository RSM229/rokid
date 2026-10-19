// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 隐患记录
 *
 * 对应后端 `POST /hazard/detect`、`/hazard/detect-and-save` 等接口。
 */
data class Hazard(
    val id: String,
    val section: String? = null,
    val riskStatus: String? = null,
    val riskCategory: String? = null,
    @SerializedName(value = "top1_risk", alternate = ["top_risk"])
    val topRisk: String? = null,
    val evidence: List<String>? = null,
    val status: String? = null,
    val createdAt: String? = null,
)

/**
 * 隐患识别请求
 *
 * @param imageId 已上传的影像 ID
 * @param section 施工部位
 * @param save 是否自动落库（true 走 detect-and-save）
 */
data class HazardDetectRequest(
    val imageId: String,
    val section: String? = null,
    val save: Boolean = false,
)

/**
 * 隐患识别响应
 */
data class HazardDetectResponse(
    val hazard: Hazard? = null,
    val riskStatus: String? = null,
    @SerializedName(value = "top1_risk", alternate = ["top_risk"])
    val topRisk: String? = null,
    val evidence: List<String>? = null,
)

/**
 * 复查请求
 *
 * @param pass true=复查合格闭环；false=驳回整改
 * @param note 复查备注
 */
data class HazardRecheckRequest(
    @SerializedName("passed")
    val pass: Boolean,
    @SerializedName("remark")
    val note: String? = null,
)

/**
 * 整改通知单生成结果
 */
data class RectificationDoc(
    @SerializedName(value = "document_id", alternate = ["doc_id"])
    val docId: String,
    val hazardId: String,
    val deadline: String? = null,
    val content: String? = null,
)
