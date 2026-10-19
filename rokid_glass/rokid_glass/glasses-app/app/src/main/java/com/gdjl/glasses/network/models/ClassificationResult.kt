package com.gdjl.glasses.network.models

/**
 * AI分类结果
 * 
 * @param workType 工作类型："巡视"|"验收"|"旁站"|"见证"|"质量"|"其他工作"|"待定"
 * @param confidence 置信度 0.0 - 1.0
 * @param reasons 分类依据列表
 * @param suggestedTitle 建议的标题（可选）
 */
data class ClassificationResult(
    val workType: String,
    val confidence: Double,
    val reasons: List<String> = emptyList(),
    val suggestedTitle: String? = null
)
