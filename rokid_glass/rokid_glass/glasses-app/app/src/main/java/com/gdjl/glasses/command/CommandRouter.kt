package com.gdjl.glasses.command

import android.widget.TextView
import com.gdjl.glasses.voice.VoicePlayer
import com.google.gson.Gson
import com.google.gson.JsonObject

/**
 * 自定义指令路由
 *
 * 解析后端下发的自定义指令，分发到对应处理器。
 *
 * 指令格式（JSON）：
 * - {"action": "speak", "text": "发现安全隐患：未戴安全帽"}
 * - {"action": "speak_urgent", "text": "紧急提醒"}
 * - {"action": "show_hazard", "data": {"risk_status": "存在隐患", "top1_risk": "...", ...}}
 * - {"action": "show_guide", "data": {"steps": [...], "key_points": [...]}}
 * - {"action": "vibrate"}
 * - {"action": "start_inspection"}
 * - {"action": "stop_inspection"}
 */
class CommandRouter(
    private val voicePlayer: VoicePlayer,
    private val statusView: TextView,
    private val contentView: TextView,
    private val businessAction: ((String, JsonObject) -> Boolean)? = null,
) {
    private val gson = Gson()
    private val handler = CommandHandler(voicePlayer, statusView, contentView)

    fun handle(commandJson: String) {
        try {
            val cmd = gson.fromJson(commandJson, JsonObject::class.java)
            val action = cmd.get("action")?.asString ?: return
            if (businessAction?.invoke(action, cmd) != true) {
                handler.dispatch(action, cmd)
            }
        } catch (e: Exception) {
            android.util.Log.e(TAG, "指令解析失败: $commandJson", e)
        }
    }

    companion object {
        private const val TAG = "CommandRouter"
    }
}
