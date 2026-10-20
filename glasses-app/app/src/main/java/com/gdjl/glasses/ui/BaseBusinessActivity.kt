// 创建日期: 2026-07-18
package com.gdjl.glasses.ui

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.MutableLiveData
import com.gdjl.glasses.R
import com.gdjl.glasses.service.WorkRecordService
import com.gdjl.glasses.voice.SpeechRecognizerManager
import com.gdjl.glasses.voice.VoicePlayer
import com.gdjl.glasses.voice.VoiceWakeService

/**
 * 业务 Activity 基类
 *
 * 统一提供：
 * 1. 顶部标题栏 + 返回按钮
 * 2. [VoicePlayer] 语音播报
 * 3. [SpeechRecognizerManager] 单次语音识别
 * 4. Loading 切换、Toast、Toast 包装
 *
 * 子类在 onCreate 中调用 [setupToolbar] 设置标题，并按需使用 [speak]/[startVoiceInput]。
 */
abstract class BaseBusinessActivity : AppCompatActivity() {

    protected lateinit var voicePlayer: VoicePlayer
        private set
    protected lateinit var speechRecognizer: SpeechRecognizerManager
        private set

    private var tvTitle: TextView? = null
    private var btnBack: View? = null
    private var loadingView: View? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        voicePlayer = VoicePlayer(this).apply { init() }
        speechRecognizer = SpeechRecognizerManager(this)
    }

    /**
     * 设置顶部标题栏
     *
     * 子类在 setContentView 后调用：
     * ```
     * setupToolbar(R.id.tv_title, R.id.btn_back, "工作记录")
     * ```
     */
    protected fun setupToolbar(titleViewId: Int, backViewId: Int, title: String) {
        tvTitle = findViewById(titleViewId)
        btnBack = findViewById(backViewId)
        tvTitle?.text = title
        btnBack?.setOnClickListener { finish() }
    }

    /** 绑定 loading 视图，供 [setLoading] 控制 */
    protected fun bindLoading(loadingViewId: Int) {
        loadingView = findViewById(loadingViewId)
    }

    /** 切换 loading 显示 */
    protected fun setLoading(loading: Boolean) {
        loadingView?.visibility = if (loading) View.VISIBLE else View.GONE
    }

    /** 语音播报 */
    protected fun speak(text: String) {
        if (::voicePlayer.isInitialized) voicePlayer.speak(text)
    }

    /** 紧急语音播报（清空队列立即播报） */
    protected fun speakUrgent(text: String) {
        if (::voicePlayer.isInitialized) voicePlayer.speakUrgent(text)
    }

    /**
     * 启动单次语音识别
     *
     * @param onResult 识别结果回调（主线程）
     * @param onError 错误回调（主线程），可空
     */
    protected fun startVoiceInput(onResult: (String) -> Unit, onError: ((Int) -> Unit)? = null) {
        if (!speechRecognizer.isAvailable()) {
            toast("设备不支持语音识别")
            return
        }
        speechRecognizer.recognizeOnce(object : SpeechRecognizerManager.Listener {
            override fun onResult(text: String) = onResult(text)
            override fun onError(errorCode: Int) = onError?.invoke(errorCode) ?: Unit
        })
    }

    /**
     * 启动持续语音监听（全程语音）。
     *
     * 每识别到一段文本即回调 [onResult]，随后自动进入下一轮监听。
     *
     * @param onResult 识别结果回调（主线程），可反复触发
     * @param onError 错误回调（主线程），可空
     */
    protected fun startContinuousVoice(onResult: (String) -> Unit, onError: ((Int) -> Unit)? = null) {
        if (!speechRecognizer.isAvailable()) {
            toast("设备不支持语音识别")
            return
        }
        speechRecognizer.startListening(object : SpeechRecognizerManager.Listener {
            override fun onResult(text: String) = onResult(text)
            override fun onError(errorCode: Int) = onError?.invoke(errorCode) ?: Unit
        })
    }

    /** 停止持续语音监听 */
    protected fun stopContinuousVoice() {
        speechRecognizer.stopListening()
    }

    // ================= 全程语音：页面级语音交互脚手架 =================
    //
    // 子类用法：
    //   override fun voiceHint() = "说：拍照、重拍、返回"
    //   override fun onPageVoiceCommand(text: String): Boolean { ... return true 表示已处理 }
    //   onCreate 中调用 enablePageVoice()
    //
    // 效果：onResume 自动播报 voiceHint 并开始持续监听；onPause 自动停止。
    // 全局指令（返回 / 帮助·重播 / 回主菜单）由基类先处理，其余交给 onPageVoiceCommand。

    /** 页面语音操作提示；返回非空后进入页面自动播报，并作为「帮助」重播内容 */
    protected open fun voiceHint(): String? = null

    /** 页面专属语音指令；返回 true 表示已处理 */
    protected open fun onPageVoiceCommand(text: String): Boolean = false

    private var pageVoiceEnabled = false

    /** 开启页面全程语音（在 onCreate 中调用一次即可） */
    protected fun enablePageVoice() {
        pageVoiceEnabled = true
    }

    /** 播报页面操作提示（「帮助 / 重播」指令也会调它） */
    protected fun announceVoiceHint() {
        voiceHint()?.let { speak(it) }
    }

    override fun onResume() {
        super.onResume()
        // 唤起服务兜底启动：系统禁止应用在后台/灭屏时 startForegroundService
        // （Application.onCreate 里的启动会被静默拒绝），前台 onResume 是可靠启动点。
        VoiceWakeService.start(this)
        if (pageVoiceEnabled) {
            // SDK 就绪后 TTS 才可用（本机 Android TTS 无中文引擎）：
            // 未就绪时等就绪再播报提示，避免启动第一句被丢弃
            WorkRecordService.registerWhenReady {
                runOnUiThread { if (pageVoiceEnabled) announceVoiceHint() }
            }
            startContinuousVoice(
                onResult = { text -> handlePageVoice(text) },
                onError = { /* 持续监听场景下错误静默，下一轮自动重试 */ }
            )
        }
    }

    override fun onPause() {
        super.onPause()
        if (pageVoiceEnabled) stopContinuousVoice()
    }

    private fun handlePageVoice(text: String) {
        Log.i("BaseBusiness", "页面语音指令: $text (${this::class.java.simpleName})")
        when {
            text.contains("返回") || text.contains("退出") || text.contains("上一页") -> {
                speak("返回")
                finish()
            }
            // 前台说唤起词：应答并播报本页操作提示
            text.contains("你好监理") || text.contains("监理监理") || text.contains("小监") -> {
                speak("我在")
                announceVoiceHint()
            }
            text.contains("帮助") || text.contains("重播") || text.contains("怎么用") ||
                text.contains("功能") || text.contains("菜单") -> announceVoiceHint()
            text.contains("主菜单") || text.contains("回首页") || text.contains("回到首页") -> goHomeByVoice()
            else -> if (!onPageVoiceCommand(text)) {
                speak("未识别指令，说帮助可听取操作提示")
            }
        }
    }

    /** 语音回主菜单；当前已在主菜单时只重播提示 */
    private fun goHomeByVoice() {
        try {
            val intent = packageManager.getLaunchIntentForPackage(packageName) ?: return
            if (intent.component?.className == this::class.java.name) {
                announceVoiceHint()
                return
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            startActivity(intent)
        } catch (e: Exception) {
            Log.e("BaseBusiness", "语音回主菜单失败", e)
        }
    }

    /** Toast 简化 */
    protected fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }

    /** 短提示 + 语音播报 */
    protected fun toastAndSpeak(msg: String) {
        toast(msg)
        speak(msg)
    }

    override fun onDestroy() {
        super.onDestroy()
        if (::voicePlayer.isInitialized) voicePlayer.release()
        if (::speechRecognizer.isInitialized) speechRecognizer.release()
    }
}
