// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 工作记录（巡视/验收/见证/旁站统一记录）
 *
 * 对应后端 `POST /work-record/inspection` 等系列接口。
 */
data class WorkRecord(
    val id: String,
    @SerializedName(value = "record_type", alternate = ["type"])
    val type: String,
    val section: String? = null,
    @SerializedName(value = "work_content", alternate = ["content"])
    val content: String? = null,
    val createdAt: String? = null,
    @SerializedName(value = "creator_id", alternate = ["created_by"])
    val createdBy: String? = null,
    val status: String? = null,
)

/**
 * 工作记录创建请求
 *
 * @param type 记录类型：inspection/acceptance/witness/station
 * @param section 施工部位（楼栋/楼层/部位）
 * @param content 记录正文
 * @param imageIds 关联影像 ID 列表
 */
data class WorkRecordCreateRequest(
    val type: String,
    val section: String? = null,
    val content: String? = null,
    val imageIds: List<String>? = null,
)

/** 工作台未查看/未处理统计。 */
data class WorkRecordStatItem(
    val unviewed: Int = 0,
    val pending: Int = 0,
)

data class HazardStatItem(
    val pending: Int = 0,
)

data class WorkRecordStats(
    @SerializedName("work_records")
    val workRecords: Map<String, WorkRecordStatItem> = emptyMap(),
    val hazards: HazardStatItem = HazardStatItem(),
)
