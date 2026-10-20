// 创建日期: 2026-07-18
package com.gdjl.glasses.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.gdjl.glasses.data.local.entities.CachedNotificationEntity
import kotlinx.coroutines.flow.Flow

/**
 * 通知缓存 DAO
 */
@Dao
interface CachedNotificationDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertAll(items: List<CachedNotificationEntity>)

    @Query("SELECT * FROM cached_notification ORDER BY created_at DESC")
    fun observeAll(): Flow<List<CachedNotificationEntity>>

    @Query("SELECT * FROM cached_notification ORDER BY created_at DESC")
    suspend fun listAll(): List<CachedNotificationEntity>

    @Query("SELECT COUNT(*) FROM cached_notification WHERE read = 0")
    suspend fun countUnread(): Int

    @Query("UPDATE cached_notification SET read = 1 WHERE id = :id")
    suspend fun markRead(id: Long)

    @Query("DELETE FROM cached_notification")
    suspend fun clear()
}
