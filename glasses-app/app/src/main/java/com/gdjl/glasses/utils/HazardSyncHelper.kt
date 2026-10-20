package com.gdjl.glasses.utils

import android.util.Log
import com.rokid.cxr.Caps
import com.rokid.cxr.link.CXRLink
import com.gdjl.glasses.GdjlApplication
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

object HazardSyncHelper {
    private const val TAG = "HazardSync"
    private const val TOPIC_HAZARD_RECORD = "gdjl.hazard"
    
    fun syncToPhone(photoFile: File, hazards: List<HazardModelHelper.HazardResult>) {
        try {
            val link = GdjlApplication.instance.requireReadyLink()
            
            val json = JSONObject().apply {
                put("type", "hazard_record")
                put("timestamp", System.currentTimeMillis())
                put("hazard_count", hazards.size)
                put("image_path", photoFile.absolutePath)
                
                val hazardsArray = JSONArray()
                hazards.forEach { hazard ->
                    hazardsArray.put(JSONObject().apply {
                        put("name", hazard.name)
                        put("confidence", hazard.confidence)
                        put("level", hazard.level)
                    })
                }
                put("hazards", hazardsArray)
            }
            
            val caps = Caps()
            caps.write("record")
            caps.write(json.toString())
            link.sendCustomCmd(TOPIC_HAZARD_RECORD, caps)
            
            Log.d(TAG, "✅ 隐患记录已发送到手机端: ${hazards.size} 处隐患")
            
        } catch (e: Exception) {
            Log.e(TAG, "❌ 发送到手机端失败", e)
        }
    }
}
