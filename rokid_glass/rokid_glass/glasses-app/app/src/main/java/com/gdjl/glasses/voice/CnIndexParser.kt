package com.gdjl.glasses.voice

/**
 * 中文/数字序号解析：从语音文本中提取「第 N 个 / 第 N 条 / N 号 / 打开 N」的 0 基索引。
 *
 * 支持：第一个、第1个、第三条、3号、打开第五个、选2
 * 不匹配返回 null。
 */
object CnIndexParser {

    private val CN_DIGITS = mapOf(
        '零' to 0, '一' to 1, '二' to 2, '两' to 2, '三' to 3, '四' to 4,
        '五' to 5, '六' to 6, '七' to 7, '八' to 8, '九' to 9,
    )

    /** 解析文本中的序号，返回 0 基索引；没有则 null */
    fun parse(text: String): Int? {
        // 阿拉伯数字：第3个 / 3号 / 打开3 / 选3
        Regex("""第\s*(\d{1,2})\s*[个条项号]?""").find(text)?.let { m ->
            return m.groupValues[1].toIntOrNull().toZeroBased()
        }
        Regex("""(\d{1,2})\s*号""").find(text)?.let { m ->
            return m.groupValues[1].toIntOrNull().toZeroBased()
        }
        Regex("""(?:打开|查看|选|播放)\s*(\d{1,2})""").find(text)?.let { m ->
            return m.groupValues[1].toIntOrNull().toZeroBased()
        }
        // 中文数字：第五个 / 第三条 / 打开第二个
        Regex("""第\s*([零一二两三四五六七八九十]{1,3})\s*[个条项号]?""").find(text)?.let { m ->
            return cnToInt(m.groupValues[1]).toZeroBased()
        }
        Regex("""(?:打开|查看|选|播放)\s*([零一二两三四五六七八九十]{1,3})""").find(text)?.let { m ->
            return cnToInt(m.groupValues[1]).toZeroBased()
        }
        return null
    }

    /** 1 基序号转 0 基索引；null 或 <=0 返回 null */
    private fun Int?.toZeroBased(): Int? = this?.takeIf { it > 0 }?.minus(1)

    /** 中文数字转整数，支持 1~99（一、十、十一、二十、二十一、九十九） */
    private fun cnToInt(s: String): Int? {
        if (s.isEmpty()) return null
        if (!s.contains('十')) {
            if (s.length == 1) return CN_DIGITS[s[0]]
            return null
        }
        val parts = s.split('十')
        val tens = if (parts[0].isEmpty()) 1
            else parts[0].singleOrNull()?.let { CN_DIGITS[it] } ?: return null
        val ones = if (parts.size > 1 && parts[1].isNotEmpty())
            parts[1].singleOrNull()?.let { CN_DIGITS[it] } ?: return null
            else 0
        return tens * 10 + ones
    }
}
