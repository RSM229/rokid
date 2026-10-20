// 全程语音：主菜单语音导航 + 触控板兼容
package com.gdjl.glasses.ui.menu

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import com.gdjl.glasses.R
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.gdjl.glasses.ui.hazard.HazardSubListActivity
import com.gdjl.glasses.ui.workrecord.WorkRecordSessionActivity
import com.gdjl.glasses.voice.VoiceCommandParser
import com.gdjl.glasses.voice.LocalVoiceCommand

class MenuActivity : BaseBusinessActivity() {

    companion object {
        private const val TAG = "MenuActivity"
    }

    private var tvWorkRecordStatus: TextView? = null
    private var tvHazardStatus: TextView? = null
    private var busy = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_menu)

        tvWorkRecordStatus = findViewById<LinearLayout>(R.id.btn_work_record)?.findViewById<TextView>(R.id.tv_menu_subtitle)
        tvHazardStatus = findViewById<LinearLayout>(R.id.btn_hazard)?.findViewById<TextView>(R.id.tv_menu_subtitle)

        // 触控板/按键导航（兼容保留）
        findViewById<View>(R.id.btn_work_record).setOnClickListener {
            enterWorkRecord()
        }
        findViewById<View>(R.id.btn_hazard).setOnClickListener {
            enterHazard()
        }

        // 全程语音：进入自动播报提示 + 持续监听（基类统一接管 onResume/onPause）
        enablePageVoice()
    }

    override fun onResume() {
        super.onResume()
        busy = false
        tvWorkRecordStatus?.text = "语音：说「工作记录」"
        tvHazardStatus?.text = "语音：说「安全隐患」"
    }

    override fun voiceHint(): String =
        "请选择功能：工作记录，或安全隐患。说开始巡视，可直接开始工作"

    override fun onPageVoiceCommand(text: String): Boolean {
        if (busy) return true
        Log.i(TAG, "菜单语音指令: $text")

        return when {
            // 直接口述开始巡视/验收/旁站等 -> 直接进入工作记录采集模式
            VoiceCommandParser.parse(text) is LocalVoiceCommand.SelectWorkType -> {
                busy = true
                speak("开始$text")
                startActivity(Intent(this, WorkRecordSessionActivity::class.java))
                true
            }
            text.contains("开始工作") || text.contains("工作记录") || text == "工作" -> {
                busy = true
                enterWorkRecord()
                true
            }
            text.contains("安全隐患") || text.contains("隐患") || text.contains("派单") -> {
                busy = true
                enterHazard()
                true
            }
            else -> false
        }
    }

    private fun enterWorkRecord() {
        speak("进入工作记录")
        startActivity(Intent(this, WorkRecordSessionActivity::class.java))
    }

    private fun enterHazard() {
        speak("进入安全隐患")
        startActivity(Intent(this, HazardSubListActivity::class.java))
    }
}
