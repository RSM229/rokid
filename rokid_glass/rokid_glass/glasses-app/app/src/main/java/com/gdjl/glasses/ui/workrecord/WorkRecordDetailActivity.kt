// 创建日期: 2026-07-18
package com.gdjl.glasses.ui.workrecord

import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.gdjl.glasses.R
import com.gdjl.glasses.network.ApiService
import com.gdjl.glasses.network.AuthManager
import com.gdjl.glasses.network.models.ApiResult
import com.gdjl.glasses.network.models.StatusActionRequest
import com.gdjl.glasses.network.models.WorkRecord
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 工作记录详情页
 *
 * 展示记录字段，根据当前用户角色与记录状态显示操作按钮：
 * - 草稿：提交审批
 * - 待审批（chief/admin）：审批通过 / 驳回
 */
class WorkRecordDetailActivity : BaseBusinessActivity() {

    private lateinit var tvType: TextView
    private lateinit var tvSection: TextView
    private lateinit var tvStatus: TextView
    private lateinit var tvCreated: TextView
    private lateinit var tvContent: TextView
    private lateinit var btnSubmit: MaterialButton
    private lateinit var btnApprove: MaterialButton
    private lateinit var btnReject: MaterialButton

    private var record: WorkRecord? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_work_record_detail)
        setupToolbar(R.id.tv_title, R.id.btn_back, getString(R.string.work_record_detail))
        bindLoading(R.id.pb_loading)

        tvType = findViewById(R.id.tv_type)
        tvSection = findViewById(R.id.tv_section)
        tvStatus = findViewById(R.id.tv_status)
        tvCreated = findViewById(R.id.tv_created)
        tvContent = findViewById(R.id.tv_content)
        btnSubmit = findViewById(R.id.btn_submit)
        btnApprove = findViewById(R.id.btn_approve)
        btnReject = findViewById(R.id.btn_reject)

        val recordId = intent.getStringExtra(EXTRA_RECORD_ID)
        if (recordId.isNullOrBlank()) {
            toastAndSpeak("参数缺失")
            finish()
            return
        }

        btnSubmit.setOnClickListener { act("submit") }
        btnApprove.setOnClickListener { act("approve") }
        btnReject.setOnClickListener { act("reject") }

        enablePageVoice()
        load(recordId)
    }

    // ---------------- 全程语音 ----------------

    override fun voiceHint(): String =
        "说：朗读、提交审批、审批通过、驳回、返回"

    override fun onPageVoiceCommand(text: String): Boolean {
        val r = record
        return when {
            text.contains("朗读") || text.contains("播报") -> {
                if (r != null) {
                    speak(
                        "${typeLabel(r.type)}记录。部位：${r.section ?: "未填写"}。" +
                            "状态：${r.status ?: "草稿"}。正文：${r.content ?: "无"}"
                    )
                } else {
                    speak("记录尚未加载完成")
                }
                true
            }
            text.contains("提交") -> {
                if (btnSubmit.visibility == View.VISIBLE) act("submit")
                else speak("当前状态不可提交")
                true
            }
            text.contains("通过") || text.contains("同意") -> {
                if (btnApprove.visibility == View.VISIBLE) act("approve")
                else speak("当前状态不可审批")
                true
            }
            text.contains("驳回") || text.contains("拒绝") -> {
                if (btnReject.visibility == View.VISIBLE) act("reject")
                else speak("当前状态不可驳回")
                true
            }
            else -> false
        }
    }

    private fun load(recordId: String) {
        setLoading(true)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { ApiService.getWorkRecord(recordId) }
            setLoading(false)
            when (result) {
                is ApiResult.Success -> render(result.data)
                else -> toastAndSpeak("加载失败：${result.errorMessage()}")
            }
        }
    }

    private fun render(r: WorkRecord) {
        record = r
        tvType.text = "类型：${typeLabel(r.type)}"
        tvSection.text = "部位：${r.section ?: "—"}"
        tvStatus.text = "状态：${r.status ?: "草稿"}"
        tvCreated.text = "创建：${r.createdAt ?: "—"}"
        tvContent.text = r.content ?: "（无正文）"

        val status = r.status ?: "draft"
        val role = AuthManager.getRole()
        btnSubmit.visibility =
            if (status in setOf("draft", "草稿", "进行中", "已驳回")) View.VISIBLE else View.GONE
        // 仅 chief/admin 可审批
        val canApprove =
            status in setOf("pending", "已提交") &&
                role in setOf("总监理工程师", "总监代表")
        btnApprove.visibility = if (canApprove) View.VISIBLE else View.GONE
        btnReject.visibility = if (canApprove) View.VISIBLE else View.GONE
    }

    private fun act(action: String) {
        val r = record ?: return
        setLoading(true)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                ApiService.actWorkRecord(r.id, StatusActionRequest(action = action))
            }
            setLoading(false)
            when (result) {
                is ApiResult.Success -> {
                    val msg = when (action) {
                        "submit" -> "已提交审批"
                        "approve" -> "已审批通过"
                        "reject" -> "已驳回"
                        else -> "操作完成"
                    }
                    toastAndSpeak(msg)
                    render(result.data)
                }
                else -> toastAndSpeak("操作失败：${result.errorMessage()}")
            }
        }
    }

    private fun typeLabel(type: String): String = when (type) {
        "inspection" -> "巡视"
        "acceptance" -> "验收"
        "station" -> "旁站"
        "witness" -> "见证"
        "巡视", "验收", "旁站", "见证" -> type
        else -> type
    }

    companion object {
        const val EXTRA_RECORD_ID = "record_id"
    }
}
