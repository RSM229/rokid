package com.gdjl.phone.data

import android.util.Log
import java.util.*

object WorkRecordManager {
    private const val TAG = "WorkRecordManager"

    private val _records = mutableListOf<WorkRecord>()

    data class WorkRecord(
        val id: String,
        val title: String,
        val workType: String,
        val photoCount: Int,
        val audioCount: Int,
        val timestamp: Long,
        val description: String = ""
    )

    fun addRecord(
        title: String,
        workType: String,
        photoCount: Int = 0,
        audioCount: Int = 0,
        description: String = ""
    ) {
        val record = WorkRecord(
            id = UUID.randomUUID().toString(),
            title = title,
            workType = workType,
            photoCount = photoCount,
            audioCount = audioCount,
            timestamp = System.currentTimeMillis(),
            description = description
        )

        _records.add(0, record)
        Log.d(TAG, "✅ 添加工作记录: $title ($workType)")
    }

    fun addRecordFromSession(sessionData: Map<String, Any>) {
        val title = sessionData["title"] as? String ?: "工作记录"
        val workType = sessionData["work_type"] as? String ?: "待定"
        val photoCount = sessionData["photo_count"] as? Int ?: 0
        val audioCount = sessionData["audio_count"] as? Int ?: 0
        val description = sessionData["description"] as? String ?: ""

        addRecord(title, workType, photoCount, audioCount, description)
    }

    fun getAllRecords(): List<WorkRecord> = _records.toList()

    fun getRecordsByType(workType: String): List<WorkRecord> =
        _records.filter { it.workType == workType }

    fun getPendingRecords(): List<WorkRecord> =
        _records.filter { it.workType == "待定" }

    fun clearAll() {
        _records.clear()
        Log.d(TAG, "✅ 已清空工作记录")
    }
}
