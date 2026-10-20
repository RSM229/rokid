package com.gdjl.glasses.data

import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.*

/**
 * 工作会话
 * 管理一次工作记录会话中的所有数据
 */
class WorkSession {
    
    val sessionId: String = UUID.randomUUID().toString()
    private val photos = mutableListOf<String>()
    private val videos = mutableListOf<String>()
    private val voiceNotes = mutableListOf<String>()
    private var workType: String = "待定"
    private var suggestedType: String? = null
    private var isConfirmed: Boolean = false
    
    val createdAt: String = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault()).format(Date())
    
    fun addPhoto(path: String) {
        photos.add(path)
    }
    
    fun addVideo(path: String) {
        videos.add(path)
    }
    
    fun addVoiceNote(text: String) {
        if (text.isNotBlank()) {
            voiceNotes.add(text)
        }
    }
    
    fun setWorkType(type: String) {
        workType = type
        isConfirmed = true
    }
    
    fun getWorkType(): String = workType
    
    fun setSuggestedType(type: String) {
        suggestedType = type
    }
    
    fun getSuggestedType(): String? = suggestedType
    
    fun isConfirmed(): Boolean = isConfirmed
    
    fun getPhotoCount(): Int = photos.size
    
    fun getVideoCount(): Int = videos.size
    
    fun getVoiceNotes(): List<String> = voiceNotes
    
    fun getVoiceSummary(): String {
        return voiceNotes.take(3).joinToString(" ")
    }
    
    fun hasContent(): Boolean {
        return photos.isNotEmpty() || videos.isNotEmpty() || voiceNotes.isNotEmpty()
    }
    
    fun getPhotos(): List<String> = photos.toList()
    
    fun getVideos(): List<String> = videos.toList()
    
    fun buildSessionData(): SessionData {
        return SessionData(
            sessionId = sessionId,
            photos = photos.toList(),
            videos = videos.toList(),
            voiceNotes = voiceNotes.toList(),
            workType = workType,
            createdAt = createdAt
        )
    }
    
    fun clear() {
        photos.clear()
        videos.clear()
        voiceNotes.clear()
        workType = "待定"
        suggestedType = null
        isConfirmed = false
    }
    
    data class SessionData(
        val sessionId: String,
        val photos: List<String>,
        val videos: List<String>,
        val voiceNotes: List<String>,
        val workType: String,
        val createdAt: String
    ) {
        fun toJson(): String {
            val json = JSONObject()
            json.put("type", "session_complete")
            json.put("session_id", sessionId)
            json.put("work_type", workType)
            json.put("photo_count", photos.size)
            json.put("video_count", videos.size)
            json.put("voice_summary", voiceNotes.joinToString("|"))
            json.put("created_at", createdAt)
            return json.toString()
        }
    }
}
