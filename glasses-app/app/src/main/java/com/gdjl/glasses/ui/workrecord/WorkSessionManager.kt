// ui/workrecord/WorkSessionManager.kt
package com.gdjl.glasses.ui.workrecord

import java.io.File
import java.text.SimpleDateFormat
import java.util.*

/**
 * 工作会话管理器
 * 管理一次工作记录会话中的所有数据：照片、视频、语音、分类状态
 */
class WorkSessionManager {
    
    private val photos = mutableListOf<File>()
    private val videos = mutableListOf<File>()
    private val voiceNotes = mutableListOf<String>()
    private var workType: String = "待定"
    private var sessionId: String = UUID.randomUUID().toString()
    private var startTime: Date = Date()
    
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault())
    
    fun createNewSession() {
        sessionId = UUID.randomUUID().toString()
        photos.clear()
        videos.clear()
        voiceNotes.clear()
        workType = "待定"
        startTime = Date()
    }
    
    fun addPhoto(file: File) {
        photos.add(file)
    }
    
    fun addVideo(file: File) {
        videos.add(file)
    }
    
    fun addVoiceNote(text: String) {
        if (text.isNotBlank()) {
            voiceNotes.add(text)
        }
    }
    
    fun setWorkType(type: String) {
        workType = type
    }
    
    fun getWorkType(): String = workType
    
    fun getPhotoCount(): Int = photos.size
    
    fun getVideoCount(): Int = videos.size
    
    fun getVoiceNotes(): List<String> = voiceNotes
    
    fun getAllMediaCount(): Int = photos.size + videos.size
    
    fun getVoiceSummary(): String {
        return voiceNotes.take(3).joinToString(" ")
    }
    
    fun getPhotos(): List<File> = photos.toList()
    
    fun getVideos(): List<File> = videos.toList()
    
    fun getSessionId(): String = sessionId
    
    fun getStartTime(): Date = startTime
    
    fun hasContent(): Boolean = photos.isNotEmpty() || videos.isNotEmpty() || voiceNotes.isNotEmpty()
    
    fun buildWorkRecord(): WorkRecordData {
        return WorkRecordData(
            sessionId = sessionId,
            type = workType,
            photos = photos.map { it.absolutePath },
            videos = videos.map { it.absolutePath },
            voiceNotes = voiceNotes.toList(),
            startTime = dateFormat.format(startTime),
            endTime = dateFormat.format(Date())
        )
    }
    
    data class WorkRecordData(
        val sessionId: String,
        val type: String,
        val photos: List<String>,
        val videos: List<String>,
        val voiceNotes: List<String>,
        val startTime: String,
        val endTime: String
    )
}
