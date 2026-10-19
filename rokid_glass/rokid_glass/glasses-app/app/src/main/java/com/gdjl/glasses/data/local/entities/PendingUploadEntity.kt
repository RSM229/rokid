// 创建日期: 2026-07-18
package com.gdjl.glasses.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 待上传实体
 *
 * 图片上传失败时入队，由 [com.gdjl.glasses.data.sync.SyncWorker] 联网后补传至 `POST /media/images`。
 *
 * @param id 自增主键
 * @param filePath 本地文件绝对路径
 * @param section 施工部位（口述解析后）
 * @param businessType 业务类型（如 hazard/inspection）
 * @param triggerDetection 是否触发 AI 隐患识别
 * @param createdAt 入队时间戳（毫秒）
 * @param retryCount 已重试次数，超过 5 次标记失败
 * @param status 状态：pending/failed
 */
@Entity(tableName = "pending_upload")
data class PendingUploadEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    @ColumnInfo(name = "file_path") val filePath: String,
    @ColumnInfo(name = "section") val section: String? = null,
    @ColumnInfo(name = "business_type") val businessType: String? = null,
    @ColumnInfo(name = "trigger_detection") val triggerDetection: Boolean = false,
    @ColumnInfo(name = "created_at") val createdAt: Long = System.currentTimeMillis(),
    @ColumnInfo(name = "retry_count") val retryCount: Int = 0,
    @ColumnInfo(name = "status") val status: String = STATUS_PENDING,
) {
    companion object {
        const val STATUS_PENDING = "pending"
        const val STATUS_FAILED = "failed"
        const val MAX_RETRY = 5
    }
}
