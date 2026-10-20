package com.gdjl.glasses.voice

/**
 * 不依赖 Android 运行时的本地语音意图解析器。
 *
 * 返回 null 表示本地规则未命中，调用方应交给服务端智能匹配。
 */
object VoiceCommandParser {
    fun parse(command: String): LocalVoiceCommand? {
        val text = command.trim()
        if (text.isEmpty()) return null

        when {
            // ===== 巡检 =====
            text.contains("启动安全AI巡检") ||
                text.contains("启动巡检") ||
                text.contains("开始巡检") -> return LocalVoiceCommand.StartInspection

            text.contains("停止巡检") ||
                text.contains("结束巡检") -> return LocalVoiceCommand.StopInspection

            // ===== 拍照 =====
            (text == "拍照" || text == "拍摄" || text.contains("拍照")) &&
                !text.contains("不") -> return LocalVoiceCommand.TakePhoto

            // ===== 录像 =====
            text.contains("录像") && !text.contains("停止") -> return LocalVoiceCommand.StartVideo
            text.contains("停止录像") -> return LocalVoiceCommand.StopVideo
            text.contains("开始录像") -> return LocalVoiceCommand.StartVideo

            // ===== 录音 =====
            text.contains("录音") && !text.contains("停止") -> return LocalVoiceCommand.StartRecording
            text.contains("停止录音") -> return LocalVoiceCommand.StopRecording
            text.contains("开始录音") -> return LocalVoiceCommand.StartRecording

            // ===== 语音描述 =====
            text.contains("描述") && text.length > 4 -> return LocalVoiceCommand.VoiceDescription(text)

            // ===== 完成工作 =====
            text.contains("完成") || text.contains("结束") || text.contains("保存") ->
                return LocalVoiceCommand.FinishWork

            // ===== 工作类型选择 =====
            text.contains("巡视") -> return LocalVoiceCommand.SelectWorkType("巡视记录")
            text.contains("验收") -> return LocalVoiceCommand.SelectWorkType("验收记录")
            text.contains("旁站") -> return LocalVoiceCommand.SelectWorkType("旁站记录")
            text.contains("见证") -> return LocalVoiceCommand.SelectWorkType("见证记录")
            text.contains("质量") && text.contains("问题") -> return LocalVoiceCommand.SelectWorkType("质量问题")
        }

        // ===== 带参数的指令 =====
        matchArgument(text, listOf("验收指引(.+)", "怎么验收(.+)"))
            ?.let { return LocalVoiceCommand.AcceptanceGuide(it) }
        matchArgument(text, listOf("查询规范(.+)", "查规范(.+)", "规范查询(.+)"))
            ?.let { return LocalVoiceCommand.QueryRegulation(it) }

        return when {
            text.contains("生成监理日志") -> LocalVoiceCommand.GenerateDocument("daily_log")
            text.contains("生成监理周报") -> LocalVoiceCommand.GenerateDocument("weekly_report")
            text.contains("生成监理月报") -> LocalVoiceCommand.GenerateDocument("monthly_report")
            text.contains("开具整改单") || text.contains("生成整改单") -> LocalVoiceCommand.CreateRectification

            text.contains("复查合格") || text.contains("复查通过") -> LocalVoiceCommand.Recheck(pass = true)
            text.contains("驳回整改") || text.contains("整改不合格") -> LocalVoiceCommand.Recheck(pass = false)

            text.contains("我的任务") || text == "待办" || text.contains("查看任务") -> LocalVoiceCommand.TaskList

            else -> null
        }
    }

    private fun matchArgument(text: String, patterns: List<String>): String? =
        patterns.firstNotNullOfOrNull { pattern ->
            Regex(pattern).find(text)
                ?.groupValues
                ?.getOrNull(1)
                ?.trim()
                ?.takeIf(String::isNotEmpty)
        }
}

sealed interface LocalVoiceCommand {
    // 巡检
    data object StartInspection : LocalVoiceCommand
    data object StopInspection : LocalVoiceCommand

    // 拍照
    data object TakePhoto : LocalVoiceCommand

    // 录像
    data object StartVideo : LocalVoiceCommand
    data object StopVideo : LocalVoiceCommand

    // 录音
    data object StartRecording : LocalVoiceCommand
    data object StopRecording : LocalVoiceCommand

    // 语音描述
    data class VoiceDescription(val text: String) : LocalVoiceCommand

    // 完成工作
    data object FinishWork : LocalVoiceCommand

    // 选择工作类型
    data class SelectWorkType(val type: String) : LocalVoiceCommand

    // 原有指令
    data class QueryRegulation(val keyword: String) : LocalVoiceCommand
    data class AcceptanceGuide(val keyword: String) : LocalVoiceCommand
    data class GenerateDocument(val documentType: String) : LocalVoiceCommand
    data object CreateRectification : LocalVoiceCommand
    data class Recheck(val pass: Boolean) : LocalVoiceCommand
    data object TaskList : LocalVoiceCommand
}
