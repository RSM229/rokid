package com.gdjl.phone.app

import android.app.Application
import android.util.Log
import com.gdjl.phone.service.MessageReceiverService
import com.rokid.cxr.link.CXRLink
import com.rokid.security.phone.sdk.api.PSecuritySDK
import com.rokid.security.phone.sdk.base.data.EngineParam
import com.rokid.security.phone.sdk.base.data.EnvType
import com.rokid.security.phone.sdk.base.data.NetServiceType
import com.rokid.security.phone.sdk.base.data.UserAuthInfo

class CXRLApplication : Application() {

    companion object {
        lateinit var instance: CXRLApplication
        private const val TAG = "CXRLApplication"
    }

    var sharedLink: CXRLink? = null
    var isSessionReady: Boolean = false
    
    private lateinit var messageReceiver: MessageReceiverService

    fun requireReadyLink(): CXRLink {
        check(sharedLink != null && isSessionReady) {
            "CXRLink session not ready: complete configCXRSession and connect first"
        }
        return sharedLink!!
    }

    fun resetSession() {
        sharedLink = null
        isSessionReady = false
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        
        // 初始化手机端 SDK
        initSecuritySDK()
        
        // 初始化消息接收服务
        messageReceiver = MessageReceiverService()
        messageReceiver.register()
    }
    
    private fun initSecuritySDK() {
        try {
            val clientIds = arrayListOf("SecurityPhone", "GlassSample")
            val userAuthInfo = UserAuthInfo(
                appId = "",  // 需要向 Rokid 申请
                secret = ""  // 需要向 Rokid 申请
            )
            
            val param = EngineParam(
                clientIds = clientIds,
                userAuthInfo = userAuthInfo,
                envType = EnvType.PUBLIC,
                banServiceList = arrayListOf(NetServiceType.ALL)
            )
            
            PSecuritySDK.initSDK(param) { result ->
                if (result.isSuccess) {
                    Log.d(TAG, "✅ Security SDK 初始化成功")
                } else {
                    Log.e(TAG, "❌ Security SDK 初始化失败: ${result}")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ Security SDK 初始化异常", e)
        }
    }
    
    override fun onTerminate() {
        super.onTerminate()
        messageReceiver.unregister()
        try {
            PSecuritySDK.destroySDK()
            Log.d(TAG, "✅ Security SDK 已销毁")
        } catch (e: Exception) {
            Log.e(TAG, "❌ 销毁 Security SDK 失败", e)
        }
    }
}
