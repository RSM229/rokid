// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 规范条款
 *
 * 对应后端 `POST /knowledge/regulations/search` 返回的检索结果项。
 */
data class Regulation(
    val id: String? = null,
    @SerializedName(value = "name", alternate = ["title"])
    val title: String? = null,
    val regulationNumber: String? = null,
    val clause: String? = null,
    val content: String? = null,
    val keyPoints: List<String>? = null,
)

/**
 * 规范检索请求
 *
 * @param keyword 检索关键词（如"钢筋绑扎"）
 * @param topK 返回条数
 */
data class RegulationSearchRequest(
    val keyword: String,
    val topK: Int = 5,
)

/**
 * 规范检索响应
 */
data class RegulationSearchResponse(
    val total: Int = 0,
    @SerializedName(value = "results", alternate = ["items"])
    val items: List<Regulation> = emptyList(),
)
