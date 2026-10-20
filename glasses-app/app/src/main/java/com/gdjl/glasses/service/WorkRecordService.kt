package com.gdjl.glasses.service

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.gdjl.glasses.data.WorkSession
import com.gdjl.glasses.voice.SpeechRecognizerManager
import com.gdjl.glasses.voice.VoicePlayer
import com.rokid.security.glass3.open.sdk.GlassSdk
import com.rokid.security.glass3.open.sdk.client.IServiceConnectionCallback
import com.rokid.security.glass3.sdk.base.data.device.bean.GlassAppConfig
import com.rokid.security.glass3.sdk.base.data.device.bean.GlassAppType
import com.rokid.security.glass3.sdk.base.data.device.bean.ThirdPartyApp
import com.rokid.security.glass3.sdk.base.data.media.PhotoResolution
import com.rokid.security.glass3.sdk.base.data.media.RecordConfig
import com.rokid.security.glass3.sdk.base.data.offlineCmd.bean.VoiceAction
import com.rokid.security.glass3.sdk.base.data.offlineCmd.listener.IVoiceCallback
import com.rokid.security.system.server.IClientCallback
import com.rokid.security.system.server.asr.IAsrService
import com.rokid.security.system.server.asr.listener.SpeechCallback
import com.rokid.security.system.server.media.IMediaServer
import com.rokid.security.system.server.media.callback.PhotoFileCallback
import com.rokid.security.system.server.media.callback.VideoCallback
import com.rokid.security.system.server.message.IMessageServer
import com.rokid.security.system.server.message.listener.IMessageListener
import com.rokid.security.system.server.offlineCmd.IOfflineCmdService
import com.rokid.security.system.server.device.listener.IAppVisibilityListener
import com.rokid.security.system.server.tts.ITtsService
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors

class WorkRecordService(private val context: Context) {
    
    companion object {
        private const val TAG = "WorkRecordService"
        private const val CLIENT_ID = "WorkRecordApp"
        private const val APP_PACKAGE_NAME = "com.gdjl.glasses"
        private const val APP_DISPLAY_NAME = "监理通"
        
        // 2026-09-30 实测：本机固件的流式 ASR 不向 App 回调文本，离线指令引擎是
        // 唯一可靠的语音识别通路 → 词表扩充为「唤起词 + 全局指令 + 菜单指令 + 工作指令」。
        private val VOICE_COMMANDS = listOf(
            // 工作会话指令（由实例 handleVoiceCommand 消费）
            "开始工作" to "kai shi gong zuo",
            "完成" to "wan cheng",
            "拍照" to "pai zhao",
            "录像" to "lu xiang",
            "停止录像" to "ting zhi lu xiang",
            "巡视" to "xun shi",
            "验收" to "yan shou",
            "旁站" to "pang zhan",
            "见证" to "jian zheng",
            "质量" to "zhi liang",
            "其他工作" to "qi ta gong zuo",
            // 唤起词（后台由唤起服务消费，前台由页面兜底应答）
            "你好监理" to "ni hao jian li",
            "监理监理" to "jian li jian li",
            // 全局指令（注入页面通道，基类统一处理）
            "返回" to "fan hui",
            "主菜单" to "zhu cai dan",
            "帮助" to "bang zhu",
            // 菜单导航指令（注入页面通道，菜单页处理）
            "工作记录" to "gong zuo ji lu",
            "安全隐患" to "an quan yin huan"
        )

        /** 非工作内容的指令词：在会话页命中时不记便签，转页面语音通道 */
        private val NAV_COMMAND_WORDS = listOf(
            "返回", "退出", "主菜单", "回首页", "帮助", "重播",
            "你好监理", "监理监理", "小监", "工作记录", "安全隐患", "隐患"
        )

        // ===== 进程级 SDK 连接（2026-09-30 重构）=====
        // GlassSdk 客户端是单槽位，全进程只允许 registerClient 一次；
        // 原来只有进入工作会话页才注册 → 全程语音（唤起/页面监听）在应用启动时拿不到 ASR。
        // 现改为 Application.onCreate 即调用 ensureInitialized，SDK 服务全进程共享。
        private val sdkInitOnce = java.util.concurrent.atomic.AtomicBoolean(false)

        @Volatile
        var isSdkReady = false
            private set

        @Volatile
        var sdkAsrService: IAsrService? = null
            private set

        @Volatile
        var sdkTtsService: ITtsService? = null
            private set

        @Volatile
        var sdkMediaServer: IMediaServer? = null
            private set

        @Volatile
        var sdkMessageServer: IMessageServer? = null
            private set

        private var sdkOfflineCmdService: IOfflineCmdService? = null

        /** 离线指令路由：由工作会话实例在初始化完成时设置，释放时清空 */
        @Volatile
        var offlineCommandHandler: ((String) -> Unit)? = null

        private val readyCallbacks = java.util.concurrent.CopyOnWriteArrayList<() -> Unit>()

        /**
         * 进程级 SDK 初始化（幂等）。bindSecurityService + registerClient 只执行一次。
         * 在 GdjlApplication.onCreate 调用；工作会话实例的 init() 也走这里。
         */
        fun ensureInitialized(context: Context) {
            if (!sdkInitOnce.compareAndSet(false, true)) return
            try {
                GlassSdk.bindSecurityService(
                    context.applicationContext,
                    object : IServiceConnectionCallback {
                        override fun onServiceConnected() {
                            Log.d(TAG, "SDK 服务已连接")
                            GlassSdk.registerClient(
                                CLIENT_ID,
                                object : IClientCallback.Stub() {
                                    override fun onReady() {
                                        sdkMediaServer = GlassSdk.getGlassMediaService()
                                        sdkOfflineCmdService = GlassSdk.getGlassOfflineCmdService()
                                        sdkAsrService = GlassSdk.getGlassAsrService()
                                        sdkTtsService = GlassSdk.getGlassTtsService()
                                        sdkMessageServer = GlassSdk.getGlassMessageService()
                                        setupOfflineCommands()
                                        scheduleAppVisibility()
                                        isSdkReady = true
                                        Log.d(TAG, "SDK 服务初始化完成（进程级）")
                                        readyCallbacks.forEach { runCatching(it) }
                                        readyCallbacks.clear()
                                    }
                                }
                            )
                        }
                        override fun onServiceDisconnected() {
                            isSdkReady = false
                            Log.d(TAG, "SDK 服务已断开")
                        }
                        override fun onBindingDied() {
                            isSdkReady = false
                            Log.d(TAG, "SDK 服务异常终止")
                        }
                    }
                )
            } catch (e: Exception) {
                Log.e(TAG, "SDK 初始化失败", e)
            }
        }

        /** SDK 就绪后执行（已就绪则立即执行） */
        fun registerWhenReady(cb: () -> Unit) {
            if (isSdkReady) cb() else readyCallbacks.add(cb)
        }

        /** 离线语音指令（进程级，应用启动即生效）；指令路由到 [offlineCommandHandler] */
        private fun setupOfflineCommands() {
            try {
                val voiceActions = VOICE_COMMANDS.map { (word, pinyin) ->
                    VoiceAction(
                        word,
                        pinyin,
                        object : IVoiceCallback.Stub() {
                            override fun onVoiceTriggered() {
                                Log.i(TAG, "离线指令触发: $word")
                                val handler = offlineCommandHandler
                                if (handler != null) {
                                    handler.invoke(word)
                                } else {
                                    // 无工作会话实例时，注入「当前活跃监听者」：
                                    // 前台=页面语音监听（页面指令分派），后台=唤起服务（唤起词分派）
                                    SpeechRecognizerManager.injectOfflineCommand(word)
                                }
                            }
                        }
                    )
                }
                sdkOfflineCmdService?.setLanguage("ZH_CN")
                sdkOfflineCmdService?.setWords("ZH_CN", voiceActions)
                sdkOfflineCmdService?.init()
                Log.d(TAG, "离线语音指令设置完成: ${VOICE_COMMANDS.size} 条")
            } catch (e: Exception) {
                Log.e(TAG, "设置离线语音指令失败", e)
            }
        }

        /**
         * 阶梯式下发应用可见性配置。
         *
         * 2026-10-08 实机逆向结论：眼镜桌面（RokidELauncher）的应用列表由
         * com.rokid.security.zjsj 的 AppConfigControl 在「每次桌面启动后约 3 秒」
         * 推送一次配置刷新；本应用的 configureAppVisibility 与之共用同一条
         * AppDataManager.onConfig 通道，**后到者生效**。
         *
         * 因此若本应用在开机时先于桌面启动完成下发（或桌面随后重启），配置就会被
         * zjsj 覆盖，表现为「内置应用全部复现」。这里采用阶梯重发，确保在 zjsj
         * 推送之后仍有一次下发生效：
         *   - 0s：立即下发（覆盖「本应用晚于桌面启动」的常见情形）
         *   - 15s / 45s：兜底重发（覆盖「本应用早于桌面启动 / 桌面稍后重启」的情形）
         */
        private fun scheduleAppVisibility() {
            val handler = Handler(Looper.getMainLooper())
            listOf(0L, 15_000L, 45_000L).forEach { delayMs ->
                handler.postDelayed({
                    if (isSdkReady) configureAppVisibility()
                }, delayMs)
            }
        }

        /**
         * 配置桌面应用可见性（企业交付：眼镜桌面只显示本应用）。
         * 幂等：每次 SDK 就绪后调用，系统侧按最后一次配置生效。
         */
        private fun configureAppVisibility() {
            try {
                if (!GlassSdk.isReady()) {
                    Log.w(TAG, "GlassSdk 未就绪，跳过应用可见性配置")
                    return
                }
                val deviceService = GlassSdk.getGlassDeviceService() ?: run {
                    Log.w(TAG, "DeviceService 不可用，跳过应用可见性配置")
                    return
                }
                val config = GlassAppConfig(
                    // 2026-10-08 实机定论：appList = 要隐藏的内置应用（文档正确）。
                    // 全部传入 → 系统侧生成 isHide=true 清单，桌面隐藏全部内置卡。
                    // （空列表会被系统侧物化为「全部显示」，不能用来表达全隐藏。）
                    appList = GlassAppType.entries.toList(),
                    thirdApps = listOf(ThirdPartyApp(APP_PACKAGE_NAME, APP_DISPLAY_NAME)),
                )
                deviceService.configureAppVisibility(config, object : IAppVisibilityListener.Stub() {
                    override fun onResult(success: Boolean) {
                        Log.d(TAG, "应用可见性配置结果：$success")
                    }
                })
                Log.d(TAG, "已下发应用可见性配置（不显示任何内置应用，仅显示 $APP_DISPLAY_NAME）")
            } catch (e: Exception) {
                Log.e(TAG, "配置应用可见性失败", e)
            }
        }
    }

    private val mediaServer: IMediaServer? get() = sdkMediaServer
    private val asrService: IAsrService? get() = sdkAsrService
    private val ttsService: ITtsService? get() = sdkTtsService
    private val messageServer: IMessageServer? get() = sdkMessageServer
    
    private var isWorking = false
    private var isRecording = false
    private var isInitialized = false
    private var currentSession: WorkSession? = null
    private var voicePlayer: VoicePlayer? = null
    
    private val pendingPhotos = ConcurrentHashMap<String, (Result<File>) -> Unit>()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    
    private var onSessionComplete: ((WorkSession.SessionData) -> Unit)? = null
    private var onWorkTypeChanged: ((String) -> Unit)? = null
    private var onStatusChanged: ((String) -> Unit)? = null
    
    private val workDir: String by lazy {
        val dir = File(context.getExternalFilesDir(null), "work_records")
        if (!dir.exists()) dir.mkdirs()
        dir.absolutePath
    }
    
    private val messageListener = object : IMessageListener.Stub() {
        override fun onTextMessage(message: String?) {
            message?.takeIf(String::isNotBlank)?.let { 
                Log.d(TAG, "收到消息: $it")
                handlePhoneMessage(it)
            }
        }
        override fun onAudioStream(data: ByteArray?) = Unit
        override fun onStreamDataReceived(tag: String?, data: ByteArray?) {
            if (tag == "gdjl.command" && data != null && data.isNotEmpty()) {
                handlePhoneMessage(String(data, Charsets.UTF_8))
            }
        }
    }
    
    private val photoCallback = object : PhotoFileCallback.Stub() {
        override fun getCallbackId(): String = "gdjl-workrecord-${System.currentTimeMillis()}"
        override fun onTakePhoto(path: String?) { completePhoto(path) }
        override fun onTakePhotoV2(path: String?, width: Int, height: Int) { completePhoto(path) }
    }
    
    private fun completePhoto(path: String?) {
        val normalized = path?.takeIf(String::isNotBlank)
        if (normalized == null) {
            pendingPhotos.entries.firstOrNull()?.let { (key, callback) ->
                pendingPhotos.remove(key)
                callback(Result.failure(IllegalStateException("未返回照片路径")))
            }
            return
        }
        val direct = pendingPhotos.remove(normalized)
        val fallback = if (direct == null && pendingPhotos.size == 1) {
            val entry = pendingPhotos.entries.first()
            pendingPhotos.remove(entry.key)
            entry.value
        } else null
        (direct ?: fallback)?.invoke(Result.success(File(normalized)))
    }
    
    fun setVoicePlayer(voicePlayer: VoicePlayer) {
        this.voicePlayer = voicePlayer
    }
    
    fun init() {
        if (isInitialized) return
        ensureInitialized(context)
        registerWhenReady {
            runCatching { sdkMessageServer?.setMessageListener(messageListener) }
            runCatching { sdkMediaServer?.addPhotoCallback(photoCallback) }
            offlineCommandHandler = { word -> handleVoiceCommand(word) }
            isInitialized = true
            mainHandler.post { onStatusChanged?.invoke("服务已就绪") }
            Log.d(TAG, "工作记录服务实例初始化完成")
        }
    }

    private fun handleVoiceCommand(text: String) {
        Log.d(TAG, "处理语音指令: $text")
        when {
            text.contains("开始工作") -> startWorkSession()
            text.contains("完成") -> finishWorkSession()
            text.contains("拍照") -> takePhoto()
            text.contains("录像") -> toggleRecording()
            text.contains("巡视") -> setWorkType("巡视")
            text.contains("验收") -> setWorkType("验收")
            text.contains("旁站") -> setWorkType("旁站")
            text.contains("见证") -> setWorkType("见证")
            text.contains("质量") -> setWorkType("质量")
            text.contains("其他工作") || text.contains("其它工作") -> setWorkType("其他工作")
            text.contains("对") || text.contains("是") || text.contains("正确") -> confirmSuggestion()
            text.contains("不对") || text.contains("不是") || text.contains("改") -> rejectSuggestion()
            else -> {
                // 全局/导航/唤起类指令不是工作内容，不能记成便签——
                // 转给页面语音通道（基类统一处理返回/主菜单/帮助/唤起应答）
                if (NAV_COMMAND_WORDS.any { text.contains(it) }) {
                    Log.i(TAG, "工作指令外的导航词，转页面通道: $text")
                    SpeechRecognizerManager.injectOfflineCommand(text)
                } else {
                    currentSession?.addVoiceNote(text)
                    onStatusChanged?.invoke("语音已记录: $text")
                }
            }
        }
    }
    
    fun startWorkSession() {
        if (isWorking) {
            ttsService?.doSpeechTts("已经在工作模式中")
            return
        }
        currentSession = WorkSession()
        isWorking = true
        startAsr()
        ttsService?.doSpeechTts("已进入工作记录模式")
        onStatusChanged?.invoke("工作模式已启动")
        Log.d(TAG, "工作会话已启动")
    }
    
    fun finishWorkSession() {
        if (!isWorking) {
            runCatching { ttsService?.doSpeechTts("当前没有进行中的工作") }
            return
        }
        currentSession?.let { session ->
            if (!session.hasContent()) {
                runCatching { ttsService?.doSpeechTts("没有记录内容，已退出") }
                cleanUp()
                return
            }
            if (!session.isConfirmed()) {
                runCatching { ttsService?.doSpeechTts("当前记录为待定状态") }
            }
            val sessionData = session.buildSessionData()
            runCatching { onSessionComplete?.invoke(sessionData) }
            runCatching { onStatusChanged?.invoke("工作记录已保存: ${sessionData.workType}") }
            Log.d(TAG, "工作会话已结束")
            sendSessionToPhone(sessionData)
        }
        cleanUp()
    }
    
    fun cancelWorkSession() {
        if (!isWorking) return
        runCatching { ttsService?.doSpeechTts("已取消当前工作") }
        cleanUp()
    }
    
    private fun cleanUp() {
        runCatching { asrService?.stopSpeech() }
            .onFailure { Log.e(TAG, "停止语音识别失败", it) }
        if (isRecording) {
            runCatching { mediaServer?.stopRecord() }
                .onFailure { Log.e(TAG, "停止录像失败", it) }
            isRecording = false
        }
        isWorking = false
        currentSession = null
        runCatching { onStatusChanged?.invoke("已退出工作模式") }
    }
    
    private fun startAsr() {
        asrService?.startSpeech(object : SpeechCallback.Stub() {
            override fun onStart() { Log.d(TAG, "ASR 已启动") }
            override fun onIntermediateVad(content: String?) {}
            override fun onAsrComplete(content: String?) {
                content?.let { 
                    Log.d(TAG, "ASR 结果: $it")
                    handleVoiceCommand(it)
                }
            }
            override fun onError(code: Int) { Log.e(TAG, "ASR 错误: $code") }
            override fun onServiceConnectState(connect: Boolean) {}
            override fun onAsrCompleteWithIntent(content: String?, intent: Int, intentJson: String?) {
                content?.let { handleVoiceCommand(it) }
            }
        })
    }
    
    fun takePhoto() {
        if (!isWorking) {
            ttsService?.doSpeechTts("请先开始工作")
            return
        }
        val timestamp = System.currentTimeMillis()
        val fileName = "photo_${timestamp}.jpg"
        val filePath = "$workDir/$fileName"
        
        filePath.let { 
            pendingPhotos[it] = { result ->
                result.onSuccess { file ->
                    currentSession?.addPhoto(file.absolutePath)
                    ttsService?.doSpeechTts("已拍照")
                    onStatusChanged?.invoke("已拍照: ${file.name}")
                }.onFailure { error ->
                    Log.e(TAG, "拍照失败", error)
                    onStatusChanged?.invoke("拍照失败: ${error.message}")
                }
            }
        }
        
        try {
            mediaServer?.takePhoto(PhotoResolution.RESOLUTION_1080P_Land, filePath)
        } catch (e: Exception) {
            pendingPhotos.remove(filePath)
            Log.e(TAG, "拍照失败", e)
            ttsService?.doSpeechTts("拍照失败")
        }
    }
    
    fun toggleRecording() {
        if (!isWorking) {
            ttsService?.doSpeechTts("请先开始工作")
            return
        }
        if (isRecording) {
            mediaServer?.stopRecord()
            isRecording = false
            ttsService?.doSpeechTts("录像已停止")
            onStatusChanged?.invoke("录像已停止")
        } else {
            val recordConfig = RecordConfig("", 0, false)
            mediaServer?.startRecord(object : VideoCallback.Stub() {
                override fun onStart() { Log.d(TAG, "录像开始") }
                override fun onError() { Log.e(TAG, "录像错误"); isRecording = false }
                override fun onFinish() { Log.d(TAG, "录像完成") }
                override fun onNewFile(startTime: Long, endTime: Long, path: String?, isLast: Boolean) {
                    path?.let {
                        currentSession?.addVideo(it)
                        if (isLast) {
                            ttsService?.doSpeechTts("录像已保存")
                            onStatusChanged?.invoke("录像已保存: ${File(it).name}")
                        }
                    }
                }
                override fun onErrorWithDetail(code: Int, errorMsg: String?) {
                    Log.e(TAG, "录像错误: $code, $errorMsg")
                    isRecording = false
                }
            }, recordConfig)
            isRecording = true
            ttsService?.doSpeechTts("开始录像")
            onStatusChanged?.invoke("开始录像")
        }
    }
    
    fun setWorkType(type: String) {
        currentSession?.setWorkType(type)
        ttsService?.doSpeechTts("已设置为${type}")
        onWorkTypeChanged?.invoke(type)
        onStatusChanged?.invoke("已设置类型: $type")
    }
    
    private fun confirmSuggestion() {
        val suggestedType = currentSession?.getSuggestedType()
        if (suggestedType != null && suggestedType != "待定") {
            currentSession?.setWorkType(suggestedType)
            ttsService?.doSpeechTts("已确认${suggestedType}")
            onWorkTypeChanged?.invoke(suggestedType)
            onStatusChanged?.invoke("已确认类型: $suggestedType")
        } else {
            ttsService?.doSpeechTts("没有待确认的分类")
        }
    }
    
    private fun rejectSuggestion() {
        ttsService?.doSpeechTts("请告诉我正确的类型")
        currentSession?.setSuggestedType("")
        onStatusChanged?.invoke("已拒绝建议，请语音指定类型")
    }
    
    fun getWorkType(): String = currentSession?.getWorkType() ?: "待定"
    fun getPhotoCount(): Int = currentSession?.getPhotoCount() ?: 0
    fun getVideoCount(): Int = currentSession?.getVideoCount() ?: 0
    fun getCurrentSession(): WorkSession? = currentSession
    fun isWorking(): Boolean = isWorking
    
    private fun sendSessionToPhone(sessionData: WorkSession.SessionData) {
        try {
            val json = sessionData.toJson()
            if (json.isNotEmpty()) {
                messageServer?.sendTextMessageByP2P(json)
            }
            Log.d(TAG, "会话数据已发送到手机端")
        } catch (e: Exception) {
            Log.e(TAG, "发送会话数据失败", e)
        }
    }
    
    private fun handlePhoneMessage(msg: String) {
        try {
            val json = JSONObject(msg)
            when (json.optString("type")) {
                "confirm_type" -> {
                    val workType = json.optString("work_type")
                    if (workType.isNotEmpty()) {
                        currentSession?.setWorkType(workType)
                        ttsService?.doSpeechTts("手机端已确认分类为${workType}")
                        onWorkTypeChanged?.invoke(workType)
                        onStatusChanged?.invoke("手机端确认类型: $workType")
                    }
                }
                "ping" -> {
                    val response = JSONObject()
                    response.put("type", "pong")
                    response.put("timestamp", System.currentTimeMillis())
                    messageServer?.sendTextMessageByP2P(response.toString())
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "处理手机消息失败", e)
        }
    }
    
    fun setOnSessionComplete(listener: (WorkSession.SessionData) -> Unit) {
        onSessionComplete = listener
    }
    fun setOnWorkTypeChanged(listener: (String) -> Unit) {
        onWorkTypeChanged = listener
    }
    fun setOnStatusChanged(listener: (String) -> Unit) {
        onStatusChanged = listener
    }
    
    fun release() {
        try {
            if (isRecording) { mediaServer?.stopRecord(); isRecording = false }
            runCatching { asrService?.stopSpeech() }
            runCatching { messageServer?.removeMessageListener(messageListener) }
            runCatching { mediaServer?.removePhotoCallback(photoCallback) }
            offlineCommandHandler = null
            // 注意：不再调用 GlassSdk.release() —— SDK 连接是进程级资产（全程语音唤起依赖），
            // 实例销毁不能断开，进程退出时由系统回收。
            isInitialized = false
            Log.d(TAG, "服务已释放")
        } catch (e: Exception) {
            Log.e(TAG, "释放服务失败", e)
        }
    }
}
