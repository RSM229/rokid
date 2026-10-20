// 创建日期: 2026-07-18
package com.gdjl.glasses.ui.hazard

import android.os.Bundle
import android.view.View
import android.widget.TextView
import androidx.lifecycle.lifecycleScope
import com.gdjl.glasses.R
import com.gdjl.glasses.camera.CameraManager
import com.gdjl.glasses.camera.ImageUploader
import com.gdjl.glasses.network.ApiService
import com.gdjl.glasses.network.models.ApiResult
import com.gdjl.glasses.network.models.Hazard
import com.gdjl.glasses.network.models.HazardRecheckRequest
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 隐患详情页
 *
 * 展示隐患描述/位置/影像/匹配规范条款/整改建议，
 * 提供：
 * - 一键开具整改通知单（create-rectification-doc）
 * - 上传整改影像（拍照 + ImageUploader）
 * - 复查合格 / 驳回整改（语音确认）
 */
class HazardDetailActivity : BaseBusinessActivity() {

    private lateinit var tvRiskStatus: TextView
    private lateinit var tvCategory: TextView
    private lateinit var tvTopRisk: TextView
    private lateinit var tvSection: TextView
    private lateinit var tvEvidence: TextView
    private lateinit var tvStatus: TextView
    private lateinit var btnRectDoc: MaterialButton
    private lateinit var btnUploadImage: MaterialButton
    private lateinit var btnRecheckPass: MaterialButton
    private lateinit var btnRecheckReject: MaterialButton

    private var hazard: Hazard? = null
    private lateinit var cameraManager: CameraManager
    private lateinit var imageUploader: ImageUploader

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_hazard_detail)
        setupToolbar(R.id.tv_title, R.id.btn_back, getString(R.string.hazard_detail))
        bindLoading(R.id.pb_loading)

        cameraManager = CameraManager(this, this)
        cameraManager.bind(null)
        imageUploader = ImageUploader(this)

        tvRiskStatus = findViewById(R.id.tv_risk_status)
        tvCategory = findViewById(R.id.tv_category)
        tvTopRisk = findViewById(R.id.tv_top_risk)
        tvSection = findViewById(R.id.tv_section)
        tvEvidence = findViewById(R.id.tv_evidence)
        tvStatus = findViewById(R.id.tv_status)
        btnRectDoc = findViewById(R.id.btn_rect_doc)
        btnUploadImage = findViewById(R.id.btn_upload_image)
        btnRecheckPass = findViewById(R.id.btn_recheck_pass)
        btnRecheckReject = findViewById(R.id.btn_recheck_reject)

        val hazardId = intent.getStringExtra(EXTRA_HAZARD_ID)
        if (hazardId.isNullOrBlank()) {
            toastAndSpeak("参数缺失")
            finish()
            return
        }

        btnRectDoc.setOnClickListener { createRectificationDoc() }
        btnUploadImage.setOnClickListener { uploadRectifyImage() }
        btnRecheckPass.setOnClickListener {
            startVoiceInput(
                onResult = { text ->
                    if (text.contains("合格") || text.contains("确认")) recheck(pass = true)
                    else toastAndSpeak("请说出“合格”以确认")
                },
                onError = { recheck(pass = true) }
            )
        }
        btnRecheckReject.setOnClickListener { recheck(pass = false) }

        enablePageVoice()
        load(hazardId)
    }

    // ---------------- 全程语音 ----------------

    override fun voiceHint(): String =
        "说：朗读详情、开具整改单、上传影像、复查合格、驳回整改、返回"

    override fun onPageVoiceCommand(text: String): Boolean {
        val h = hazard
        return when {
            text.contains("朗读") || text.contains("播报") -> {
                if (h != null) {
                    speak(
                        "隐患详情。风险类别：${h.riskCategory ?: "未知"}。" +
                            "主要风险：${h.topRisk ?: "未识别"}。" +
                            "部位：${h.section ?: "未知"}。" +
                            "当前状态：${statusLabel(h.status)}"
                    )
                } else {
                    speak("详情尚未加载完成")
                }
                true
            }
            text.contains("整改单") || text.contains("开具") || text.contains("开单") -> {
                if (btnRectDoc.visibility == View.VISIBLE) createRectificationDoc()
                else speak("当前状态不可开具整改单")
                true
            }
            text.contains("上传") || text.contains("影像") || text.contains("拍照") -> {
                if (btnUploadImage.visibility == View.VISIBLE) uploadRectifyImage()
                else speak("当前状态无需上传整改影像")
                true
            }
            text.contains("复查合格") || text.contains("复查通过") -> {
                if (btnRecheckPass.visibility == View.VISIBLE) recheck(pass = true)
                else speak("当前状态不可复查")
                true
            }
            text.contains("驳回") -> {
                if (btnRecheckReject.visibility == View.VISIBLE) recheck(pass = false)
                else speak("当前状态不可驳回")
                true
            }
            else -> false
        }
    }

    private fun load(hazardId: String) {
        setLoading(true)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { ApiService.getHazard(hazardId) }
            setLoading(false)
            when (result) {
                is ApiResult.Success -> render(result.data)
                else -> toastAndSpeak("加载失败：${result.errorMessage()}")
            }
        }
    }

    private fun render(h: Hazard) {
        hazard = h
        tvRiskStatus.text = "风险状态：${h.riskStatus ?: "-"}"
        tvCategory.text = "风险类别：${h.riskCategory ?: "-"}"
        tvTopRisk.text = "主要风险：${h.topRisk ?: "-"}"
        tvSection.text = "部位：${h.section ?: "-"}"
        tvStatus.text = "状态：${statusLabel(h.status)}"
        val ev = h.evidence?.joinToString("\n- ") { it } ?: ""
        tvEvidence.text = if (ev.isBlank()) "证据：无" else "证据：\n- $ev"

        val status = h.status
        // 已提报状态可开具整改通知单
        btnRectDoc.visibility =
            if (status in setOf("reported", "已提报")) View.VISIBLE else View.GONE
        // 整改中状态可上传整改影像
        btnUploadImage.visibility =
            if (status in setOf("notified", "rectifying", "已通知整改", "整改中")) {
                View.VISIBLE
            } else {
                View.GONE
            }
        // 待复查状态可复查
        btnRecheckPass.visibility =
            if (status in setOf("recheck", "待复查")) View.VISIBLE else View.GONE
        btnRecheckReject.visibility = btnRecheckPass.visibility
    }

    /** 一键开具整改通知单 */
    private fun createRectificationDoc() {
        val h = hazard ?: return
        setLoading(true)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { ApiService.createRectificationDoc(h.id) }
            setLoading(false)
            when (result) {
                is ApiResult.Success -> {
                    val doc = result.data
                    toastAndSpeak("整改通知单已生成：${doc.docId}")
                    // 刷新详情
                    load(h.id)
                }
                else -> toastAndSpeak("开具失败：${result.errorMessage()}")
            }
        }
    }

    /** 上传整改影像 */
    private fun uploadRectifyImage() {
        val h = hazard ?: return
        cameraManager.takePhoto(
            onSuccess = { file ->
                setLoading(true)
                imageUploader.uploadImage(
                    file = file,
                    section = h.section,
                    callback = object : ImageUploader.Callback {
                        override fun onSuccess(record: com.gdjl.glasses.network.models.ImageRecord) {
                            // 绑定至隐患
                            lifecycleScope.launch {
                                val result = withContext(Dispatchers.IO) {
                                    ApiService.uploadRectifyImage(h.id, record.id)
                                }
                                setLoading(false)
                                when (result) {
                                    is ApiResult.Success -> {
                                        toastAndSpeak("整改影像已上传")
                                        render(result.data)
                                    }
                                    else -> toastAndSpeak("绑定失败：${result.errorMessage()}")
                                }
                            }
                        }

                        override fun onError(message: String) {
                            setLoading(false)
                            toastAndSpeak("上传失败：$message")
                        }
                    }
                )
            },
            onError = { msg -> toastAndSpeak("拍照失败：$msg") }
        )
    }

    /** 复查 */
    private fun recheck(pass: Boolean) {
        val h = hazard ?: return
        setLoading(true)
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                ApiService.recheckHazard(h.id, HazardRecheckRequest(pass = pass))
            }
            setLoading(false)
            when (result) {
                is ApiResult.Success -> {
                    toastAndSpeak(if (pass) "复查合格，已闭环" else "已驳回整改")
                    render(result.data)
                }
                else -> toastAndSpeak("操作失败：${result.errorMessage()}")
            }
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

    override fun onDestroy() {
        super.onDestroy()
        if (::cameraManager.isInitialized) cameraManager.release()
    }

    companion object {
        const val EXTRA_HAZARD_ID = "hazard_id"
    }
}
