package com.gdjl.glasses.network.models

/**
 * 派单任务
 */
data class DispatchTask(
    val id: String,
    val hazardId: String,
    val hazardName: String,
    val riskLevel: String,      // 重大/高/中/低
    val assignee: String,        // 负责人
    val assigneePhone: String,   // 负责人电话
    val deadline: String,        // 截止日期
    val status: DispatchStatus,  // 状态
    val description: String,     // 任务描述
    val createdAt: String,
    val updatedAt: String,
    val images: List<String>     // 相关图片
)

enum class DispatchStatus {
    PENDING,      // 待派单
    ASSIGNED,     // 已派单
    PROCESSING,   // 处理中
    COMPLETED,    // 已完成
    REJECTED,     // 已驳回
    CLOSED        // 已闭环
}

/**
 * 派单请求
 */
data class DispatchRequest(
    val hazardId: String,
    val assignee: String,
    val assigneePhone: String,
    val deadline: String,
    val description: String
)

/**
 * 派单响应
 */
data class DispatchResponse(
    val success: Boolean,
    val taskId: String?,
    val message: String
)
