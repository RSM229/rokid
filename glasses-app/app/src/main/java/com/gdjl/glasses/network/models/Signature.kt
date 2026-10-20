// 创建日期: 2026-07-18
package com.gdjl.glasses.network.models

import com.google.gson.annotations.SerializedName

/**
 * 电子签章
 *
 * 对应后端 `GET /signature/list`、`POST /signature/upload`、
 * `DELETE /signature/{id}`、`POST /document/{id}/sign` 接口。
 */
data class Signature(
    val id: String,
    val name: String? = null,
    @SerializedName(value = "image_path", alternate = ["url"])
    val url: String? = null,
    val createdAt: String? = null,
    val userId: String? = null,
)

/**
 * 签章上传响应
 */
data class SignatureUploadResponse(
    val id: String,
    @SerializedName(value = "image_path", alternate = ["url"])
    val url: String? = null,
)

/**
 * 文书签章请求
 *
 * @param signatureId 签章 ID
 * @param position 签章位置（如 "bottom_right"）
 */
data class DocumentSignRequest(
    val signatureId: String,
    val position: String? = "bottom_right",
    val password: String = "",
)

/**
 * 签章删除请求（需密码确认）
 *
 * @param password 用户登录密码二次确认
 */
data class SignatureDeleteRequest(
    val password: String,
)
