package com.gdjl.phone.service

import android.util.Log
import org.json.JSONObject

object DispatchReceiver {
    private const val TAG = "DispatchReceiver"
    
    data class DispatchTask(
        val id: String,
        val hazardName: String,
        val riskLevel: String,
        val assignee: String,
        val status: String,
        val deadline: String,
        val description: String
    )
    
    fun handleDispatch(message: String) {
        try {
            val json = JSONObject(message)
            val type = json.optString("type")
            
            if (type == "dispatch") {
                val task = DispatchTask(
                    id = json.optString("id"),
                    hazardName = json.optString("hazard_name"),
                    riskLevel = json.optString("risk_level"),
                    assignee = json.optString("assignee"),
                    status = json.optString("status"),
                    deadline = json.optString("deadline"),
                    description = json.optString("description")
                )
                Log.d(TAG, "📋 收到派单: ${task.hazardName} -> ${task.assignee}")
                // TODO: 保存到数据库或通知UI
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ 处理派单失败", e)
        }
    }
}
