// 创建日期: 2026-07-18
// 2026-09-30 重构：语音识别通道从「MediaRecorder 录音 + 云端 STT」切换为「Rokid SDK ASR」。
// 根因：Rokid 眼镜的麦克风被系统语音栈（com.rokid.glass.ota / system.server）常驻独占，
// 三方 App 直接录音会被音频策略静音（RecordActivityMonitor 显示 silenced），云端 STT 恒为空。
// SDK ASR 走系统服务通道，不受静音策略影响。公共 API 保持不变。
package com.gdjl.glasses.voice

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.gdjl.glasses.service.WorkRecordService
import com.rokid.security.system.server.asr.listener.SpeechCallback
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/**
 * 语音识别管理器（Rokid SDK ASR 通道）。
 *
 * - [startListening] 后台线程等待 SDK 就绪后开启流式 ASR 会话，
 *   每识别出一句话通过 [Listener.onResult] 回调（主线程），会话持续有效；
 * - 会话出错/断开时按 [loopGapMs] 间隔自动重开；
 * - [stopListening] 关闭 ASR 会话（把麦克风还给系统/其他页面）。
 *
 * 注意：ASR 会话全设备通常一路，WorkRecordService 的工作会话 ASR 与本通道
 * 不能同时开。依赖既有仲裁：唤起服务仅后台监听、页面监听 onPause 即停。
 */
class SpeechRecognizerManager(private val context: Context) {

    companion object {
        private const val TAG = "SpeechRecognizer"
        private const val LOOP_GAP_MS = 800L
        private const val SDK_WAIT_MS = 15000L
        const val ERROR_AUDIO = 1
        const val ERROR_NO_MATCH = 2
        const val ERROR_NETWORK = 3
        const val ERROR_TIMEOUT = 4
        const val ERROR_NOT_RUNNING = 5

        /**
         * 当前活跃监听者（最后调用 startListening 的实例登记的 listener）。
         *
         * 2026-09-30 实测：本机 Rokid 固件的流式 ASR 只对系统离线指令引擎分发结果，
         * App 的 SpeechCallback 收不到任何文本回调；唯一可靠的文本来源是
         * 离线指令触发。因此 WorkRecordService 的离线指令回调在无人接收时，
         * 通过 [injectOfflineCommand] 把指令词注入给当前活跃监听者——
         * 前台时是页面语音监听（走页面指令分派），后台时是唤起服务（走唤起词分派）。
         */
        @Volatile
        private var activeListener: Listener? = null

        /** 注入一条离线指令识别结果（主线程回调给当前活跃监听者）。 */
        fun injectOfflineCommand(text: String) {
            val target = activeListener
            Log.i(TAG, "离线指令注入: $text -> ${if (target != null) "已分发" else "无监听者，丢弃"}")
            if (target != null) {
                Handler(Looper.getMainLooper()).post { target.onResult(text) }
            }
        }
    }

    /** 语音识别结果回调（在主线程） */
    interface Listener {
        /** 成功识别到文本 */
        fun onResult(text: String)
        /** 识别出错；[errorCode] 见 ERROR_* 常量 */
        fun onError(errorCode: Int)
    }

    private val running = AtomicBoolean(false)
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()

    @Volatile
    private var listener: Listener? = null

    /** 会话重开间隔（毫秒）；唤起服务在断网时会加大此值省电。 */
    @Volatile
    var loopGapMs: Long = LOOP_GAP_MS

    private val lastResultAt = AtomicLong(0L)

    @Volatile
    private var lastText = ""

    fun setListener(listener: Listener?) {
        this.listener = listener
    }

    fun isAvailable(): Boolean = true

    /**
     * 启动持续监听：等待 SDK 就绪 → 开启 ASR 流式会话 → 结果逐句回调。
     * 单例运行，重复调用将被忽略。
     */
    fun startListening(listener: Listener) {
        this.listener = listener
        activeListener = listener
        if (!running.compareAndSet(false, true)) {
            Log.d(TAG, "持续监听已在运行")
            return
        }
        Log.i(TAG, "持续监听启动（Rokid ASR 通道）")
        executor.execute {
            WorkRecordService.ensureInitialized(context)
            var waited = 0L
            while (running.get() && !WorkRecordService.isSdkReady && waited < SDK_WAIT_MS) {
                Thread.sleep(500)
                waited += 500
            }
            if (!running.get()) return@execute
            if (!WorkRecordService.isSdkReady) {
                Log.e(TAG, "等待 Rokid SDK 就绪超时")
                postError(ERROR_AUDIO)
                running.set(false)
                return@execute
            }
            openSession()
        }
    }

    /** 停止持续监听并关闭 ASR 会话。 */
    fun stopListening() {
        if (activeListener === listener) activeListener = null
        if (running.compareAndSet(true, false)) {
            Log.i(TAG, "持续监听停止")
            runCatching { WorkRecordService.sdkAsrService?.stopSpeech() }
        }
    }

    /**
     * 单次语音识别（按键/触控场景）：开启监听，首个结果（或首个错误）后即停止。
     */
    fun recognizeOnce(listener: Listener) {
        startListening(object : Listener {
            override fun onResult(text: String) {
                stopListening()
                Handler(Looper.getMainLooper()).post { listener.onResult(text) }
            }
            override fun onError(errorCode: Int) {
                stopListening()
                Handler(Looper.getMainLooper()).post { listener.onError(errorCode) }
            }
        })
    }

    fun release() {
        stopListening()
        executor.shutdownNow()
    }

    // ---------------- 内部实现 ----------------

    private val speechCallback = object : SpeechCallback.Stub() {
        override fun onStart() {
            Log.d(TAG, "ASR 会话已开始")
        }

        override fun onIntermediateVad(content: String?) {}

        override fun onAsrComplete(content: String?) {
            deliver(content)
        }

        override fun onAsrCompleteWithIntent(content: String?, intent: Int, intentJson: String?) {
            deliver(content)
        }

        override fun onError(code: Int) {
            Log.e(TAG, "ASR 错误: $code")
            postError(ERROR_NETWORK)
            scheduleRestart()
        }

        override fun onServiceConnectState(connect: Boolean) {
            if (!connect) {
                Log.e(TAG, "ASR 服务连接断开")
                postError(ERROR_NETWORK)
                scheduleRestart()
            }
        }
    }

    private fun deliver(content: String?) {
        val text = content?.trim().orEmpty()
        if (text.isEmpty() || !running.get()) return
        // onAsrComplete 与 onAsrCompleteWithIntent 可能对同一句都回调，1.5 秒内同文本去重
        val now = System.currentTimeMillis()
        if (text == lastText && now - lastResultAt.get() < 1500) return
        lastText = text
        lastResultAt.set(now)
        postResult(text)
    }

    private fun openSession() {
        val asr = WorkRecordService.sdkAsrService
        if (asr == null) {
            Log.e(TAG, "ASR 服务不可用")
            postError(ERROR_AUDIO)
            running.set(false)
            return
        }
        try {
            asr.startSpeech(speechCallback)
        } catch (e: Exception) {
            Log.e(TAG, "启动 ASR 会话失败", e)
            postError(ERROR_AUDIO)
            scheduleRestart()
        }
    }

    private fun scheduleRestart() {
        if (!running.get()) return
        executor.execute {
            try {
                Thread.sleep(loopGapMs)
            } catch (_: InterruptedException) {
                return@execute
            }
            if (running.get() && WorkRecordService.isSdkReady) openSession()
        }
    }

    private fun postResult(text: String) {
        Handler(Looper.getMainLooper()).post {
            listener?.onResult(text)
        }
    }

    private fun postError(code: Int) {
        Handler(Looper.getMainLooper()).post {
            listener?.onError(code)
        }
    }
}
