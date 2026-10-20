package com.gdjl.phone.data

import android.util.Log
import java.util.*

object HazardRecordManager {
    private const val TAG = "HazardRecordManager"
    
    private val _records = mutableListOf<HazardRecord>()
    private val listeners = mutableListOf<OnHazardRecordListener>()
    
    data class HazardRecord(
        val id: String,
        val hazardName: String,
        val riskLevel: String,
        val riskCategory: String,
        val section: String,
        val imageUrl: String?,
        val timestamp: Long,
        val status: String = "待处理"
    ) {
        fun getStatusColor(): String {
            return when (status) {
                "待处理" -> "#FF9800"
                "处理中" -> "#2196F3"
                "已闭环" -> "#4CAF50"
                else -> "#9E9E9E"
            }
        }
    }
    
    interface OnHazardRecordListener {
        fun onHazardRecordReceived(record: HazardRecord)
        fun onHazardRecordsUpdated(records: List<HazardRecord>)
    }
    
    fun addListener(listener: OnHazardRecordListener) {
        if (!listeners.contains(listener)) {
            listeners.add(listener)
            Log.d(TAG, "✅ 添加监听器")
        }
    }
    
    fun removeListener(listener: OnHazardRecordListener) {
        listeners.remove(listener)
        Log.d(TAG, "✅ 移除监听器")
    }
    
    fun addRecord(
        hazardName: String,
        riskLevel: String,
        riskCategory: String = "",
        section: String = "",
        imageUrl: String? = null
    ) {
        val record = HazardRecord(
            id = UUID.randomUUID().toString(),
            hazardName = hazardName,
            riskLevel = riskLevel,
            riskCategory = riskCategory,
            section = section,
            imageUrl = imageUrl,
            timestamp = System.currentTimeMillis()
        )
        
        _records.add(0, record)
        Log.d(TAG, "✅ 添加隐患记录: $hazardName ($riskLevel)")
        
        listeners.forEach { it.onHazardRecordReceived(record) }
    }
    
    fun getAllRecords(): List<HazardRecord> = _records.toList()
    
    fun getPendingRecords(): List<HazardRecord> = _records.filter { it.status == "待处理" }
    
    fun updateStatus(recordId: String, newStatus: String) {
        val index = _records.indexOfFirst { it.id == recordId }
        if (index != -1) {
            _records[index] = _records[index].copy(status = newStatus)
            listeners.forEach { it.onHazardRecordsUpdated(getAllRecords()) }
        }
    }
    
    fun clearAll() {
        _records.clear()
        listeners.forEach { it.onHazardRecordsUpdated(getAllRecords()) }
    }
}
