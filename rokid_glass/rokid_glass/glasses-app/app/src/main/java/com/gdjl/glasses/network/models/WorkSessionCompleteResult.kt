package com.gdjl.glasses.network.models

/**
 * 全程语音会话直建结果（POST /work-session/complete）。
 */
class WorkSessionCompleteResult {
    var recordId: Long = 0
    var recordType: String? = null
    var workType: String? = null
    var confidence: Double = 0.0
    var reasons: List<String> = emptyList()
    var suggestedTitle: String? = null
    var fields: Map<String, String> = emptyMap()
    var photoCount: Int = 0
    var photoUrls: List<String> = emptyList()
}
