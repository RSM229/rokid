package com.gdjl.phone.activities.main

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.tooling.preview.Preview
import com.gdjl.phone.R
import com.gdjl.phone.activities.HazardRecordsActivity
import com.gdjl.phone.activities.WorkRecordsActivity
import com.gdjl.phone.activities.pending.PendingSessionsActivity
import com.gdjl.phone.session.CxrSessionType
import com.gdjl.phone.ui.design.DevSampleScaffold
import com.gdjl.phone.ui.design.DevStatusCard
import com.gdjl.phone.ui.design.FlowStepIndicator
import com.gdjl.phone.ui.theme.RenewCXRLSampleTheme
import androidx.lifecycle.viewmodel.compose.viewModel

class MainActivity : ComponentActivity() {

    private val viewModel by viewModels<MainViewModel>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            RenewCXRLSampleTheme {
                MainScreen(
                    viewModel = viewModel,
                    toCustomView = { viewModel.toSession(this@MainActivity, CxrSessionType.CUSTOM_VIEW) },
                    toCustomApp = { viewModel.toSession(this@MainActivity, CxrSessionType.CUSTOM_APP) },
                    toWorkRecords = {
                        startActivity(Intent(this@MainActivity, WorkRecordsActivity::class.java))
                    },
                    toPendingSessions = {
                        startActivity(Intent(this@MainActivity, PendingSessionsActivity::class.java))
                    },
                    toHazard = {
                        startActivity(Intent(this@MainActivity, HazardRecordsActivity::class.java))
                    }
                )
            }
        }
    }
}

@Composable
fun MainScreen(
    viewModel: MainViewModel,
    toCustomView: () -> Unit = {},
    toCustomApp: () -> Unit = {},
    toWorkRecords: () -> Unit = {},
    toPendingSessions: () -> Unit = {},
    toHazard: () -> Unit = {},
) {
    val info by viewModel.infoText.collectAsState()
    val step by viewModel.currentStep.collectAsState()

    val stepIndex = 2

    DevSampleScaffold(
        title = "CXRLSample",
        subtitle = "场景选择"
    ) {
        FlowStepIndicator(
            steps = listOf("安装", "授权", "场景"),
            currentIndex = stepIndex
        )

        DevStatusCard(
            title = "准备状态",
            lines = listOf(
                "✅ 已就绪",
                "✅ 已授权",
                info
            )
        )

        Text(
            text = "请选择功能：",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(vertical = 8.dp)
        )

        SceneSelectCard(
            title = "🎨 Custom View 场景",
            features = "能力: 音频、拍照",
            onClick = toCustomView
        )

        SceneSelectCard(
            title = "📱 Custom App 场景",
            features = "能力: 音频、拍照、自定义指令",
            onClick = toCustomApp
        )

        SceneSelectCard(
            title = "📋 工作记录",
            features = "查看所有工作记录",
            onClick = toWorkRecords
        )

        SceneSelectCard(
            title = "⏳ 待定记录",
            features = "查看眼镜端上传的待定工作记录，确认分类",
            onClick = toPendingSessions
        )

        SceneSelectCard(
            title = "🔴 安全隐患",
            features = "查看眼镜端上传的隐患记录",
            onClick = toHazard
        )
    }
}

@Composable
private fun SceneSelectCard(
    title: String,
    features: String,
    onClick: () -> Unit
) {
    ElevatedCard(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp), onClick = onClick) {
        Column(modifier = Modifier.fillMaxWidth().padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(
                features,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
fun MainScreenPreview() {
    RenewCXRLSampleTheme {
        MainScreen(viewModel = viewModel { MainViewModel() })
    }
}
