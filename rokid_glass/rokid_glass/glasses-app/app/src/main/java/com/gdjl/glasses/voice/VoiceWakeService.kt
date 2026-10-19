package com.gdjl.glasses.voice

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import com.gdjl.glasses.R

/**
 * 全程语音唤起服务（前台服务）。
 *
 * 工作方式：
 * - 应用**退到后台**时，通过云端 STT 循环监听唤起词；
 * - 命中唤起词后拉起对应页面并 TTS 应答；
 * - 应用**回到前台**时立即暂停监听（麦克风让给页面语音，避免抢占）；
 * - 断网时按指数退避拉长监听间隔（省电），网络恢复自动回到正常节奏。
 *
 * 唤起词：
 * - 「你好监理 / 监理监理 / 小监」→ 回主菜单并应答
 * - 「工作记录 / 开始工作」→ 直接进入工作记录会话
 * - 「安全隐患 / 隐患」→ 进入安全隐患
 *
 * 注意：录音 + 上传循环在后台持续运行有明显耗电，属于「全程语音唤起」的固有代价；
 * 后台通过 [setWakeEnabled] 可整体关闭。
 */
class VoiceWakeService : Service() {

    companion object {
        private const val TAG = "VoiceWakeService"
        private const val CHANNEL_ID = "voice_wake"
        private const val NOTIFICATION_ID = 42
        private const val NORMAL_GAP_MS = 800L
        private const val MAX_BACKOFF_GAP_MS = 30_000L
        private const val WAKE_LOCK_TIMEOUT_MS = 3 * 60 * 60 * 1000L // 3 小时兜底，防泄漏

        @Volatile
        private var wakeEnabled = true

        fun start(context: Context) {
            try {
                context.startForegroundService(Intent(context, VoiceWakeService::class.java))
            } catch (e: Exception) {
                Log.e(TAG, "启动语音唤起服务失败", e)
            }
        }

        fun setWakeEnabled(context: Context, enabled: Boolean) {
            wakeEnabled = enabled
            if (enabled) start(context)
            else context.stopService(Intent(context, VoiceWakeService::class.java))
        }
    }

    private lateinit var recognizer: SpeechRecognizerManager
    private lateinit var voicePlayer: VoicePlayer
    private var wakeLock: PowerManager.WakeLock? = null
    private var listening = false
    private var networkFailCount = 0

    override fun onCreate() {
        super.onCreate()
        startForeground(NOTIFICATION_ID, buildNotification())
        recognizer = SpeechRecognizerManager(this)
        voicePlayer = VoicePlayer(this).apply { init() }

        // 前后台协调：前台让麦，后台监听
        AppForegroundTracker.listener = { foreground ->
            if (foreground) pauseWake() else resumeWake()
        }
        Log.i(TAG, "语音唤起服务已创建")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!AppForegroundTracker.isForeground) resumeWake()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        pauseWake()
        if (AppForegroundTracker.listener != null) AppForegroundTracker.listener = null
        recognizer.release()
        voicePlayer.release()
        Log.i(TAG, "语音唤起服务已销毁")
        super.onDestroy()
    }

    // ---------------- 唤起监听 ----------------

    private fun resumeWake() {
        if (!wakeEnabled || listening) return
        listening = true
        acquireWakeLock()
        networkFailCount = 0
        recognizer.loopGapMs = NORMAL_GAP_MS
        recognizer.startListening(object : SpeechRecognizerManager.Listener {
            override fun onResult(text: String) {
                networkFailCount = 0
                recognizer.loopGapMs = NORMAL_GAP_MS
                handleWakeText(text)
            }

            override fun onError(errorCode: Int) {
                if (errorCode == SpeechRecognizerManager.ERROR_NETWORK) {
                    // 断网指数退避：0.8s -> 1.6s -> ... -> 30s 封顶
                    networkFailCount++
                    val backoff = NORMAL_GAP_MS shl networkFailCount.coerceAtMost(6)
                    recognizer.loopGapMs = backoff.coerceAtMost(MAX_BACKOFF_GAP_MS)
                }
            }
        })
        Log.i(TAG, "后台语音唤起监听已启动")
    }

    private fun pauseWake() {
        if (!listening) return
        listening = false
        recognizer.stopListening()
        releaseWakeLock()
        Log.i(TAG, "应用前台，唤起监听已暂停")
    }

    private fun handleWakeText(text: String) {
        Log.d(TAG, "唤起识别: $text")
        when {
            text.contains("工作记录") || text.contains("开始工作") ->
                wakeTo("com.gdjl.glasses.ui.workrecord.WorkRecordSessionActivity", "进入工作记录")
            text.contains("安全隐患") || text.contains("隐患") ->
                wakeTo("com.gdjl.glasses.ui.hazard.HazardSubListActivity", "进入安全隐患")
            text.contains("你好监理") || text.contains("监理监理") || text.contains("小监") ->
                wakeTo("com.gdjl.glasses.ui.menu.MenuActivity", "我在，请说指令")
            // 非唤起词：静默忽略，继续监听
        }
    }

    private fun wakeTo(activityClassName: String, greeting: String) {
        try {
            wakeScreen()
            val intent = Intent().apply {
                setClassName(this@VoiceWakeService, activityClassName)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            }
            startActivity(intent)
            voicePlayer.speak(greeting)
            Log.i(TAG, "已唤起: $activityClassName")
        } catch (e: Exception) {
            Log.e(TAG, "唤起页面失败: $activityClassName", e)
        }
    }

    /** 灭屏语音唤起时点亮屏幕（否则页面拉起了用户也看不见） */
    @Suppress("DEPRECATION")
    private fun wakeScreen() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            if (pm.isInteractive) return
            pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or
                    PowerManager.ACQUIRE_CAUSES_WAKEUP or
                    PowerManager.ON_AFTER_RELEASE,
                "gdjl:wakeScreen"
            ).acquire(3000L)
            Log.i(TAG, "已点亮屏幕")
        } catch (e: Exception) {
            Log.e(TAG, "点亮屏幕失败", e)
        }
    }

    // ---------------- 通知与唤醒锁 ----------------

    private fun buildNotification(): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "语音唤起", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val launchIntent = packageManager.getLaunchIntentForPackage(packageName)
        val pendingIntent = launchIntent?.let {
            PendingIntent.getActivity(
                this, 0, it,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("监理通")
            .setContentText("语音唤起待命中，说「你好监理」唤醒")
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) return
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "gdjl:voiceWake").apply {
                acquire(WAKE_LOCK_TIMEOUT_MS)
            }
        } catch (e: Exception) {
            Log.e(TAG, "获取唤醒锁失败", e)
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (e: Exception) {
            Log.e(TAG, "释放唤醒锁失败", e)
        }
        wakeLock = null
    }
}
