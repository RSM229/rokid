// 创建日期: 2026-07-18
package com.gdjl.glasses.voice

import android.content.Context
import android.util.Log
import com.gdjl.glasses.network.ApiService
import com.gdjl.glasses.network.models.VoiceCommandRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class VoiceCommandRouter(
    private val context: Context,
    private val callback: Callback,
) {

    companion object {
        private const val TAG = "VoiceCommandRouter"
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun route(command: String) {
        val text = command.trim()
        Log.i(TAG, "路由语音指令: $text")

        when (val parsed = VoiceCommandParser.parse(text)) {
            LocalVoiceCommand.StartInspection -> callback.onStartInspection()
            LocalVoiceCommand.StopInspection -> callback.onStopInspection()
            LocalVoiceCommand.TakePhoto -> callback.onTakePhoto()
            
            // ===== 新增：录像 =====
            LocalVoiceCommand.StartVideo -> callback.onStartVideo()
            LocalVoiceCommand.StopVideo -> callback.onStopVideo()
            
            // ===== 新增：录音 =====
            LocalVoiceCommand.StartRecording -> callback.onStartRecording()
            LocalVoiceCommand.StopRecording -> callback.onStopRecording()
            
            // ===== 新增：语音描述 =====
            is LocalVoiceCommand.VoiceDescription -> callback.onVoiceDescription(parsed.text)
            
            // ===== 新增：完成工作 =====
            LocalVoiceCommand.FinishWork -> callback.onFinishWork()
            
            // ===== 新增：选择工作类型 =====
            is LocalVoiceCommand.SelectWorkType -> callback.onSelectWorkType(parsed.type)
            
            is LocalVoiceCommand.AcceptanceGuide -> callback.onAcceptanceGuide(parsed.keyword)
            is LocalVoiceCommand.QueryRegulation -> callback.onQueryRegulation(parsed.keyword)
            is LocalVoiceCommand.GenerateDocument -> callback.onGenerateDocument(parsed.documentType)
            LocalVoiceCommand.CreateRectification -> callback.onCreateRectificationDoc()
            is LocalVoiceCommand.Recheck -> callback.onRecheck(parsed.pass)
            LocalVoiceCommand.TaskList -> callback.onTaskList()
            null -> smartMatch(text)
        }
    }

    private fun smartMatch(text: String) {
        scope.launch {
            val result = ApiService.matchVoiceCommand(VoiceCommandRequest(text = text, context = "glasses"))
            when (result) {
                is com.gdjl.glasses.network.models.ApiResult.Success -> {
                    val resp = result.data
                    val reply = resp.reply ?: "未识别到有效指令"
                    Log.i(TAG, "智能匹配意图=${resp.intent} reply=$reply")
                    callback.onSmartReply(intent = resp.intent, reply = reply)
                }
                is com.gdjl.glasses.network.models.ApiResult.Error -> {
                    Log.w(TAG, "智能匹配失败: ${result.code} ${result.message}")
                    callback.onSmartReply(intent = null, reply = "指令未匹配，请重试")
                }
                is com.gdjl.glasses.network.models.ApiResult.Exception -> {
                    Log.e(TAG, "智能匹配异常", result.throwable)
                    callback.onSmartReply(intent = null, reply = "网络异常，请检查连接")
                }
            }
        }
    }

    interface Callback {
        fun onStartInspection()
        fun onStopInspection()
        fun onTakePhoto()
        
        // ===== 新增回调 =====
        fun onStartVideo()
        fun onStopVideo()
        fun onStartRecording()
        fun onStopRecording()
        fun onVoiceDescription(text: String)
        fun onFinishWork()
        fun onSelectWorkType(type: String)
        
        fun onQueryRegulation(keyword: String)
        fun onAcceptanceGuide(keyword: String)
        fun onGenerateDocument(docType: String)
        fun onCreateRectificationDoc()
        fun onRecheck(pass: Boolean)
        fun onTaskList()
        fun onSmartReply(intent: String?, reply: String)
    }
}
