// 全程语音：隐患子菜单语音导航 + 触控板兼容
package com.gdjl.glasses.ui.hazard

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import com.gdjl.glasses.R
import com.gdjl.glasses.ui.BaseBusinessActivity

class HazardSubListActivity : BaseBusinessActivity() {

    companion object {
        private const val TAG = "HazardSubList"
    }

    private var busy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_hazard_sub_list)
        Log.d(TAG, "onCreate: 页面已创建")

        // 安全隐患识别 - 直接绑定点击
        findViewById<View>(R.id.btn_hazard_detect).setOnClickListener {
            enterDetect()
        }

        // 派单管理 - 直接绑定点击
        findViewById<View>(R.id.btn_dispatch).setOnClickListener {
            enterDispatch()
        }

        enablePageVoice()
        Log.d(TAG, "onCreate: 完成")
    }

    override fun onResume() {
        super.onResume()
        busy = false
    }

    override fun voiceHint(): String =
        "请选择：隐患识别，派单管理，或隐患列表。说返回可回主菜单"

    override fun onPageVoiceCommand(text: String): Boolean {
        if (busy) return true
        Log.i(TAG, "子菜单语音指令: $text")
        return when {
            text.contains("识别") || text.contains("拍照") -> {
                busy = true
                enterDetect()
                true
            }
            text.contains("派单") -> {
                busy = true
                enterDispatch()
                true
            }
            text.contains("列表") || text.contains("记录") || text.contains("查看隐患") -> {
                busy = true
                speak("进入隐患列表")
                startActivitySafely(HazardListActivity::class.java)
                true
            }
            else -> false
        }
    }

    private fun enterDetect() {
        speak("进入隐患识别")
        startActivitySafely(HazardDetectActivity::class.java)
    }

    private fun enterDispatch() {
        speak("进入派单管理")
        startActivitySafely(DispatchActivity::class.java)
    }

    private fun startActivitySafely(cls: Class<*>) {
        try {
            startActivity(Intent(this, cls))
        } catch (e: Exception) {
            Log.e(TAG, "启动失败: ${cls.simpleName}", e)
        }
    }
}
