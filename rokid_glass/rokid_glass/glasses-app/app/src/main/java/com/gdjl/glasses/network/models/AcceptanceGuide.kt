// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 验收指引
 *
 * 对应后端 `POST /knowledge/acceptance-guide` 返回的验收流程指引。
 */
data class AcceptanceGuide(
    val keyword: String? = null,
    val steps: List<String> = emptyList(),
    val keyPoints: List<String> = emptyList(),
    val commonViolations: List<String> = emptyList(),
    @SerializedName(value = "regulation_basis", alternate = ["regulations"])
    val regulations: List<String> = emptyList(),
)

/**
 * 验收指引请求
 *
 * @param keyword 验收关键词（如"钢筋"、"模板"）
 */
data class AcceptanceGuideRequest(
    val keyword: String,
)
