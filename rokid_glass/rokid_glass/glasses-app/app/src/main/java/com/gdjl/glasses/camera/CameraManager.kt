// 创建日期: 2026-07-18
package com.gdjl.glasses.camera

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.gdjl.glasses.device.RokidDeviceRuntime
import com.gdjl.glasses.network.AuthManager
import com.gdjl.glasses.utils.WatermarkInfo
import com.gdjl.glasses.utils.WatermarkOverlay
import java.io.File
import java.io.FileOutputStream
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * CameraX 相机封装
 *
 * 职责：
 * 1. 初始化 [PreviewView] 与 [ImageCapture]，绑定到 [LifecycleOwner]
 * 2. 单次拍照 [takePhoto]（可选自动叠加不可篡改水印）
 * 3. 按频率自动采集 [startPeriodicCapture] / [stopPeriodicCapture]（巡检模式）
 *
 * 默认使用后置摄像头，眼镜端可按需切换 [CameraSelector]。
 *
 * 水印：当 [takePhoto] 传入 [WatermarkInfo] 时，拍照后调用 [WatermarkOverlay.applyWatermark]
 * 在 Bitmap 上绘制半透明水印条（项目/人员/时间/部位/隐患标识/SHA256），并在文件名加 `_wm` 后缀
 * 标记本地水印已叠加，供 [ImageUploader] 上传时设置 `local_watermarked=true`。
 */
class CameraManager(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
) {
    companion object {
        private const val TAG = "CameraManager"
        private const val FILENAME_PREFIX = "gdjl_"
        private const val PHOTO_EXTENSION = ".jpg"
        /** 本地水印已叠加的文件名后缀标记 */
        const val WATERMARKED_SUFFIX = "_wm"
    }

    private var imageCapture: ImageCapture? = null
    private var cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    /** 定时采集状态 */
    @Volatile
    private var periodicRunning = false
    private var periodicIntervalSec: Long = 5L
    private var periodicCallback: ((File) -> Unit)? = null
    private var periodicRunnable: Runnable? = null

    /** 输出目录（应用私有缓存，无需存储权限） */
    private val outputDir: File by lazy {
        File(context.cacheDir, "captures").apply { if (!exists()) mkdirs() }
    }

    /**
     * 初始化预览与拍照用例，绑定到生命周期
     *
     * @param previewView 布局中的 PreviewView；为 null 时不显示预览（仅后台采集）
     */
    @SuppressLint("RestrictedApi")
    fun bind(previewView: PreviewView? = null) {
        if (RokidDeviceRuntime.edition == "enterprise") {
            // Glass3 系统媒体服务独占相机；并行绑定 CameraX 会触发 CAMERA_IN_USE。
            Log.i(TAG, "企业版使用 Glass Media Service，不绑定 CameraX")
            return
        }
        val providerFuture = ProcessCameraProvider.getInstance(context)
        providerFuture.addListener({
            try {
                val provider = providerFuture.get()
                val capture = ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                    .build()

                val selector = CameraSelector.DEFAULT_BACK_CAMERA

                provider.unbindAll()
                if (previewView != null) {
                    val preview = Preview.Builder().build().also {
                        it.setSurfaceProvider(previewView.surfaceProvider)
                    }
                    provider.bindToLifecycle(lifecycleOwner, selector, preview, capture)
                } else {
                    provider.bindToLifecycle(lifecycleOwner, selector, capture)
                }
                imageCapture = capture
                Log.i(TAG, "CameraX 绑定成功")
            } catch (e: Exception) {
                Log.e(TAG, "CameraX 绑定失败", e)
            }
        }, ContextCompat.getMainExecutor(context))
    }

    /**
     * 单次拍照（不叠加水印）
     *
     * @param onSuccess 拍照成功回调（主线程），返回写入的文件
     * @param onError 拍照失败回调（主线程），返回错误描述
     */
    fun takePhoto(onSuccess: (File) -> Unit, onError: (String) -> Unit) {
        takePhoto(watermark = null, onSuccess = onSuccess, onError = onError)
    }

    /**
     * 单次拍照（可选叠加水印）
     *
     * @param watermark 水印信息；非空时拍照后叠加不可篡改水印（文件名加 [WATERMARKED_SUFFIX]）
     * @param onSuccess 拍照成功回调（主线程），返回写入的文件
     * @param onError 拍照失败回调（主线程），返回错误描述
     */
    fun takePhoto(
        watermark: WatermarkInfo?,
        onSuccess: (File) -> Unit,
        onError: (String) -> Unit,
    ) {
        val rawFile = createOutputFile()
        val completed = AtomicBoolean(false)
        val handledByVendor = RokidDeviceRuntime.capturePhoto(rawFile) { result ->
            if (!completed.compareAndSet(false, true)) return@capturePhoto
            result.fold(
                onSuccess = { captured ->
                    val finalFile = if (watermark != null) {
                        applyWatermarkToFile(captured, watermark)
                    } else {
                        captured
                    }
                    mainHandler.post { onSuccess(finalFile) }
                },
                onFailure = { error ->
                    Log.e(TAG, "企业版媒体服务拍照失败", error)
                    mainHandler.post { onError(error.message ?: "企业版媒体服务拍照失败") }
                },
            )
        }
        if (handledByVendor) {
            mainHandler.postDelayed({
                if (completed.compareAndSet(false, true)) {
                    onError("企业版媒体服务拍照超时")
                }
            }, 15_000L)
            return
        }

        val capture = imageCapture
        if (capture == null) {
            onError("相机未初始化")
            return
        }
        val options = ImageCapture.OutputFileOptions.Builder(rawFile).build()
        capture.takePicture(
            options,
            cameraExecutor,
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    Log.i(TAG, "拍照成功: ${rawFile.absolutePath}")
                    val finalFile = if (watermark != null) {
                        applyWatermarkToFile(rawFile, watermark)
                    } else {
                        rawFile
                    }
                    mainHandler.post { onSuccess(finalFile) }
                }

                override fun onError(exc: ImageCaptureException) {
                    Log.e(TAG, "拍照失败: code=${exc.imageCaptureError} msg=${exc.message}", exc)
                    mainHandler.post { onError(exc.message ?: "拍照失败") }
                }
            }
        )
    }

    /**
     * 按频率自动采集（巡检模式）
     *
     * 首次立即采集一次，之后每隔 [intervalSec] 秒采集一次，直到调用 [stopPeriodicCapture]。
     * 巡检模式自动叠加水印（项目/人员/时间），便于后续取证。
     *
     * @param intervalSec 采集间隔（秒），默认 5
     * @param onCapture 每次采集成功回调（主线程）
     */
    fun startPeriodicCapture(intervalSec: Long = 5L, onCapture: (File) -> Unit) {
        if (periodicRunning) {
            Log.w(TAG, "定时采集已在运行，忽略重复启动")
            return
        }
        periodicRunning = true
        periodicIntervalSec = intervalSec.coerceAtLeast(1L)
        periodicCallback = onCapture
        Log.i(TAG, "启动定时采集，间隔 ${periodicIntervalSec}s")
        scheduleNextCapture(delayMs = 0L)
    }

    /**
     * 停止定时采集
     */
    fun stopPeriodicCapture() {
        if (!periodicRunning) return
        periodicRunning = false
        periodicRunnable?.let { mainHandler.removeCallbacks(it) }
        periodicRunnable = null
        periodicCallback = null
        Log.i(TAG, "停止定时采集")
    }

    private fun scheduleNextCapture(delayMs: Long) {
        if (!periodicRunning) return
        val r = Runnable {
            if (!periodicRunning) return@Runnable
            // 巡检采集自动叠加基础水印
            takePhoto(
                watermark = buildDefaultWatermark(section = null, hazardFlag = null),
                onSuccess = { file ->
                    periodicCallback?.invoke(file)
                    scheduleNextCapture(periodicIntervalSec * 1000L)
                },
                onError = { msg ->
                    Log.w(TAG, "定时采集失败：$msg，继续下一次")
                    scheduleNextCapture(periodicIntervalSec * 1000L)
                }
            )
        }
        periodicRunnable = r
        mainHandler.postDelayed(r, delayMs)
    }

    /**
     * 释放资源
     *
     * 在 Activity.onDestroy 中调用。
     */
    fun release() {
        stopPeriodicCapture()
        cameraExecutor.shutdown()
    }

    private fun createOutputFile(): File {
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.CHINA).format(Date())
        return File(outputDir, "$FILENAME_PREFIX$timestamp$PHOTO_EXTENSION")
    }

    /**
     * 将水印应用到拍照文件，返回带 [WATERMARKED_SUFFIX] 后缀的新文件
     *
     * 流程：读取原图 -> [WatermarkOverlay.applyWatermark] -> 覆盖写回 -> 重命名加后缀。
     */
    private fun applyWatermarkToFile(file: File, watermark: WatermarkInfo): File {
        return try {
            val bitmap = BitmapFactory.decodeFile(file.absolutePath) ?: return file
            val watermarked = WatermarkOverlay.applyWatermark(bitmap, watermark)
            FileOutputStream(file).use { out ->
                watermarked.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, out)
            }
            bitmap.recycle()
            // 重命名加 _wm 后缀标记本地水印已叠加
            val newName = file.nameWithoutExtension + WATERMARKED_SUFFIX + PHOTO_EXTENSION
            val target = File(file.parentFile, newName)
            if (target.exists()) target.delete()
            file.renameTo(target)
            Log.i(TAG, "水印叠加完成: ${target.absolutePath}")
            target
        } catch (e: Exception) {
            Log.e(TAG, "水印叠加失败，返回原图", e)
            file
        }
    }

    /**
     * 构建默认水印信息（从 [AuthManager] 读取项目/人员）
     */
    private fun buildDefaultWatermark(section: String?, hazardFlag: String?): WatermarkInfo {
        val user = AuthManager.getUserInfo()
        return WatermarkInfo(
            projectName = user?.projectName,
            inspector = user?.realName ?: user?.username,
            captureTime = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA).format(Date()),
            gpsCoords = null,
            weather = null,
            section = section,
            hazardFlag = hazardFlag,
        )
    }
}
