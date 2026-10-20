// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 影像记录
 *
 * 对应后端 `POST /media/images` 上传成功后的返回项。
 */
data class ImageRecord(
    val id: String,
    val url: String? = null,
    @SerializedName(value = "file_hash", alternate = ["sha256"])
    val sha256: String? = null,
    val section: String? = null,
    val businessType: String? = null,
    val hazardId: String? = null,
    val triggerDetection: Boolean = false,
    val detection: HazardDetectResponse? = null,
    val createdAt: String? = null,
)

/**
 * 语音指令智能匹配请求
 *
 * 未命中本地正则的语音文本，送后端 `POST /local/voice-command` 由 LLM 解析意图。
 */
data class VoiceCommandRequest(
    val text: String,
    val context: String? = null,
)

/**
 * 语音指令智能匹配响应
 *
 * @param intent 意图标签：capture_photo/start/end/query/guidance/switch_module
 * @param reply 回复语（可由 TTS 播报）
 * @param payload 附加数据
 */
data class VoiceCommandResponse(
    val intent: String? = null,
    val reply: String? = null,
    val payload: Map<String, String> = emptyMap(),
)
