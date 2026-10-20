package com.gdjl.glasses.command

import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.widget.TextView
import com.gdjl.glasses.voice.VoicePlayer
import com.google.gson.JsonObject

/**
 * 指令处理器
 *
 * 执行具体的指令动作：语音播报、界面展示、震动等。
 */
class CommandHandler(
    private val voicePlayer: VoicePlayer,
    private val statusView: TextView,
    private val contentView: TextView,
) {

    fun dispatch(action: String, cmd: JsonObject) {
        when (action) {
            "speak" -> {
                val text = cmd.get("text")?.asString ?: ""
                voicePlayer.speak(text)
            }
            "speak_urgent" -> {
                val text = cmd.get("text")?.asString ?: ""
                voicePlayer.speakUrgent(text)
                vibrate()
            }
            "show_hazard" -> {
                val data = cmd.getAsJsonObject("data")
                showHazard(data)
            }
            "show_guide" -> {
                val data = cmd.getAsJsonObject("data")
                showGuide(data)
            }
            "show_rectification" -> {
                val data = cmd.getAsJsonObject("data")
                showRectification(data)
            }
            "show_recheck" -> {
                val data = cmd.getAsJsonObject("data")
                showRecheck(data)
            }
            "vibrate" -> vibrate()
            "start_inspection" -> {
                statusView.text = "AI安全巡检已启动"
                voicePlayer.speak("安全AI巡检已启动")
            }
            "stop_inspection" -> {
                statusView.text = "AI安全巡检已停止"
                voicePlayer.speak("安全AI巡检已停止")
            }
            "show_document" -> {
                val data = cmd.getAsJsonObject("data")
                showDocument(data)
            }
            "clear_display" -> {
                contentView.text = ""
                statusView.text = "就绪"
            }
            else -> {
                android.util.Log.w(TAG, "未知指令: $action")
            }
        }
    }

    /**
     * 展示隐患识别结果
     */
    private fun showHazard(data: JsonObject?) {
        if (data == null) return
        val riskStatus = data.get("risk_status")?.asString ?: ""
        val top1Risk = data.get("top1_risk")?.asString ?: ""
        val category = data.get("risk_category")?.asString ?: ""

        val sb = StringBuilder()
        sb.append("【隐患识别结果】\n")
        sb.append("风险状态: $riskStatus\n")
        sb.append("风险类别: $category\n")
        sb.append("主要风险: $top1Risk\n")

        val evidence = data.getAsJsonArray("evidence")
        if (evidence != null && evidence.size() > 0) {
            sb.append("证据:\n")
            for (i in 0 until evidence.size()) {
                sb.append("  - ${evidence[i].asString}\n")
            }
        }

        contentView.text = sb.toString()

        // 语音播报
        if (riskStatus == "存在隐患") {
            voicePlayer.speakUrgent("警告，发现安全隐患：$top1Risk")
            vibrate()
        } else if (riskStatus == "不存在隐患") {
            voicePlayer.speak("未发现安全隐患")
        }
    }

    /**
     * 展示验收指引
     */
    private fun showGuide(data: JsonObject?) {
        if (data == null) return
        val sb = StringBuilder()
        sb.append("【验收流程指引】\n")

        val steps = data.getAsJsonArray("steps")
        if (steps != null) {
            sb.append("验收步骤:\n")
            for (i in 0 until steps.size()) {
                sb.append("${i + 1}. ${steps[i].asString}\n")
            }
        }

        val keyPoints = data.getAsJsonArray("key_points")
        if (keyPoints != null) {
            sb.append("\n管控要点:\n")
            for (i in 0 until keyPoints.size()) {
                sb.append("  - ${keyPoints[i].asString}\n")
            }
        }

        contentView.text = sb.toString()
    }

    /**
     * 展示整改通知单
     */
    private fun showRectification(data: JsonObject?) {
        if (data == null) return
        val sb = StringBuilder()
        sb.append("【整改通知单】\n")
        sb.append("编号: ${data.get("doc_id")?.asString ?: ""}\n")
        sb.append("隐患: ${data.get("hazard_description")?.asString ?: ""}\n")
        sb.append("部位: ${data.get("section")?.asString ?: ""}\n")
        sb.append("期限: ${data.get("deadline")?.asString ?: ""}\n")
        contentView.text = sb.toString()
        voicePlayer.speak("整改通知单已生成")
    }

    /**
     * 展示复查结果
     */
    private fun showRecheck(data: JsonObject?) {
        if (data == null) return
        val passed = data.get("passed")?.asBoolean ?: false
        val sb = StringBuilder()
        sb.append("【复查结果】\n")
        sb.append(if (passed) "状态: 复查合格，已闭环\n" else "状态: 驳回整改\n")
        sb.append("隐患ID: ${data.get("hazard_id")?.asString ?: ""}\n")
        contentView.text = sb.toString()
    }

    /**
     * 展示文书信息
     */
    private fun showDocument(data: JsonObject?) {
        if (data == null) return
        val sb = StringBuilder()
        sb.append("【监理文书】\n")
        sb.append("类型: ${data.get("doc_type")?.asString ?: ""}\n")
        sb.append("标题: ${data.get("title")?.asString ?: ""}\n")
        sb.append("状态: ${data.get("status")?.asString ?: ""}\n")
        contentView.text = sb.toString()
    }

    /**
     * 震动提示
     */
    private fun vibrate() {
        val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val vm = statusView.context.getSystemService(VibratorManager::class.java)
            vm?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            statusView.context.getSystemService(Vibrator::class.java)
        }
        vibrator?.let {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                it.vibrate(VibrationEffect.createOneShot(500, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                it.vibrate(500)
            }
        }
    }

    companion object {
        private const val TAG = "CommandHandler"
    }
}
