package com.gdjl.phone.ui.feature

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import com.gdjl.phone.R
import com.gdjl.phone.activities.customCMD.CustomCmdViewModel
import com.gdjl.phone.session.CxrSessionType
import com.gdjl.phone.ui.components.PRIMARY_BUTTON_WIDTH
import com.gdjl.phone.ui.components.statusLine
import com.gdjl.phone.ui.components.statusLines
import com.gdjl.phone.ui.design.DevSampleScaffold
import com.gdjl.phone.ui.design.DevStatusCard
import com.gdjl.phone.ui.design.PrerequisiteHint
import com.gdjl.phone.ui.design.SceneSectionTitle
import com.gdjl.phone.ui.design.SessionTypeBanner

@Composable
fun CustomCmdScreen(
    viewModel: CustomCmdViewModel,
    sessionType: CxrSessionType,
    onBack: () -> Unit
) {
    val tokenGot by viewModel.tokenGot.collectAsState()
    val available by viewModel.available.collectAsState()
    val ready by viewModel.ready.collectAsState()
    val status by viewModel.status.collectAsState()
    val entryLabel by viewModel.entryLabel.collectAsState()
    val from by viewModel.from.collectAsState()

    DevSampleScaffold(
        title = stringResource(id = R.string.screen_title_custom_cmd),
        subtitle = stringResource(id = R.string.custom_cmd_subtitle),
        showBack = true,
        onBack = onBack
    ) {
        SessionTypeBanner(sessionType = sessionType)
        DevStatusCard(
            title = stringResource(id = R.string.status_panel_title),
            lines = statusLines(
                entryLabel,
                statusLine(R.string.common_status_prefix, status)
            )
        )
        Text(
            text = stringResource(id = R.string.feature_dev_hint_cmd),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        DevStatusCard(
            title = stringResource(id = R.string.custom_cmd_feedback),
            lines = statusLines(stringResource(id = R.string.custom_cmd_from_glasses, from))
        )
        SceneSectionTitle(stringResource(id = R.string.common_actions))
        if (!tokenGot) {
            PrerequisiteHint(stringResource(id = R.string.token_required_hint))
        } else if (!available) {
            PrerequisiteHint(stringResource(id = R.string.custom_cmd_only_custom_app))
        } else if (!ready) {
            PrerequisiteHint(stringResource(id = R.string.custom_cmd_connected_hint))
        }
        Button(
            modifier = Modifier.fillMaxWidth(PRIMARY_BUTTON_WIDTH),
            onClick = { viewModel.sendMessage() },
            enabled = tokenGot && available && ready
        ) { Text(stringResource(id = R.string.custom_cmd_send)) }
    }
}
