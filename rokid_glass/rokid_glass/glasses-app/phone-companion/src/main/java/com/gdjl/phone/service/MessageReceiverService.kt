package com.gdjl.phone.service

import android.util.Log
import com.gdjl.phone.app.CXRLApplication
import com.gdjl.phone.data.HazardRecordManager
import com.rokid.cxr.link.callbacks.ICustomCmdCbk
import com.rokid.security.phone.sdk.api.PSecuritySDK
import com.rokid.security.phone.sdk.api.msg.api.IMessage
import com.rokid.security.phone.sdk.api.msg.listener.IMessageListener
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets

data class PendingSession(
    val id: String,
    val imagesCount: Int,
    val voiceSummary: String,
    val createdAt: String,
    val workType: String = "待定"
)

class MessageReceiverService {
    
    companion object {
        private const val TAG = "MessageReceiver"
        private const val TOPIC_WORK_RECORD = "gdjl.workrecord"
        
        private val _pendingSessions = mutableListOf<PendingSession>()
        private val listeners = mutableListOf<OnPendingSessionReceivedListener>()
        
        fun addListener(listener: OnPendingSessionReceivedListener) {
            if (!listeners.contains(listener)) {
                listeners.add(listener)
                Log.d(TAG, "✅ 添加监听器")
            }
        }
        
        fun removeListener(listener: OnPendingSessionReceivedListener) {
            listeners.remove(listener)
            Log.d(TAG, "✅ 移除监听器")
        }
        
        fun getPendingSessions(): List<PendingSession> = _pendingSessions.toList()
    }
    
    // 标准 IMessageListener
    private val messageListener = object : IMessageListener {
        override fun onP2PTextMessage(msg: String, clientId: String) {
            Log.d(TAG, "📨 P2P消息: clientId=$clientId")
            if (clientId == TOPIC_WORK_RECORD) {
                handleWorkRecordMessage(msg)
            }
        }
        
        override fun onClassicBTTextMessage(msg: String, clientId: String) {
            Log.d(TAG, "📨 蓝牙消息: clientId=$clientId")
            if (clientId == TOPIC_WORK_RECORD) {
                handleWorkRecordMessage(msg)
            }
        }
        
        override fun onVideoH264Stream(buffer: ByteBuffer) {
            Log.v(TAG, "📹 收到H264视频流: ${buffer.remaining()} bytes")
        }
        
        override fun onAudioStream(buffer: ByteBuffer) {
            Log.v(TAG, "🎵 收到音频流: ${buffer.remaining()} bytes")
        }
        
        override fun onNv21Data(data: ByteArray, width: Int, height: Int) {
            Log.v(TAG, "📹 收到NV21帧: ${width}x${height}")
        }
        
        override fun onClassicBTAudioStream(buffer: ByteArray) {
            Log.v(TAG, "🎵 收到蓝牙音频: ${buffer.size} bytes")
        }
        
        override fun onBTStreamDataReceived(tag: String, data: ByteArray, clientId: String) {
            Log.v(TAG, "📦 收到流数据: tag=$tag, size=${data.size}")
        }
    }
    
    // 降级方案：ICustomCmdCbk（如果标准API不可用）
    private val cmdCallback = object : ICustomCmdCbk {
        override fun onCustomCmdResult(key: String?, payload: ByteArray?) {
            if (key == TOPIC_WORK_RECORD && payload != null) {
                try {
                    val message = String(payload, StandardCharsets.UTF_8)
                    Log.d(TAG, "📨 降级消息: $message")
                    handleWorkRecordMessage(message)
                } catch (e: Exception) {
                    Log.e(TAG, "❌ 解析降级消息失败", e)
                }
            }
        }
    }
    
    fun register() {
        var registered = false
        
        // 方案1：使用标准 IMessage API
        try {
            val messageService = PSecuritySDK.getMessageService()
            if (messageService != null) {
                messageService.addMessageListener(messageListener)
                Log.d(TAG, "✅ 消息接收服务已注册（标准API）")
                registered = true
            } else {
                Log.w(TAG, "⚠️ MessageService 不可用，请检查 SDK 初始化")
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ 标准API注册失败: ${e.message}", e)
        }
        
        // 方案2：降级使用 CXRLink
        if (!registered) {
            try {
                val app = CXRLApplication.instance
                if (app.sharedLink != null && app.isSessionReady) {
                    app.requireReadyLink().setCXRCustomCmdCbk(cmdCallback)
                    Log.d(TAG, "✅ 消息接收服务已注册（降级方案）")
                    registered = true
                } else {
                    Log.w(TAG, "⚠️ CXRLink 未就绪")
                }
            } catch (e: Exception) {
                Log.e(TAG, "❌ 降级方案注册失败: ${e.message}", e)
            }
        }
        
        if (!registered) {
            Log.e(TAG, "❌ 所有注册方案均失败")
        }
    }
    
    fun unregister() {
        // 移除标准监听器
        try {
            val messageService = PSecuritySDK.getMessageService()
            messageService?.removeMessageListener(messageListener)
            Log.d(TAG, "✅ 消息监听器已移除")
        } catch (e: Exception) {
            Log.e(TAG, "❌ 移除消息监听失败", e)
        }
        
        // 清空降级回调
        try {
            val app = CXRLApplication.instance
            app.sharedLink?.setCXRCustomCmdCbk(object : ICustomCmdCbk {
                override fun onCustomCmdResult(key: String?, payload: ByteArray?) {}
            })
            Log.d(TAG, "✅ 降级回调已清空")
        } catch (e: Exception) {
            Log.e(TAG, "❌ 清空降级回调失败", e)
        }
    }
    
    private fun handleWorkRecordMessage(message: String) {
        try {
            val json = JSONObject(message)
            val type = json.optString("type")
            
            when (type) {
                "session_complete" -> {
                    val sessionId = json.optString("session_id")
                    val workType = json.optString("work_type")
                    val photoCount = json.optInt("photo_count")
                    val voiceSummary = json.optString("voice_summary")
                    val createdAt = json.optString("created_at")
                    
                    Log.d(TAG, "📝 会话完成: id=$sessionId, type=$workType, photos=$photoCount")
                    
                    if (workType == "待定") {
                        val session = PendingSession(
                            id = sessionId,
                            imagesCount = photoCount,
                            voiceSummary = voiceSummary,
                            createdAt = createdAt,
                            workType = workType
                        )
                        _pendingSessions.add(0, session)
                        Log.d(TAG, "✅ 已添加待定记录，当前总数: ${_pendingSessions.size}")
                        
                        listeners.forEach { 
                            try {
                                it.onPendingSessionReceived(session)
                            } catch (e: Exception) {
                                Log.e(TAG, "❌ 通知监听器失败", e)
                            }
                        }
                    } else {
                        Log.d(TAG, "ℹ️ 工作类型已确认: $workType，不加入待定列表")
                    }
                }
                "confirm_type" -> {
                    Log.d(TAG, "📨 收到确认消息回执")
                }
                "hazard_record" -> {
                    handleHazardRecord(message)
                }
                else -> {
                    Log.w(TAG, "⚠️ 未知消息类型: $type")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ 处理消息失败: $message", e)
        }
    }
    
    /**
     * 处理隐患记录
     */
    private fun handleHazardRecord(message: String) {
        try {
            val json = JSONObject(message)
            val type = json.optString("type")
            
            if (type == "hazard_record") {
                val hazardCount = json.optInt("hazard_count")
                val hazardsArray = json.optJSONArray("hazards")
                val imageId = json.optString("image_id")
                val imageUrl = json.optString("image_url")
                val timestamp = json.optLong("timestamp")
                
                Log.d(TAG, "📋 收到隐患记录: $hazardCount 处隐患")
                
                // 解析隐患列表
                val hazards = mutableListOf<Map<String, String>>()
                if (hazardsArray != null) {
                    for (i in 0 until hazardsArray.length()) {
                        val hazardJson = hazardsArray.getJSONObject(i)
                        hazards.add(mapOf(
                            "id" to hazardJson.optString("id"),
                            "top_risk" to hazardJson.optString("top_risk"),
                            "risk_status" to hazardJson.optString("risk_status"),
                            "risk_category" to hazardJson.optString("risk_category"),
                            "section" to hazardJson.optString("section")
                        ))
                    }
                }
                
                // 显示每个隐患
                hazards.forEach { hazard ->
                    Log.d(TAG, "  ⚠️ ${hazard["top_risk"]} (${hazard["risk_status"]})")
                }
                
                // TODO: 保存到本地数据库或通知UI
                // 可以在这里添加通知用户或更新UI的逻辑
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ 处理隐患记录失败", e)
        }
    }
    
    interface OnPendingSessionReceivedListener {
        fun onPendingSessionReceived(session: PendingSession)
    }
}
