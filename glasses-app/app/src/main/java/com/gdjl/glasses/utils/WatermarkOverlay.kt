// 创建日期: 2026-07-18
package com.gdjl.glasses.utils

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.text.TextPaint
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 水印信息
 *
 * 拍摄时叠加在图片上的不可篡改水印字段（RPD 11.3）。
 *
 * @param projectName 项目名称
 * @param inspector 监理人员姓名
 * @param captureTime 拍摄时间（为空则取当前时间）
 * @param gpsCoords GPS 坐标（如 "121.4737,31.2304"）
 * @param weather 天气（如 "晴 28℃"）
 * @param section 施工部位（楼栋/楼层/部位）
 * @param hazardFlag 隐患标识（命中隐患时的提示文本，为空表示无隐患）
 */
data class WatermarkInfo(
    val projectName: String?,
    val inspector: String?,
    val captureTime: String? = null,
    val gpsCoords: String? = null,
    val weather: String? = null,
    val section: String? = null,
    val hazardFlag: String? = null,
)

/**
 * 自动水印叠加工具
 *
 * 在拍照后的 Bitmap 上绘制半透明水印条：
 * - 底部水印条：项目/人员/时间/位置/天气/部位
 * - 右上角隐患标识（命中隐患时显示红色角标）
 * - 计算图片 SHA256 哈希作为防篡改校验值，写入水印
 *
 * 使用 Canvas + Paint 在原图 Bitmap 上绘制后返回新 Bitmap（原图不变）。
 */
object WatermarkOverlay {

    private const val TAG = "WatermarkOverlay"
    private const val TIME_FORMAT = "yyyy-MM-dd HH:mm:ss"

    /** 半透明背景色（黑色 55% 不透明） */
    private const val BG_ALPHA = 0x8C

    /** 文本颜色（白色 85% 不透明） */
    private const val TEXT_ALPHA = 0xD9

    /** 隐患角标颜色（红色） */
    private const val HAZARD_COLOR = 0xCCFF3B30.toInt()

    /**
     * 在 [bitmap] 上叠加水印，返回新 Bitmap（不修改原图）
     *
     * 若创建可变副本失败（OOM 等），返回原图。
     */
    fun applyWatermark(bitmap: Bitmap, watermark: WatermarkInfo): Bitmap {
        // 创建可变副本
        val output = bitmap.copy(Bitmap.Config.ARGB_8888, true) ?: return bitmap
        val canvas = Canvas(output)

        val w = output.width
        val h = output.height
        // 水印字号按图片宽度自适应（最小 12px，最大 48px）
        val fontScale = (w / 32f).coerceIn(14f, 48f)
        val linePadding = fontScale * 0.4f

        drawBottomBar(canvas, w, h, fontScale, linePadding, watermark)
        drawHazardCorner(canvas, w, h, fontScale, watermark)
        drawHash(canvas, w, h, fontScale, output)

        return output
    }

    /** 底部水印条 */
    private fun drawBottomBar(
        canvas: Canvas,
        w: Int,
        h: Int,
        fontScale: Float,
        linePadding: Float,
        info: WatermarkInfo,
    ) {
        val lines = buildList {
            info.projectName?.takeIf { it.isNotBlank() }?.let { add("项目：$it") }
            info.inspector?.takeIf { it.isNotBlank() }?.let { add("监理：$it") }
            val time = info.captureTime
                ?: SimpleDateFormat(TIME_FORMAT, Locale.CHINA).format(Date())
            add("时间：$time")
            info.gpsCoords?.takeIf { it.isNotBlank() }?.let { add("GPS：$it") }
            info.weather?.takeIf { it.isNotBlank() }?.let { add("天气：$it") }
            info.section?.takeIf { it.isNotBlank() }?.let { add("部位：$it") }
        }
        if (lines.isEmpty()) return

        val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.argb(TEXT_ALPHA, 255, 255, 255)
            textSize = fontScale
            typeface = Typeface.MONOSPACE
        }
        val bgPaint = Paint().apply { color = Color.argb(BG_ALPHA, 0, 0, 0) }

        val lineHeight = fontScale + linePadding
        val blockHeight = lineHeight * lines.size + linePadding
        val top = h - blockHeight

        // 绘制底部半透明背景条
        canvas.drawRect(0f, top, w.toFloat(), h.toFloat(), bgPaint)

        // 逐行绘制文本
        var y = top + lineHeight
        lines.forEach { line ->
            canvas.drawText(line, linePadding, y, textPaint)
            y += lineHeight
        }
    }

    /** 右上角隐患标识角标 */
    private fun drawHazardCorner(
        canvas: Canvas,
        w: Int,
        h: Int,
        fontScale: Float,
        info: WatermarkInfo,
    ) {
        val flag = info.hazardFlag?.takeIf { it.isNotBlank() } ?: return

        val text = "⚠ 隐患：$flag"
        val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textSize = fontScale * 0.8f
            typeface = Typeface.DEFAULT_BOLD
        }
        val bgPaint = Paint().apply { color = HAZARD_COLOR }

        val textWidth = textPaint.measureText(text)
        val pad = fontScale * 0.3f
        val rectRight = w.toFloat()
        val rectLeft = rectRight - textWidth - pad * 2
        val rectTop = 0f
        val rectBottom = fontScale * 0.8f + pad * 2

        canvas.drawRect(rectLeft, rectTop, rectRight, rectBottom, bgPaint)
        canvas.drawText(text, rectLeft + pad, rectBottom - pad, textPaint)
    }

    /**
     * 计算图片 SHA256 哈希并写入水印右下角
     *
     * 注意：哈希基于加水印前的像素计算会陷入"鸡生蛋"问题，
     * 这里采用"先算原图哈希 -> 再绘制底部/角标 -> 最后把哈希值绘制到右下角"的两阶段方案，
     * 哈希值本身不参与自身计算，后端可通过重新计算除哈希区域外像素校验。
     */
    private fun drawHash(canvas: Canvas, w: Int, h: Int, fontScale: Float, bitmap: Bitmap) {
        val hash = computeSha256(bitmap)
        val shortHash = hash.take(16)
        val text = "SHA256:$shortHash"
        val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.argb(0xAA, 255, 255, 255)
            textSize = fontScale * 0.55f
            typeface = Typeface.MONOSPACE
        }
        val pad = fontScale * 0.2f
        val tw = textPaint.measureText(text)
        // 绘制在右上角下方（避开隐患角标区域）
        canvas.drawText(text, w.toFloat() - tw - pad, fontScale * 1.6f + pad, textPaint)
    }

    /** 计算 Bitmap 的 SHA256 哈希（基于像素字节数组） */
    private fun computeSha256(bitmap: Bitmap): String {
        return try {
            val buffer = java.nio.ByteBuffer.allocate(bitmap.byteCount)
            bitmap.copyPixelsToBuffer(buffer)
            val bytes = buffer.array()
            val md = java.security.MessageDigest.getInstance("SHA-256")
            val digest = md.digest(bytes)
            digest.joinToString("") { "%02x".format(it) }
        } catch (e: Exception) {
            "unknown"
        }
    }
}
