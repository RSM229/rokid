// ui/workrecord/WorkRecordSessionActivity.kt
package com.gdjl.glasses.ui.workrecord

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.camera.view.PreviewView
import com.gdjl.glasses.R
import com.gdjl.glasses.camera.CameraManager
import com.gdjl.glasses.network.ApiClient
import com.gdjl.glasses.network.ApiService
import com.gdjl.glasses.network.models.ApiResult
import com.gdjl.glasses.network.models.ClassificationResult
import com.gdjl.glasses.service.WorkRecordService
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.gdjl.glasses.utils.WatermarkInfo
import java.io.File
import java.text.SimpleDateFormat
import java.util.*

class WorkRecordSessionActivity : BaseBusinessActivity() {

    companion object {
        private const val TAG = "WorkRecordSession"
    }

    // UI组件
    private lateinit var cameraPreview: PreviewView
    private lateinit var touchpad: View
    private lateinit var btnRecord: Button
    private lateinit var btnComplete: Button
    private lateinit var tvStatus: TextView
    private lateinit var tvPhotoCount: TextView
    private lateinit var tvVideoCount: TextView
    private lateinit var tvWorkType: TextView
    private lateinit var tvVoiceText: TextView
    private lateinit var tvClassificationStatus: TextView
    private lateinit var tvTimer: TextView

    // 核心组件
    private lateinit var cameraManager: CameraManager
    private lateinit var apiService: ApiService
    private lateinit var workRecordService: WorkRecordService
    
    // 会话管理
    private val sessionManager = WorkSessionManager()
    
    // 状态
    private var isRecording = false  // 录像状态（仅用于UI，实际由 CameraManager 管理）
    private var isClassifying = false
    private var suggestedWorkType: String? = null
    private var isWaitingForConfirmation = false
    private var lastClickTime = 0L
    private var clickPending = false
    
    // 计时器
    private val timerHandler = Handler(Looper.getMainLooper())
    private var timerSeconds = 0
    private val timerRunnable = object : Runnable {
        override fun run() {
            timerSeconds++
            updateTimerDisplay()
            timerHandler.postDelayed(this, 1000)
        }
    }
    
    private val dateFormat = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_work_record_session)
        
        initViews()
        initComponents()
        setupVoiceRecognition()
        
        // 初始化工作记录服务
        workRecordService = WorkRecordService(this)
        workRecordService.init()
        workRecordService.setVoicePlayer(voicePlayer)
        
        // 设置回调
        workRecordService.setOnStatusChanged { status ->
            runOnUiThread { tvStatus.text = status }
        }
        workRecordService.setOnWorkTypeChanged { type ->
            runOnUiThread { 
                updateWorkTypeDisplay(type)
                tvClassificationStatus.text = "已设置: $type ✅"
            }
        }
        workRecordService.setOnSessionComplete { sessionData ->
            runOnUiThread {
                Toast.makeText(this, "工作记录已保存: ${sessionData.workType}", Toast.LENGTH_SHORT).show()
            }
        }
        
        workRecordService.startWorkSession()
        
        sessionManager.createNewSession()
        setupTouchpadGestures()
        
        speak("已进入工作记录模式，单击拍照，按住说话")
        startTimer()
    }

    override fun onResume() {
        super.onResume()
        // 在 onResume 中初始化相机，此时 LifecycleOwner 已就绪
        if (::cameraManager.isInitialized) {
            setupCamera()
        }
    }

    private fun initViews() {
        cameraPreview = findViewById(R.id.camera_preview)
        touchpad = findViewById(R.id.touchpad)
        btnRecord = findViewById(R.id.btn_record)
        btnComplete = findViewById(R.id.btn_complete)
        tvStatus = findViewById(R.id.tv_status)
        tvPhotoCount = findViewById(R.id.tv_photo_count)
        tvVideoCount = findViewById(R.id.tv_video_count)
        tvWorkType = findViewById(R.id.tv_work_type)
        tvVoiceText = findViewById(R.id.tv_voice_text)
        tvClassificationStatus = findViewById(R.id.tv_classification_status)
        tvTimer = findViewById(R.id.tv_timer)
        
        btnComplete.setOnClickListener {
            finishSession()
        }
    }

    private fun initComponents() {
        cameraManager = CameraManager(this, this)
        apiService = ApiService
    }

    private fun setupCamera() {
        try {
            Handler(Looper.getMainLooper()).postDelayed({
                cameraManager.bind(cameraPreview)
                Log.d(TAG, "相机绑定成功")
            }, 500)
        } catch (e: Exception) {
            Log.e(TAG, "相机绑定失败", e)
        }
    }

    private fun setupVoiceRecognition() {
        // 全程语音：持续监听，说话自动识别并执行
        startContinuousVoice(
            onResult = { text ->
                runOnUiThread { handleVoiceInput(text) }
            },
            onError = { code ->
                runOnUiThread {
                    tvStatus.text = "语音识别错误: $code"
                }
            }
        )
    }

    private fun setupTouchpadGestures() {
        touchpad.setOnClickListener {
            val currentTime = System.currentTimeMillis()
            if (currentTime - lastClickTime < 400) {
                clickPending = false
                // 双击 -> 启动/停止定时采集（替代录像功能）
                togglePeriodicCapture()
                lastClickTime = 0
            } else {
                lastClickTime = currentTime
                clickPending = true
                Handler(Looper.getMainLooper()).postDelayed({
                    if (clickPending) {
                        takePhoto()
                        clickPending = false
                    }
                }, 400)
            }
        }
    }

    // ========== 拍照 ==========
    private fun takePhoto() {
        try {
            // 构建水印信息
            val watermarkInfo = WatermarkInfo(
                projectName = "项目名称",
                inspector = getUserName(),
                captureTime = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date()),
                gpsCoords = getCurrentLocation(),
                weather = null,
                section = null,
                hazardFlag = null
            )
            
            cameraManager.takePhoto(
                watermark = watermarkInfo,
                onSuccess = { file ->
                    runOnUiThread {
                        sessionManager.addPhoto(file)
                        workRecordService.getCurrentSession()?.addPhoto(file.absolutePath)
                        updatePhotoCount()
                        speak("已拍照")
                        classifySession()
                        tvStatus.text = "已拍照: ${file.name}"
                    }
                },
                onError = { error ->
                    runOnUiThread {
                        Toast.makeText(this, "拍照失败: $error", Toast.LENGTH_SHORT).show()
                        tvStatus.text = "拍照失败: $error"
                    }
                }
            )
        } catch (e: Exception) {
            e.printStackTrace()
            Toast.makeText(this, "拍照失败: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    // ========== 定时采集（替代录像功能） ==========
    private var isPeriodicRunning = false
    
    private fun togglePeriodicCapture() {
        if (isPeriodicRunning) {
            cameraManager.stopPeriodicCapture()
            isPeriodicRunning = false
            btnRecord.text = "自动采集"
            btnRecord.setBackgroundResource(R.drawable.btn_rounded)
            speak("已停止自动采集")
            tvStatus.text = "已停止自动采集"
        } else {
            cameraManager.startPeriodicCapture(
                intervalSec = 3L,
                onCapture = { file ->
                    runOnUiThread {
                        sessionManager.addPhoto(file)
                        workRecordService.getCurrentSession()?.addPhoto(file.absolutePath)
                        updatePhotoCount()
                        tvStatus.text = "自动采集: ${file.name}"
                        classifySession()
                    }
                }
            )
            isPeriodicRunning = true
            btnRecord.text = "停止采集"
            btnRecord.setBackgroundResource(R.drawable.btn_rounded_primary)
            speak("开始自动采集")
            tvStatus.text = "开始自动采集"
        }
    }

    // ========== 语音输入处理 ==========
    private fun handleVoiceInput(text: String) {
        tvVoiceText.text = text
        tvVoiceText.visibility = View.VISIBLE
        
        sessionManager.addVoiceNote(text)
        workRecordService.getCurrentSession()?.addVoiceNote(text)
        
        when {
            text.contains("完成") || text.contains("结束") -> {
                finishSession()
            }
            text.contains("巡视") -> setWorkTypeAndConfirm("巡视")
            text.contains("验收") -> setWorkTypeAndConfirm("验收")
            text.contains("旁站") -> setWorkTypeAndConfirm("旁站")
            text.contains("见证") -> setWorkTypeAndConfirm("见证")
            text.contains("质量") -> setWorkTypeAndConfirm("质量")
            text.contains("其他工作") || text.contains("其它工作") -> setWorkTypeAndConfirm("其他工作")
            text.contains("对") || text.contains("是") || text.contains("正确") -> {
                confirmSuggestion()
            }
            text.contains("不对") || text.contains("不是") || text.contains("改") -> {
                rejectSuggestion()
            }
            else -> {
                if (sessionManager.getPhotoCount() > 0) {
                    classifySession()
                }
            }
        }
    }

    private fun setWorkTypeAndConfirm(type: String) {
        sessionManager.setWorkType(type)
        workRecordService.setWorkType(type)
        updateWorkTypeDisplay(type)
        speak("已设置为${type}")
        isWaitingForConfirmation = false
        suggestedWorkType = null
        tvClassificationStatus.text = "已设置: $type ✅"
    }

    private fun confirmSuggestion() {
        suggestedWorkType?.let { type ->
            if (type != "待定") {
                sessionManager.setWorkType(type)
                workRecordService.setWorkType(type)
                updateWorkTypeDisplay(type)
                speak("已确认${type}")
                tvClassificationStatus.text = "已确认: $type ✅"
            }
            isWaitingForConfirmation = false
            suggestedWorkType = null
        } ?: run {
            speak("当前没有待确认的分类建议")
        }
    }

    private fun rejectSuggestion() {
        suggestedWorkType?.let {
            speak("请告诉我正确的类型")
            tvClassificationStatus.text = "请语音指定类型"
            isWaitingForConfirmation = true
        }
    }

    // ========== AI分类 ==========
    private fun classifySession() {
        if (isClassifying) return
        if (sessionManager.getPhotoCount() == 0) return
        
        isClassifying = true
        tvClassificationStatus.text = "AI分析中..."
        
        val photos = sessionManager.getPhotos()
        if (photos.isEmpty()) {
            isClassifying = false
            return
        }
        
        val firstPhoto = photos.first()
        val voiceText = sessionManager.getVoiceSummary()
        
        apiService.classifyWorkSession(
            image = firstPhoto,
            voiceText = voiceText.takeIf { it.isNotBlank() }
        ) { result ->
            runOnUiThread {
                isClassifying = false
                when (result) {
                    is ApiResult.Success -> handleClassificationResult(result.data)
                    is ApiResult.Error -> tvClassificationStatus.text = "分类失败: ${result.message}"
                    is ApiResult.Exception -> tvClassificationStatus.text = "分类服务不可用"
                }
            }
        }
    }

    private fun handleClassificationResult(result: ClassificationResult) {
        val type = result.workType
        val confidence = result.confidence
        suggestedWorkType = type
        
        when {
            confidence > 0.85 -> {
                sessionManager.setWorkType(type)
                workRecordService.setWorkType(type)
                updateWorkTypeDisplay(type)
                tvClassificationStatus.text = "判断为: $type (${(confidence * 100).toInt()}%)"
                speak("判断为${type}，是否正确？")
                isWaitingForConfirmation = true
            }
            confidence > 0.5 -> {
                tvClassificationStatus.text = "请告诉我类型 (${(confidence * 100).toInt()}%置信度)"
                speak("这次是巡视、验收、旁站、见证、质量还是其他工作？")
                isWaitingForConfirmation = true
            }
            else -> {
                sessionManager.setWorkType("待定")
                workRecordService.setWorkType("待定")
                updateWorkTypeDisplay("待定")
                tvClassificationStatus.text = "未能确定类型，已归入待定"
                speak("未能确定工作类型，已归入待定")
                isWaitingForConfirmation = false
            }
        }
    }

    // ========== 结束会话 ==========
    private fun finishSession() {
        if (!sessionManager.hasContent()) {
            speak("没有记录内容，返回")
            finish()
            return
        }
        
        val workType = sessionManager.getWorkType()
        if (workType == "待定") {
            speak("当前记录为待定状态，确认完成吗？")
            Handler(Looper.getMainLooper()).postDelayed({
                doFinishSession()
            }, 2000)
        } else {
            doFinishSession()
        }
    }

    private fun doFinishSession() {
        tvStatus.text = "正在保存..."

        try {
            // 停止定时采集
            if (isPeriodicRunning) {
                cameraManager.stopPeriodicCapture()
                isPeriodicRunning = false
            }

            val photos = sessionManager.getPhotos()
            if (photos.isEmpty()) {
                cleanupLocalSessionAsync()
                speak("工作记录已保存")
                stopTimer()
                finish()
                return
            }

            val voiceText = sessionManager.getVoiceSummary()
            tvStatus.text = "上传保存中..."
            speak("正在上传保存")

            apiService.completeWorkSession(
                photos = photos,
                voiceText = voiceText
            ) { result ->
                runOnUiThread {
                    when (result) {
                        is ApiResult.Success -> {
                            val data = result.data
                            val type = data.workType ?: "待定"
                            val msg = buildString {
                                append("工作记录已保存")
                                if (!type.isNullOrBlank() && type != "待定") {
                                    append("，类型${type}")
                                }
                                append("，共${data.photoCount}张照片")
                            }
                            tvStatus.text = "保存成功 ✔ ${type} ${data.photoCount}张"
                            speak(msg)
                        }
                        is ApiResult.Error -> {
                            val msg = if (result.code == 401) "登录已过期，请重新登录" else "保存失败：${result.message}"
                            tvStatus.text = msg
                            speak(msg)
                        }
                        is ApiResult.Exception -> {
                            tvStatus.text = "保存失败，网络异常"
                            speak("保存失败，网络异常")
                        }
                    }
                    // 本地会话结束（后台线程执行，binder 阻塞/异常不影响退出）
                    cleanupLocalSessionAsync()
                    stopTimer()
                    finish()
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
            tvStatus.text = "保存失败"
            Toast.makeText(this, "保存失败: ${e.message}", Toast.LENGTH_SHORT).show()
            cleanupLocalSessionAsync()
            stopTimer()
            finish()
        }
    }

    /**
     * 后台线程清理本地工作会话。
     *
     * finishWorkSession 内部含 TTS/ASR/Media/P2P 等 binder 调用，
     * 在无网或 SDK 服务异常时可能抛异常甚至阻塞；绝不能放在 finish() 之前的主线程上同步执行
     * （2026-09-30 实机复现：空照片 + 无网络时页面卡死在“正在保存…”）。
     */
    private fun cleanupLocalSessionAsync() {
        Thread {
            runCatching { workRecordService.finishWorkSession() }
                .onFailure { Log.e(TAG, "本地会话清理失败", it) }
        }.start()
    }

    // ========== UI更新 ==========
    private fun updatePhotoCount() {
        tvPhotoCount.text = "📷 ${sessionManager.getPhotoCount()}张"
    }

    private fun updateVideoCount() {
        // 由于没有录像功能，显示为自动采集次数
        tvVideoCount.text = "📸 ${sessionManager.getPhotoCount()}次"
    }

    private fun updateWorkTypeDisplay(type: String) {
        tvWorkType.text = type
        val color = when (type) {
            "巡视" -> android.graphics.Color.parseColor("#4CAF50")
            "验收" -> android.graphics.Color.parseColor("#2196F3")
            "旁站" -> android.graphics.Color.parseColor("#FF9800")
            "见证" -> android.graphics.Color.parseColor("#9C27B0")
            "质量" -> android.graphics.Color.parseColor("#E91E63")
            "其他工作" -> android.graphics.Color.parseColor("#607D8B")
            else -> android.graphics.Color.parseColor("#F44336")
        }
        tvWorkType.setTextColor(color)
    }

    private fun updateTimerDisplay() {
        val minutes = timerSeconds / 60
        val seconds = timerSeconds % 60
        tvTimer.text = String.format("%02d:%02d", minutes, seconds)
    }

    private fun startTimer() {
        timerSeconds = 0
        timerHandler.post(timerRunnable)
    }

    private fun stopTimer() {
        timerHandler.removeCallbacks(timerRunnable)
    }

    private fun getUserName(): String = "监理人员"
    private fun getUserProject(): String = "项目"
    private fun getCurrentLocation(): String = "位置"

    override fun onDestroy() {
        super.onDestroy()
        stopTimer()
        cameraManager.release()
        workRecordService.release()
    }
}
