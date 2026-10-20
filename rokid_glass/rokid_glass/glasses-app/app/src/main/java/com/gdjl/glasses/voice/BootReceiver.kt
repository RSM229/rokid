package com.gdjl.glasses.voice

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * 开机自启语音唤起服务。
 *
 * 注意：部分系统版本对「后台启动麦克风类前台服务」有限制，
 * 若开机启动后麦克风不可用，唤醒监听会以 ERROR_AUDIO 静默重试，
 * 用户首次手动打开应用后即恢复正常（应用在前台启动服务不受限）。
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Log.i("BootReceiver", "开机完成，启动语音唤起服务")
            VoiceWakeService.start(context)
        }
    }
}
