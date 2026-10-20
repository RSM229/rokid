// 创建日期: 2026-07-18
package com.gdjl.glasses.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.gdjl.glasses.data.local.entities.CachedNotificationEntity
import com.gdjl.glasses.data.local.entities.PendingUploadEntity

/**
 * 应用 Room 数据库
 *
 * 持有：
 * - [PendingUploadEntity]：待上传队列（图片离线缓存）
 * - [CachedNotificationEntity]：通知缓存
 *
 * version 1，后续字段变更需提供 Migration。
 */
@Database(
    entities = [PendingUploadEntity::class, CachedNotificationEntity::class],
    version = 1,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun pendingUploadDao(): PendingUploadDao
    abstract fun cachedNotificationDao(): CachedNotificationDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun get(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "gdjl_glasses.db"
                )
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { INSTANCE = it }
            }
        }
    }
}
