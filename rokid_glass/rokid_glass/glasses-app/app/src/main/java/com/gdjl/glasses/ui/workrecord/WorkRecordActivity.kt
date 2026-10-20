package com.gdjl.glasses.ui.workrecord

import android.Manifest
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.MotionEvent
import android.widget.TextView
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.gdjl.glasses.R
import com.gdjl.glasses.network.ApiService
import com.gdjl.glasses.network.models.ApiResult
import com.gdjl.glasses.network.models.WorkRecordCreateRequest
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.gdjl.glasses.voice.SpeechRecognizerManager
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class WorkRecordActivity : BaseBusinessActivity() {

    private lateinit var previewView: PreviewView
    private lateinit var btnFinish: MaterialButton
    private lateinit var btnVoice: MaterialButton
    private lateinit var tvStatus: TextView
    private lateinit var tvHint: TextView

    private var imageCapture: ImageCapture? = null
    private var cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private var mediaRecorder: MediaRecorder? = null
    private var photoFiles = mutableListOf<File>()
    private var recordedText = ""
    private var isVideoRecording = false

    private var selectedOption = 0
    private val workTypeOptions = listOf("巡视记录", "验收记录", "旁站记录", "见证记录", "质量问题")

    private var touchDownTime = 0L
    private var isLongPress = false
    private val longPressHandler = Handler(Looper.getMainLooper())
    private var longPressRunnable: Runnable? = null
    private var lastTouchX = 0f
    private var lastTouchY = 0f

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_work_record)

        previewView = findViewById(R.id.previewView)
        btnFinish = findViewById(R.id.btn_finish)
        btnVoice = findViewById(R.id.btn_voice)
        tvStatus = findViewById(R.id.tv_status)
        tvHint = findViewById(R.id.tv_hint)

        // 关键：让 PreviewView 不拦截触摸事件
        previewView.setOnTouchListener { _, _ -> false }
        previewView.isClickable = false
        previewView.isFocusable = false
        previewView.isFocusableInTouchMode = false

        if (!allPermissionsGranted()) {
            requestPermissions()
        }

        btnFinish.setOnClickListener { finishRecord() }
        btnVoice.setOnClickListener { startVoiceRecognition() }

        tvStatus.text = "📷 准备就绪"
        tvHint.text = "单击拍照 | 长按录像 | 滑动选择类型"

        startCamera()
    }

    // ==================== 触控板事件 ====================

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                touchDownTime = System.currentTimeMillis()
                lastTouchX = event.x
                lastTouchY = event.y
                isLongPress = false

                longPressRunnable = Runnable {
                    isLongPress = true
                    toggleVideoRecording()
                }
                longPressHandler.postDelayed(longPressRunnable!!, 600)
                return true
            }
            MotionEvent.ACTION_UP -> {
                longPressRunnable?.let { longPressHandler.removeCallbacks(it) }
                longPressRunnable = null

                if (isLongPress) {
                    return true
                }

                val duration = System.currentTimeMillis() - touchDownTime
                if (duration < 600) {
                    takePhoto()
                }
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                val deltaX = event.x - lastTouchX
                val deltaY = event.y - lastTouchY
                if (Math.abs(deltaX) > 50 || Math.abs(deltaY) > 50) {
                    longPressRunnable?.let { longPressHandler.removeCallbacks(it) }
                    longPressRunnable = null
                    if (Math.abs(deltaX) > Math.abs(deltaY)) {
                        if (deltaX > 0) selectNext() else selectPrevious()
                    } else {
                        if (deltaY > 0) selectNext() else selectPrevious()
                    }
                    lastTouchX = event.x
                    lastTouchY = event.y
                    return true
                }
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    // ==================== 拍照 ====================

    private fun takePhoto() {
        val imageCapture = imageCapture ?: return
        val photoFile = File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES),
            "GDJL_${System.currentTimeMillis()}.jpg"
        )
        val outputOptions = ImageCapture.OutputFileOptions.Builder(photoFile).build()
        imageCapture.takePicture(outputOptions, ContextCompat.getMainExecutor(this),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    photoFiles.add(photoFile)
                    tvStatus.text = "📸 已拍照 (${photoFiles.size}张)"
                    speak("已拍照")
                }
                override fun onError(exception: ImageCaptureException) {
                    tvStatus.text = "❌ 拍照失败"
                }
            })
    }

    // ==================== 录像 ====================

    private fun toggleVideoRecording() {
        if (isVideoRecording) {
            stopVideoRecording()
        } else {
            startVideoRecording()
        }
    }

    private fun startVideoRecording() {
        try {
            val videoFile = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MOVIES),
                "GDJL_VIDEO_${System.currentTimeMillis()}.mp4"
            )
            mediaRecorder = MediaRecorder().apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setVideoSource(MediaRecorder.VideoSource.CAMERA)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setVideoEncoder(MediaRecorder.VideoEncoder.H264)
                setOutputFile(videoFile.absolutePath)
                prepare()
                start()
            }
            isVideoRecording = true
            tvStatus.text = "🎥 录像中... 长按停止"
            speak("开始录像")
        } catch (e: Exception) {
            tvStatus.text = "❌ 录像启动失败"
        }
    }

    private fun stopVideoRecording() {
        try {
            mediaRecorder?.apply { stop(); release() }
            mediaRecorder = null
            isVideoRecording = false
            tvStatus.text = "📹 录像已保存"
            speak("录像已保存")
        } catch (e: Exception) {
            tvStatus.text = "❌ 停止录像失败"
        }
    }

    // ==================== 选择工作类型 ====================

    private fun selectNext() {
        selectedOption = (selectedOption + 1) % workTypeOptions.size
        showSelectedOption()
        speak("选择${workTypeOptions[selectedOption]}")
    }

    private fun selectPrevious() {
        selectedOption = (selectedOption - 1 + workTypeOptions.size) % workTypeOptions.size
        showSelectedOption()
        speak("选择${workTypeOptions[selectedOption]}")
    }

    private fun showSelectedOption() {
        tvHint.text = "当前选择: ${workTypeOptions[selectedOption]}"
    }

    // ==================== 语音识别 ====================

    private fun startVoiceRecognition() {
        if (!speechRecognizer.isAvailable()) {
            toast("设备不支持语音识别，请手动操作")
            return
        }

        tvStatus.text = "🎤 请说话..."
        speak("请说话")

        speechRecognizer.startListening(object : SpeechRecognizerManager.Listener {
            override fun onResult(text: String) {
                tvStatus.text = "📝 识别: $text"
                handleVoiceCommand(text)
            }

            override fun onError(errorCode: Int) {
                tvStatus.text = "❌ 识别失败，请重试"
                toast("识别失败")
            }
        })
    }

    private fun handleVoiceCommand(text: String) {
        val lowerText = text.lowercase().trim()
        if (lowerText.isEmpty()) return

        when {
            lowerText.contains("拍照") -> { takePhoto(); speak("拍照") }
            lowerText.contains("录像") && !lowerText.contains("停止") -> {
                if (!isVideoRecording) startVideoRecording()
            }
            lowerText.contains("停止录像") -> { if (isVideoRecording) stopVideoRecording() }
            lowerText.contains("描述") || lowerText.contains("录音") -> {
                val content = text.replace(Regex("描述|录音"), "").trim()
                if (content.isNotEmpty()) {
                    recordedText = if (recordedText.isEmpty()) content else "$recordedText\n$content"
                    tvStatus.text = "📝 已记录"
                    speak("已记录")
                }
            }
            lowerText.contains("完成") || lowerText.contains("结束") -> { finishRecord() }
            lowerText.contains("巡视") -> { selectWorkType("巡视记录") }
            lowerText.contains("验收") -> { selectWorkType("验收记录") }
            lowerText.contains("旁站") -> { selectWorkType("旁站记录") }
            lowerText.contains("见证") -> { selectWorkType("见证记录") }
            lowerText.contains("质量") -> { selectWorkType("质量问题") }
            else -> {
                if (lowerText.length > 2) {
                    recordedText = if (recordedText.isEmpty()) lowerText else "$recordedText\n$lowerText"
                    tvStatus.text = "📝 已记录: $lowerText"
                    speak("已记录")
                }
            }
        }
    }

    private fun selectWorkType(type: String) {
        selectedOption = workTypeOptions.indexOf(type)
        if (selectedOption >= 0) {
            showSelectedOption()
            speak("选择$type")
        }
    }

    // ==================== 完成工作 ====================

    private fun finishRecord() {
        if (isVideoRecording) stopVideoRecording()
        tvStatus.text = "⏳ 正在保存..."

        val selectedType = workTypeOptions[selectedOption]
        val workType = when (selectedType) {
            "巡视记录" -> "inspection"
            "验收记录" -> "acceptance"
            "旁站记录" -> "station"
            "见证记录" -> "witness"
            "质量问题" -> "quality"
            else -> "pending"
        }

        val content = buildString {
            if (recordedText.isNotEmpty()) {
                append("【语音描述】\n$recordedText\n")
            }
            append("【照片数量】${photoFiles.size}张\n")
        }

        saveRecord(workType, content)
    }

    private fun saveRecord(workType: String, content: String) {
        lifecycleScope.launch {
            val req = WorkRecordCreateRequest(
                type = workType,
                section = "",
                content = content,
                imageIds = emptyList()
            )
            val result = withContext(Dispatchers.IO) { ApiService.createWorkRecord(req) }
            when (result) {
                is ApiResult.Success -> {
                    tvStatus.text = "✅ 记录已保存"
                    toastAndSpeak("记录已保存")
                    finish()
                }
                else -> {
                    tvStatus.text = "❌ 保存失败: ${result.errorMessage()}"
                    toastAndSpeak("保存失败")
                }
            }
        }
    }

    // ==================== 权限 ====================

    private fun allPermissionsGranted(): Boolean {
        val permissions = listOf(
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.WRITE_EXTERNAL_STORAGE
        )
        return permissions.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun requestPermissions() {
        ActivityCompat.requestPermissions(
            this,
            arrayOf(
                Manifest.permission.CAMERA,
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.WRITE_EXTERNAL_STORAGE
            ),
            100
        )
    }

    // ==================== 相机 ====================

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()
            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(previewView.surfaceProvider)
            }
            imageCapture = ImageCapture.Builder().build()
            val cameraSelector = CameraSelector.DEFAULT_BACK_CAMERA
            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(this, cameraSelector, preview, imageCapture)
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    override fun onDestroy() {
        super.onDestroy()
        cameraExecutor.shutdown()
        mediaRecorder?.release()
        longPressRunnable?.let { longPressHandler.removeCallbacks(it) }
    }
}
