package com.gdjl.glasses.utils

import android.graphics.*
import android.util.Log
import java.text.SimpleDateFormat
import java.util.*

class WatermarkHelper {

    companion object {
        private const val TAG = "WatermarkHelper"
    }

    fun addWatermark(
        bitmap: Bitmap,
        hazards: List<HazardModelHelper.HazardResult>,
        location: String = ""
    ): Bitmap {
        val resultBitmap = bitmap.copy(Bitmap.Config.ARGB_8888, true)
        val canvas = Canvas(resultBitmap)
        val paint = Paint().apply {
            isAntiAlias = true
            style = Paint.Style.FILL_AND_STROKE
        }

        val width = bitmap.width
        val height = bitmap.height

        // 半透明背景
        val bgPaint = Paint().apply {
            color = Color.argb(200, 0, 0, 0)
            style = Paint.Style.FILL
        }

        // ---- 顶部标题栏 ----
        val topHeight = (height * 0.12f).toInt()
        canvas.drawRect(0f, 0f, width.toFloat(), topHeight.toFloat(), bgPaint)

        paint.apply {
            color = Color.parseColor("#FF6B6B")
            textSize = (width * 0.045f)
            typeface = Typeface.create(Typeface.DEFAULT_BOLD, Typeface.BOLD)
        }
        canvas.drawText("🔴 安全巡视记录", (width * 0.04f), (topHeight * 0.55f), paint)

        paint.apply {
            color = Color.WHITE
            textSize = (width * 0.028f)
            typeface = Typeface.DEFAULT
        }
        val timeStr = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date())
        canvas.drawText("📅 $timeStr", (width * 0.04f), (topHeight * 0.85f), paint)

        // ---- 底部隐患信息 ----
        val bottomHeight = (height * 0.22f).toInt()
        val bottomStartY = height - bottomHeight
        canvas.drawRect(0f, bottomStartY.toFloat(), width.toFloat(), height.toFloat(), bgPaint)

        var yOffset = bottomStartY + (bottomHeight * 0.12f)

        paint.apply {
            color = Color.parseColor("#FFD93D")
            textSize = (width * 0.038f)
            typeface = Typeface.create(Typeface.DEFAULT_BOLD, Typeface.BOLD)
        }
        canvas.drawText("⚠️ 发现 ${hazards.size} 处隐患", (width * 0.04f), yOffset, paint)

        yOffset += (bottomHeight * 0.2f)

        paint.apply {
            color = Color.WHITE
            textSize = (width * 0.028f)
            typeface = Typeface.DEFAULT
        }

        hazards.forEach { hazard ->
            val icon = when (hazard.level) {
                "重大" -> "🔴"
                "高" -> "🟠"
                "中" -> "🟡"
                else -> "🟢"
            }
            val text = "$icon ${hazard.name} (${String.format("%.0f", hazard.confidence * 100)}%)"
            canvas.drawText(text, (width * 0.04f), yOffset, paint)
            yOffset += (bottomHeight * 0.18f)
        }

        // 位置信息
        if (location.isNotEmpty()) {
            paint.apply {
                color = Color.parseColor("#74B9FF")
                textSize = (width * 0.028f)
            }
            canvas.drawText("📍 $location", (width * 0.04f), (height * 0.96f), paint)
        }

        // 底部角落水印
        paint.apply {
            color = Color.argb(80, 255, 255, 255)
            textSize = (width * 0.03f)
            typeface = Typeface.DEFAULT_BOLD
        }
        canvas.drawText("安全巡视", (width * 0.85f), (height * 0.96f), paint)

        Log.d(TAG, "✅ 水印添加完成")
        return resultBitmap
    }
}
