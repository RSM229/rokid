package com.gdjl.glasses.voice

/**
 * 应用前后台状态追踪。
 *
 * 由 GdjlApplication 的 ActivityLifecycleCallbacks 驱动，
 * 供 [VoiceWakeService] 判断：前台时暂停唤起监听（避免与页面语音抢麦克风），
 * 后台时恢复监听（全程语音唤起）。
 */
object AppForegroundTracker {

    @Volatile
    var isForeground: Boolean = false
        private set

    /** 前后台切换回调（可能运行在任意线程，回调方自行切线程） */
    @Volatile
    var listener: ((Boolean) -> Unit)? = null

    private var startedCount = 0

    @Synchronized
    fun onActivityStarted() {
        startedCount++
        if (!isForeground) {
            isForeground = true
            listener?.invoke(true)
        }
    }

    @Synchronized
    fun onActivityStopped() {
        startedCount = (startedCount - 1).coerceAtLeast(0)
        if (startedCount == 0 && isForeground) {
            isForeground = false
            listener?.invoke(false)
        }
    }
}
