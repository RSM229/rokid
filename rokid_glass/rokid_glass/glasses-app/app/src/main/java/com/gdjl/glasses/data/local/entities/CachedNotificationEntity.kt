// 创建日期: 2026-07-18
package com.gdjl.glasses.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 通知缓存实体
 *
 * 弱网/离线时缓存后端 `GET /task/notifications` 返回的派单通知，供眼镜端离线浏览。
 *
 * @param id 自增主键
 * @param remoteId 后端通知 ID（用于去重）
 * @param taskId 关联派单 ID
 * @param title 通知标题
 * @param content 通知内容
 * @param type 通知类型
 * @param status 通知状态
 * @param createdAt 后端下发时间（毫秒）
 * @param read 是否已读
 */
@Entity(tableName = "cached_notification")
data class CachedNotificationEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    @ColumnInfo(name = "remote_id") val remoteId: String? = null,
    @ColumnInfo(name = "task_id") val taskId: String? = null,
    @ColumnInfo(name = "title") val title: String? = null,
    @ColumnInfo(name = "content") val content: String? = null,
    @ColumnInfo(name = "type") val type: String? = null,
    @ColumnInfo(name = "status") val status: String? = null,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "read") val read: Boolean = false,
)
