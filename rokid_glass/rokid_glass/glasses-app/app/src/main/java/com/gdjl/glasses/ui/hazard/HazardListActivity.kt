// 创建日期: 2026-07-18
package com.gdjl.glasses.ui.hazard

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.gdjl.glasses.R
import com.gdjl.glasses.network.ApiService
import com.gdjl.glasses.network.models.ApiResult
import com.gdjl.glasses.network.models.Hazard
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.gdjl.glasses.voice.CnIndexParser
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 隐患列表页
 *
 * 按状态筛选（已提报/整改中/待复查/已闭环），点击进入详情，底部入口进入拍照识别。
 */
class HazardListActivity : BaseBusinessActivity() {

    private var currentStatus: String? = null
    private lateinit var listContainer: LinearLayout
    private lateinit var emptyView: TextView
    private var currentItems: List<Hazard> = emptyList()

    private val tabs = listOf(
        "全部" to null,
        "已提报" to "reported",
        "整改中" to "rectifying",
        "待复查" to "recheck",
        "已闭环" to "closed",
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_hazard_list)
        setupToolbar(R.id.tv_title, R.id.btn_back, getString(R.string.hazard_title))
        bindLoading(R.id.pb_loading)

        listContainer = findViewById(R.id.list_container)
        emptyView = findViewById(R.id.tv_empty)

        val tabContainer = findViewById<LinearLayout>(R.id.tab_container)
        tabs.forEach { (label, status) ->
            val btn = layoutInflater.inflate(R.layout.item_tab, tabContainer, false) as MaterialButton
            btn.text = label
            btn.setOnClickListener {
                currentStatus = status
                loadList()
            }
            tabContainer.addView(btn)
        }

        findViewById<MaterialButton>(R.id.btn_detect).setOnClickListener {
            startActivity(Intent(this, HazardDetectActivity::class.java))
        }

        enablePageVoice()
        loadList()
    }

    // ---------------- 全程语音 ----------------

    override fun voiceHint(): String =
        "说状态名切换列表：全部、已提报、整改中、待复查、已闭环；说第几个查看详情；说开始识别进行拍照识别"

    override fun onPageVoiceCommand(text: String): Boolean {
        // 状态筛选：说「已提报」「整改中」等直接切 tab
        tabs.firstOrNull { (label, _) -> label != "全部" && text.contains(label) }?.let { (label, status) ->
            currentStatus = status
            speak("查看$label")
            loadList()
            return true
        }
        if (text.contains("全部")) {
            currentStatus = null
            speak("查看全部")
            loadList()
            return true
        }
        // 「第 N 个」打开详情
        if (text.contains("第") || text.contains("打开") || text.contains("查看")) {
            val index = CnIndexParser.parse(text)
            if (index != null) {
                val item = currentItems.getOrNull(index)
                if (item != null) {
                    speak("打开第${index + 1}个")
                    openDetail(item)
                } else {
                    speak("没有第${index + 1}个，当前共${currentItems.size}条")
                }
                return true
            }
        }
        if (text.contains("识别") || text.contains("拍照")) {
            speak("进入隐患识别")
            startActivity(Intent(this, HazardDetectActivity::class.java))
            return true
        }
        if (text.contains("刷新")) {
            speak("刷新列表")
            loadList()
            return true
        }
        return false
    }

    private fun openDetail(hazard: Hazard) {
        startActivity(
            Intent(this, HazardDetailActivity::class.java)
                .putExtra(HazardDetailActivity.EXTRA_HAZARD_ID, hazard.id)
        )
    }

    private fun loadList() {
        setLoading(true)
        emptyView.visibility = View.GONE
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { ApiService.listHazards(currentStatus) }
            setLoading(false)
            when (result) {
                is ApiResult.Success -> renderList(result.data)
                else -> {
                    toastAndSpeak("加载失败：${result.errorMessage()}")
                    renderList(emptyList())
                }
            }
        }
    }

    private fun renderList(items: List<Hazard>) {
        currentItems = items
        listContainer.removeAllViews()
        if (items.isEmpty()) {
            emptyView.visibility = View.VISIBLE
            return
        }
        items.forEach { hazard ->
            val view = layoutInflater.inflate(R.layout.item_text_row, listContainer, false)
            view.findViewById<TextView>(R.id.tv_primary).text =
                "${hazard.riskCategory ?: "隐患"} · ${hazard.topRisk ?: "未识别"}"
            view.findViewById<TextView>(R.id.tv_secondary).text =
                "状态：${statusLabel(hazard.status)}    ${hazard.createdAt ?: ""}"
            view.findViewById<TextView>(R.id.tv_extra).text = "部位：${hazard.section ?: "-"}"
            view.setOnClickListener { openDetail(hazard) }
            listContainer.addView(view)
        }
    }

    private fun statusLabel(status: String?): String = when (status) {
        "reported" -> "已提报"
        "notified" -> "已通知整改"
        "rectifying" -> "整改中"
        "recheck" -> "待复查"
        "closed" -> "已闭环"
        "rejected" -> "驳回整改"
        "已提报", "已通知整改", "整改中", "待复查", "已闭环", "驳回整改" -> status
        null -> "已提报"
        else -> status
    }

    override fun onResume() {
        super.onResume()
        if (::listContainer.isInitialized) loadList()
    }
}
