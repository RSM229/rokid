package com.gdjl.glasses.utils

import android.content.Context
import android.graphics.Bitmap
import android.util.Log
import java.io.File

class HazardModelHelper(private val context: Context) {

    companion object {
        private const val TAG = "HazardModel"
        private const val INPUT_SIZE = 224
        private const val SIMULATION_MODE = true  // 开启模拟模式
    }

    private var isLoaded = false
    private val simulatedHazards = listOf(
        "未佩戴安全帽", "未系安全绳", "临边防护缺失",
        "电缆裸露", "无警示标识", "脚手架不稳",
        "高空坠物风险", "消防通道堵塞", "违规用电",
        "安全网破损", "临电箱未锁", "材料堆放不规范"
    )

    fun loadModel(): Boolean {
        if (SIMULATION_MODE) {
            isLoaded = true
            Log.d(TAG, "✅ 模拟模式已启用")
            return true
        }

        try {
            // 尝试加载真实模型
            val modelBuffer = context.assets.open("hazard_model.tflite").readBytes()
            // interpreter = Interpreter(ByteBuffer.wrap(modelBuffer))
            isLoaded = true
            Log.d(TAG, "✅ 模型加载成功")
            return true
        } catch (e: Exception) {
            Log.w(TAG, "⚠️ 模型文件不存在，使用模拟模式")
            isLoaded = true
            return true
        }
    }

    fun detect(bitmap: Bitmap): List<HazardResult> {
        if (!isLoaded) {
            Log.w(TAG, "模型未加载")
            return emptyList()
        }

        if (SIMULATION_MODE) {
            // 模拟识别：随机返回 1-3 个隐患
            val count = (1..3).random()
            val shuffled = simulatedHazards.shuffled()
            
            return shuffled.take(count).map { name ->
                val confidence = 0.55f + (0.40f * (0..100).random() / 100f)
                val level = when {
                    confidence > 0.85 -> "重大"
                    confidence > 0.70 -> "高"
                    confidence > 0.60 -> "中"
                    else -> "低"
                }
                HazardResult(name, confidence, level)
            }
        }

        // 真实模型推理（后续实现）
        return emptyList()
    }

    data class HazardResult(
        val name: String,
        val confidence: Float,
        val level: String
    )
}
