// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 巡检会话
 *
 * 对应后端 `POST /inspection/start`、`/inspection/{id}/stop`、`/inspection/{id}/capture` 接口。
 */
data class InspectionSession(
    @SerializedName(value = "session_id", alternate = ["id"])
    val id: String,
    val projectId: String? = null,
    val section: String? = null,
    val status: String? = null,
    val startedAt: String? = null,
    val stoppedAt: String? = null,
    @SerializedName(value = "total_captures", alternate = ["capture_count"])
    val captureCount: Int = 0,
)

/**
 * 启动巡检请求
 *
 * @param section 巡检部位
 * @param intervalSec 自动采集间隔（秒），默认 5
 */
data class InspectionStartRequest(
    val section: String? = null,
    @SerializedName("frequency_seconds")
    val intervalSec: Long = 5,
)

/**
 * 巡检拍照请求
 *
 * @param imageId 已上传的影像 ID
 */
data class InspectionCaptureRequest(
    val imageId: String,
)
