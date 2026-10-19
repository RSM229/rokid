// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

/**
 * 工作记录/文书/隐患/任务的通用状态操作请求
 *
 * @param action 操作类型：submit/approve/reject
 * @param note 备注（驳回原因等）
 */
data class StatusActionRequest(
    val action: String,
    val note: String? = null,
)
