// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 派单通知
 *
 * 对应后端 `GET /task/notifications` 返回的待办列表项。
 */
data class TaskNotification(
    val id: String,
    val taskId: String? = null,
    val title: String? = null,
    @SerializedName(value = "description", alternate = ["content"])
    val content: String? = null,
    val type: String? = null,
    val status: String? = null,
    val createdAt: String? = null,
    val read: Boolean = false,
)

/**
 * 派单响应请求
 *
 * @param action respond=接单/拒接，complete=完成
 * @param accept true=接单，false=拒接（仅 respond 有效）
 * @param note 备注
 */
data class TaskRespondRequest(
    val action: String,
    val accept: Boolean? = null,
    val note: String? = null,
)
