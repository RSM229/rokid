package com.gdjl.glasses.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VoiceCommandParserTest {
    @Test
    fun parsesInspectionAndPhotoCommands() {
        assertEquals(
            LocalVoiceCommand.StartInspection,
            VoiceCommandParser.parse("请启动安全AI巡检"),
        )
        assertEquals(
            LocalVoiceCommand.StopInspection,
            VoiceCommandParser.parse("结束巡检"),
        )
        assertEquals(LocalVoiceCommand.TakePhoto, VoiceCommandParser.parse("现在拍照"))
        assertNull(VoiceCommandParser.parse("先不拍照"))
        assertNull(VoiceCommandParser.parse("暂时不需要拍照"))
        assertEquals(
            LocalVoiceCommand.StartInspection,
            VoiceCommandParser.parse("启动巡检后查询规范脚手架"),
        )
    }

    @Test
    fun extractsAcceptanceAndRegulationKeywords() {
        assertEquals(
            LocalVoiceCommand.AcceptanceGuide("钢筋工程"),
            VoiceCommandParser.parse("怎么验收钢筋工程"),
        )
        assertEquals(
            LocalVoiceCommand.QueryRegulation("脚手架"),
            VoiceCommandParser.parse("查询规范脚手架"),
        )
    }

    @Test
    fun parsesDocumentAndClosureCommands() {
        assertEquals(
            LocalVoiceCommand.GenerateDocument("daily_log"),
            VoiceCommandParser.parse("生成监理日志"),
        )
        assertEquals(
            LocalVoiceCommand.CreateRectification,
            VoiceCommandParser.parse("开具整改单"),
        )
        assertEquals(
            LocalVoiceCommand.Recheck(pass = true),
            VoiceCommandParser.parse("复查合格"),
        )
        assertEquals(
            LocalVoiceCommand.Recheck(pass = false),
            VoiceCommandParser.parse("整改不合格"),
        )
    }

    @Test
    fun fallsBackForUnknownOrEmptyCommands() {
        assertEquals(LocalVoiceCommand.TaskList, VoiceCommandParser.parse("查看任务"))
        assertNull(VoiceCommandParser.parse("帮我看看今天现场怎么样"))
        assertNull(VoiceCommandParser.parse("   "))
    }
}
