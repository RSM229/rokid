package com.gdjl.glasses.ui.hazard

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Bundle
import android.os.Environment
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.gdjl.glasses.R
import com.gdjl.glasses.camera.ImageUploader
import com.gdjl.glasses.network.ApiService
import com.gdjl.glasses.network.models.ApiResult
import com.gdjl.glasses.network.models.HazardDetectRequest
import com.gdjl.glasses.network.models.HazardDetectResponse
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.gdjl.glasses.utils.HazardModelHelper
import com.gdjl.glasses.utils.WatermarkHelper
import com.gdjl.glasses.utils.HazardSyncHelper
import com.google.android.material.button.MaterialButton
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.Executors

/**
 * 安全隐患识别（真实识别）
 *
 * 拍照后上传照片，调用后端华工 VLM 隐患识别（POST /media/{id}/detect），
 * 返回真实识别结果，叠加水印后保存，并同步到手机端。
 */
class HazardDetectActivity : BaseBusinessActivity() {

    companion object {
        private const val TAG = "HazardDetect"
    }

    private lateinit var previewView: PreviewView
    private lateinit var btnCapture: MaterialButton
    private lateinit var btnConfirm: MaterialButton
    private lateinit var btnRetake: MaterialButton
    private lateinit var llConfirm: LinearLayout
    private lateinit var tvTip: TextView
    private lateinit var tvResult: TextView
    private lateinit var progressBar: ProgressBar

    private var imageCapture: ImageCapture? = null
    private var currentPhotoFile: File? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_hazard_detect)

        previewView = findViewById(R.id.previewView)
        btnCapture = findViewById(R.id.btn_capture)
        btnConfirm = findViewById(R.id.btn_confirm)
        btnRetake = findViewById(R.id.btn_retake)
        llConfirm = findViewById(R.id.ll_confirm)
        tvTip = findViewById(R.id.tv_tip)
        tvResult = findViewById(R.id.tv_result)
        progressBar = findViewById(R.id.progressBar)

        tvTip.text = "单击拍照（真实识别）"

        btnCapture.setOnClickListener { takePhoto() }
        btnConfirm.setOnClickListener { recognize() }
        btnRetake.setOnClickListener { retake() }

        startCamera()
        enablePageVoice()
    }

    // ---------------- 全程语音 ----------------

    override fun voiceHint(): String =
        "安全隐患识别。说拍照开始拍摄；拍完说确认识别、或说重拍；说返回退出"

    override fun onPageVoiceCommand(text: String): Boolean {
        return when {
            text.contains("重拍") || text.contains("重新拍") -> {
                if (llConfirm.visibility == View.VISIBLE) retake()
                else speak("请先拍照")
                true
            }
            text.contains("确认") || text.contains("开始识别") || text.contains("识别") -> {
                if (llConfirm.visibility == View.VISIBLE) recognize()
                else if (btnCapture.visibility == View.VISIBLE) speak("请先拍照，说拍照")
                else speak("正在识别中")
                true
            }
            text.contains("拍照") || text.contains("拍摄") -> {
                if (btnCapture.visibility == View.VISIBLE && btnCapture.isEnabled) takePhoto()
                else speak("当前不可拍照")
                true
            }
            else -> false
        }
    }

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)

        cameraProviderFuture.addListener({
            val cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(previewView.surfaceProvider)
            }

            imageCapture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                .build()

            try {
                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture)
            } catch (e: Exception) {
                Log.e(TAG, "启动相机失败", e)
                tvTip.text = "相机启动失败"
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun takePhoto() {
        val imageCapture = imageCapture ?: return
        val storageDir = getExternalFilesDir(Environment.DIRECTORY_PICTURES) ?: return

        val timeStamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
        val photoFile = File(storageDir, "hazard_${timeStamp}.jpg")
        currentPhotoFile = photoFile

        btnCapture.isEnabled = false
        tvTip.text = "拍照中..."

        imageCapture.takePicture(
            ImageCapture.OutputFileOptions.Builder(photoFile).build(),
            ContextCompat.getMainExecutor(this),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(outputFileResults: ImageCapture.OutputFileResults) {
                    btnCapture.isEnabled = true
                    previewView.visibility = View.GONE
                    btnCapture.visibility = View.GONE
                    llConfirm.visibility = View.VISIBLE
                    tvTip.text = "确认照片"
                }

                override fun onError(exception: ImageCaptureException) {
                    btnCapture.isEnabled = true
                    tvTip.text = "拍照失败"
                }
            }
        )
    }

    /**
     * 真实识别流程：
     * 1. 上传照片到 /media/images（businessType=hazard）拿 imageId
     * 2. 调用华工 VLM：POST /media/{imageId}/detect
     * 3. 解析真实结果，叠加水印，保存并同步到手机端
     */
    private fun recognize() {
        val file = currentPhotoFile ?: return
        llConfirm.visibility = View.GONE
        progressBar.visibility = View.VISIBLE
        tvTip.text = "上传识别中..."

        ImageUploader(this).uploadImage(
            file = file,
            section = null,
            businessType = "hazard",
            triggerDetection = false,
            callback = object : ImageUploader.Callback {
                override fun onSuccess(record: com.gdjl.glasses.network.models.ImageRecord) {
                    detectCloud(record.id.toString())
                }

                override fun onError(message: String) {
                    progressBar.visibility = View.GONE
                    tvTip.text = "照片上传失败：$message"
                    llConfirm.visibility = View.VISIBLE
                }
            }
        )
    }

    private fun detectCloud(imageId: String) {
        tvTip.text = "华工AI识别中..."
        val result = ApiService.detectHazard(
            HazardDetectRequest(imageId = imageId, section = null, save = false)
        )
        when (result) {
            is ApiResult.Success -> handleDetectResult(result.data)
            is ApiResult.Error -> {
                progressBar.visibility = View.GONE
                val msg = if (result.code == 401) "登录已过期，请重新登录" else "识别失败：${result.message}"
                tvTip.text = msg
                speak(msg)
                llConfirm.visibility = View.VISIBLE
            }
            is ApiResult.Exception -> {
                progressBar.visibility = View.GONE
                tvTip.text = "网络异常，识别失败"
                speak("网络异常，识别失败")
                llConfirm.visibility = View.VISIBLE
            }
        }
    }

    private fun handleDetectResult(resp: HazardDetectResponse) {
        val photoFile = currentPhotoFile ?: run { finishRecognize(); return }
        val riskStatus = resp.riskStatus
        val topRisk = resp.topRisk
        val evidence = resp.evidence ?: resp.hazard?.evidence ?: emptyList()
        val hasHazard = riskStatus == "存在隐患" ||
            topRisk?.isNotBlank() == true ||
            evidence.isNotEmpty()

        try {
            val bitmap = BitmapFactory.decodeFile(photoFile.absolutePath)
            if (bitmap == null) {
                progressBar.visibility = View.GONE
                tvTip.text = "图片解析失败"
                return
            }

            // 将华工真实识别结果映射为水印用的 HazardResult 列表
            val hazards = mutableListOf<HazardModelHelper.HazardResult>()
            if (hasHazard) {
                val riskName = topRisk ?: (resp.hazard?.topRisk ?: "安全隐患")
                val level = when {
                    (riskStatus ?: "").contains("重大") -> "重大"
                    (riskStatus ?: "").contains("高") -> "高"
                    else -> "中"
                }
                hazards.add(
                    HazardModelHelper.HazardResult(
                        name = riskName,
                        confidence = 0.9f,
                        level = level
                    )
                )
            }

            val watermarked = WatermarkHelper().addWatermark(bitmap, hazards)
            val dir = File(getExternalFilesDir(Environment.DIRECTORY_PICTURES), "hazard_records")
            if (!dir.exists()) dir.mkdirs()

            val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.getDefault()).format(Date())
            val outputFile = File(dir, "巡视记录_${timestamp}.jpg")

            FileOutputStream(outputFile).use { out ->
                watermarked.compress(Bitmap.CompressFormat.JPEG, 95, out)
            }

            progressBar.visibility = View.GONE
            tvResult.visibility = View.VISIBLE

            val resultText = buildString {
                appendLine("✅ 华工AI识别完成")
                appendLine()
                if (hasHazard) {
                    appendLine("状态：$riskStatus")
                    appendLine("隐患：$topRisk")
                    if (evidence.isNotEmpty()) {
                        appendLine("依据：")
                        evidence.forEach { appendLine("  • $it") }
                    }
                } else {
                    appendLine("未发现安全隐患")
                }
            }
            tvResult.text = resultText
            tvTip.text = "双击返回"
            speak(if (hasHazard) "发现隐患：$topRisk" else "未发现安全隐患")

            Log.d(TAG, "真实识别完成: hasHazard=$hasHazard topRisk=$topRisk evidence=${evidence.size}")
            if (hazards.isNotEmpty()) {
                HazardSyncHelper.syncToPhone(outputFile, hazards)
            }

        } catch (e: Exception) {
            Log.e(TAG, "识别处理失败", e)
            progressBar.visibility = View.GONE
            tvResult.text = "识别异常"
        }
    }

    private fun finishRecognize() {
        progressBar.visibility = View.GONE
        tvTip.text = "照片已丢失，请重拍"
        llConfirm.visibility = View.VISIBLE
    }

    private fun retake() {
        previewView.visibility = View.VISIBLE
        llConfirm.visibility = View.GONE
        btnCapture.visibility = View.VISIBLE
        tvTip.text = "单击拍照"
        tvResult.visibility = View.GONE
        currentPhotoFile = null
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            finish()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }
}
