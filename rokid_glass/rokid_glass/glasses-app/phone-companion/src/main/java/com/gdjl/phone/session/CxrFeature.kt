package com.gdjl.phone.session

import androidx.annotation.StringRes
import com.gdjl.phone.R

enum class CxrFeature(@StringRes val labelRes: Int, val route: String) {
    AUDIO(R.string.screen_title_audio, SceneRoutes.AUDIO),
    PHOTO(R.string.screen_title_photo, SceneRoutes.PHOTO),
    CUSTOM_CMD(R.string.screen_title_custom_cmd, SceneRoutes.CMD),
}
