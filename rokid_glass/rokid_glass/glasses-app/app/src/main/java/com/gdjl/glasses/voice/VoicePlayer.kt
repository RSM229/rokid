package com.gdjl.glasses.voice

import android.content.Context
import android.speech.tts.TextToSpeech
import android.util.Log
import com.gdjl.glasses.service.WorkRecordService
import java.util.Locale

/**
 * 语音播报器
 *
 * 使用 Android TextToSpeech 引擎在眼镜端实现语音播报。
 * 接收后端下发的播报指令，将文本转为语音输出。
 */
class VoicePlayer(private val context: Context) {

    private var tts: TextToSpeech? = null
    private var ready = false

    fun init() {
        tts = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.CHINESE
                ready = true
                Log.i(TAG, "TTS 初始化成功")
            } else {
                Log.e(TAG, "TTS 初始化失败: $status")
            }
        }
    }

    /**
     * 播报文本：优先 Rokid SDK TTS（系统语音通道，设备必定可用），
     * SDK 未就绪或调用失败时回退 Android TextToSpeech。
     */
    fun speak(text: String) {
        if (speakViaSdk(text)) return
        if (ready) {
            tts?.speak(text, TextToSpeech.QUEUE_ADD, null, "msg_${System.currentTimeMillis()}")
        } else {
            Log.w(TAG, "TTS 未就绪，跳过播报: $text")
        }
    }

    /**
     * 紧急播报（清空队列立即播报）
     */
    fun speakUrgent(text: String) {
        if (speakViaSdk(text)) return
        if (ready) {
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "urgent_${System.currentTimeMillis()}")
        }
    }

    private fun speakViaSdk(text: String): Boolean {
        val sdk = WorkRecordService.sdkTtsService ?: return false
        return try {
            sdk.doSpeechTts(text)
            true
        } catch (e: Exception) {
            Log.w(TAG, "SDK TTS 调用失败，回退系统 TTS", e)
            false
        }
    }

    fun release() {
        tts?.stop()
        tts?.shutdown()
        tts = null
    }

    companion object {
        private const val TAG = "VoicePlayer"
    }
}
