package com.gdjl.glasses.utils

import android.util.Log
import com.gdjl.glasses.network.models.DispatchTask
import com.rokid.cxr.Caps
import com.gdjl.glasses.GdjlApplication
import org.json.JSONObject

object DispatchSyncHelper {
    private const val TAG = "DispatchSync"
    private const val TOPIC_DISPATCH = "gdjl.dispatch"
    
    fun syncToPhone(task: DispatchTask) {
        try {
            val link = GdjlApplication.instance.requireReadyLink()
            
            val json = JSONObject().apply {
                put("type", "dispatch")
                put("id", task.id)
                put("hazard_name", task.hazardName)
                put("risk_level", task.riskLevel)
                put("assignee", task.assignee)
                put("status", task.status.name)
                put("deadline", task.deadline)
                put("description", task.description)
                put("timestamp", System.currentTimeMillis())
            }
            
            val caps = Caps()
            caps.write("record")
            caps.write(json.toString())
            link.sendCustomCmd(TOPIC_DISPATCH, caps)
            
            Log.d(TAG, "✅ 派单已发送到手机端: ${task.hazardName}")
            
        } catch (e: Exception) {
            Log.e(TAG, "❌ 发送派单失败", e)
        }
    }
}
