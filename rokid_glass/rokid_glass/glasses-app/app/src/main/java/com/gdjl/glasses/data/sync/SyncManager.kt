// 创建日期: 2026-07-18
package com.gdjl.glasses.data.sync

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkRequest
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.gdjl.glasses.data.local.AppDatabase
import com.gdjl.glasses.data.local.entities.PendingUploadEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * 同步管理器
 *
 * 职责：
 * 1. [enqueueUpload] 入队待上传项（写 Room + 触发 WorkManager）
 * 2. [registerNetworkCallback] 监听网络恢复，立即触发一次同步
 * 3. [getPendingCount] 查询当前待同步数量
 *
 * 由 [com.gdjl.glasses.camera.ImageUploader] 在上传失败时调用。
 */
object SyncManager {

    private const val TAG = "SyncManager"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private lateinit var appContext: Context
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    /**
     * 初始化（在 Application/MainActivity.onCreate 中调用一次）
     */
    fun init(context: Context) {
        if (!::appContext.isInitialized) {
            appContext = context.applicationContext
        }
    }

    /**
     * 入队待上传项
     *
     * @param file 本地文件
     * @param section 施工部位
     * @param businessType 业务类型
     * @param triggerDetection 是否触发 AI 识别
     */
    fun enqueueUpload(
        file: java.io.File,
        section: String? = null,
        businessType: String? = null,
        triggerDetection: Boolean = false,
    ) {
        ensureInit()
        scope.launch {
            val entity = PendingUploadEntity(
                filePath = file.absolutePath,
                section = section,
                businessType = businessType,
                triggerDetection = triggerDetection,
            )
            val dao = AppDatabase.get(appContext).pendingUploadDao()
            dao.insert(entity)
            Log.i(TAG, "入队待上传：${file.absolutePath}")
            triggerSync()
        }
    }

    /**
     * 查询待同步数量
     */
    suspend fun getPendingCount(): Int {
        ensureInit()
        return AppDatabase.get(appContext).pendingUploadDao().countPending()
    }

    /**
     * 注册网络回调：网络恢复时触发同步
     *
     * 在 MainActivity.onCreate 中调用。
     */
    fun registerNetworkCallback() {
        ensureInit()
        val cm = appContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return
        if (networkCallback != null) return

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                Log.i(TAG, "网络恢复，触发同步")
                triggerSync()
            }
        }
        val request = NetworkRequest.Builder().build()
        cm.registerNetworkCallback(request, callback)
        networkCallback = callback
    }

    /**
     * 触发一次同步任务（仅在联网时执行）
     */
    fun triggerSync() {
        ensureInit()
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build()
        val request = OneTimeWorkRequestBuilder<SyncWorker>()
            .setConstraints(constraints)
            .build()
        WorkManager.getInstance(appContext)
            .enqueueUniqueWork(
                SyncWorker.UNIQUE_WORK_NAME,
                ExistingWorkPolicy.REPLACE,
                request
            )
        Log.i(TAG, "已触发同步任务")
    }

    private fun ensureInit() {
        check(::appContext.isInitialized) { "SyncManager 未初始化，请先调用 init(context)" }
    }
}
