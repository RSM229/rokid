// 创建日期: 2026-07-18
package com.gdjl.glasses.data.sync

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.gdjl.glasses.camera.ImageUploader
import com.gdjl.glasses.data.local.AppDatabase
import com.gdjl.glasses.data.local.entities.PendingUploadEntity
import java.io.File

/**
 * 离线同步 Worker
 *
 * 联网时由 [SyncManager] 或 WorkManager 触发，查询所有 pending 的待上传记录，
 * 逐个补传至 `POST /media/images`：
 * - 成功：删除队列项
 * - 失败：retry_count++，超过 [PendingUploadEntity.MAX_RETRY] 次标记 failed
 */
class SyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    companion object {
        private const val TAG = "SyncWorker"
        const val UNIQUE_WORK_NAME = "gdjl_sync_uploads"
    }

    private val dao by lazy { AppDatabase.get(applicationContext).pendingUploadDao() }
    private val uploader by lazy { ImageUploader(applicationContext) }

    override suspend fun doWork(): Result {
        val pending = dao.listPending()
        if (pending.isEmpty()) {
            Log.i(TAG, "无待同步任务")
            return Result.success()
        }
        Log.i(TAG, "开始同步 ${pending.size} 个待上传项")

        for (item in pending) {
            val ok = uploadOne(item)
            if (ok) {
                dao.deleteById(item.id)
                Log.i(TAG, "同步成功并删除：#${item.id} ${item.filePath}")
            } else {
                if (item.retryCount + 1 >= PendingUploadEntity.MAX_RETRY) {
                    dao.markFailed(item.id)
                    Log.w(TAG, "重试超限，标记失败：#${item.id}")
                } else {
                    dao.incrementRetry(item.id)
                    Log.w(TAG, "同步失败，retry_count=${item.retryCount + 1}：#${item.id}")
                }
            }
        }
        return Result.success()
    }

    private suspend fun uploadOne(item: PendingUploadEntity): Boolean {
        val file = File(item.filePath)
        if (!file.exists()) {
            Log.w(TAG, "文件不存在：${item.filePath}")
            return false
        }
        // 同步上传（挂起直到完成）
        return uploader.uploadImageSync(
            file = file,
            section = item.section,
            businessType = item.businessType,
            triggerDetection = item.triggerDetection,
        )
    }
}
