// 创建日期: 2026-07-18
package com.gdjl.glasses.data.local

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.gdjl.glasses.data.local.entities.PendingUploadEntity
import kotlinx.coroutines.flow.Flow

/**
 * 待上传队列 DAO
 */
@Dao
interface PendingUploadDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(entity: PendingUploadEntity): Long

    @Update
    suspend fun update(entity: PendingUploadEntity)

    @Query("SELECT * FROM pending_upload WHERE status = 'pending' ORDER BY created_at ASC")
    suspend fun listPending(): List<PendingUploadEntity>

    @Query("SELECT * FROM pending_upload WHERE status = 'pending' ORDER BY created_at ASC")
    fun observePending(): Flow<List<PendingUploadEntity>>

    @Query("SELECT COUNT(*) FROM pending_upload WHERE status = 'pending'")
    suspend fun countPending(): Int

    @Query("DELETE FROM pending_upload WHERE id = :id")
    suspend fun deleteById(id: Long)

    @Query("UPDATE pending_upload SET retry_count = retry_count + 1 WHERE id = :id")
    suspend fun incrementRetry(id: Long)

    @Query("UPDATE pending_upload SET status = 'failed' WHERE id = :id")
    suspend fun markFailed(id: Long)
}
