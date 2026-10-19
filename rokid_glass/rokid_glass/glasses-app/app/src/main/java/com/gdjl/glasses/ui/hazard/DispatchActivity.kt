package com.gdjl.glasses.ui.hazard

import android.app.AlertDialog
import android.os.Bundle
import android.view.KeyEvent
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.*
import com.gdjl.glasses.R
import com.gdjl.glasses.network.models.DispatchStatus
import com.gdjl.glasses.network.models.DispatchTask
import com.gdjl.glasses.utils.DispatchSyncHelper
import com.gdjl.glasses.ui.BaseBusinessActivity
import com.gdjl.glasses.voice.CnIndexParser
import java.text.SimpleDateFormat
import java.util.*

class DispatchActivity : BaseBusinessActivity() {

    companion object {
        private const val TAG = "DispatchActivity"
    }

    private lateinit var gridView: GridView
    private lateinit var adapter: DispatchAdapter
    private lateinit var tvEmpty: TextView
    private lateinit var btnCreateDispatch: Button

    private val dispatchTasks = mutableListOf<DispatchTask>()
    private var doubleBackCount = 0
    private var lastBackTime = 0L

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_dispatch)

        gridView = findViewById(R.id.grid_dispatch_tasks)
        tvEmpty = findViewById(R.id.tv_empty)
        btnCreateDispatch = findViewById(R.id.btn_create_dispatch)
        findViewById<TextView>(R.id.tv_title).text = "派单管理"

        gridView.isFocusable = false
        gridView.isFocusableInTouchMode = false
        window.decorView.isFocusable = true
        window.decorView.isFocusableInTouchMode = true
        window.decorView.requestFocus()

        initMockData()

        adapter = DispatchAdapter(dispatchTasks)
        gridView.adapter = adapter

        gridView.setOnItemClickListener { _, _, position, _ ->
            val task = dispatchTasks[position]
            showTaskDetail(task)
        }

        btnCreateDispatch.setOnClickListener {
        findViewById<View>(R.id.btn_back).setOnClickListener {
            finish()
        }
            showCreateDispatchDialog()
        findViewById<View>(R.id.btn_back).setOnClickListener {
            finish()
        }
        }
        findViewById<View>(R.id.btn_back).setOnClickListener {
            finish()
        }

        enablePageVoice()
        updateEmptyView()
    }

    // ---------------- 全程语音 ----------------

    override fun voiceHint(): String =
        "共${dispatchTasks.size}条派单。说第几个查看详情；说新建派单创建；说播报列表听取概况"

    override fun onPageVoiceCommand(text: String): Boolean {
        // 「第 N 个」查看详情
        if (text.contains("第") || text.contains("打开") || text.contains("查看") || text.contains("详情")) {
            val index = CnIndexParser.parse(text)
            if (index != null) {
                val task = dispatchTasks.getOrNull(index)
                if (task != null) {
                    speak("第${index + 1}个派单：${task.hazardName}，负责人${task.assignee}")
                    showTaskDetail(task)
                } else {
                    speak("没有第${index + 1}个，当前共${dispatchTasks.size}条")
                }
                return true
            }
        }
        if (text.contains("新建") || text.contains("创建")) {
            speak("请填写派单信息")
            showCreateDispatchDialog()
            return true
        }
        if (text.contains("播报") || text.contains("概况") || text.contains("朗读")) {
            if (dispatchTasks.isEmpty()) {
                speak("当前没有派单")
            } else {
                val summary = dispatchTasks.take(3).joinToString("；") { t ->
                    "${t.hazardName}，负责人${t.assignee}"
                }
                speak("共${dispatchTasks.size}条派单。最新：$summary")
            }
            return true
        }
        return false
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            val currentTime = System.currentTimeMillis()
            if (currentTime - lastBackTime < 500) {
                doubleBackCount++
                if (doubleBackCount >= 2) {
                    finish()
                    return true
                }
            } else {
                doubleBackCount = 1
            }
            lastBackTime = currentTime
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    private fun initMockData() {
        val now = System.currentTimeMillis()
        val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())

        dispatchTasks.addAll(listOf(
            DispatchTask(
                id = "T001",
                hazardId = "H001",
                hazardName = "未佩戴安全帽",
                riskLevel = "高",
                assignee = "张三",
                assigneePhone = "138****1234",
                deadline = dateFormat.format(Date(now + 86400000 * 2)),
                status = DispatchStatus.PENDING,
                description = "施工现场发现工人未佩戴安全帽",
                createdAt = dateFormat.format(Date(now - 3600000)),
                updatedAt = dateFormat.format(Date(now - 1800000)),
                images = listOf()
            ),
            DispatchTask(
                id = "T002",
                hazardId = "H002",
                hazardName = "电缆裸露",
                riskLevel = "重大",
                assignee = "李四",
                assigneePhone = "139****5678",
                deadline = dateFormat.format(Date(now + 86400000 * 1)),
                status = DispatchStatus.PROCESSING,
                description = "配电箱电缆裸露，有触电风险",
                createdAt = dateFormat.format(Date(now - 7200000)),
                updatedAt = dateFormat.format(Date(now - 3600000)),
                images = listOf()
            )
        ))
    }

    private fun showCreateDispatchDialog() {
        val dialogView = layoutInflater.inflate(R.layout.dialog_create_dispatch, null)
        val etAssignee = dialogView.findViewById<EditText>(R.id.et_assignee)
        val etPhone = dialogView.findViewById<EditText>(R.id.et_phone)
        val etDeadline = dialogView.findViewById<EditText>(R.id.et_deadline)
        val etDescription = dialogView.findViewById<EditText>(R.id.et_description)
        val btnConfirm = dialogView.findViewById<Button>(R.id.btn_confirm)
        val btnCancel = dialogView.findViewById<Button>(R.id.btn_cancel)

        val dialog = AlertDialog.Builder(this)
            .setTitle("创建派单")
            .setView(dialogView)
            .setCancelable(false)
            .create()

        btnCancel.setOnClickListener { dialog.dismiss() }
        btnConfirm.setOnClickListener {
            val assignee = etAssignee.text.toString()
            val phone = etPhone.text.toString()
            val deadline = etDeadline.text.toString()
            val description = etDescription.text.toString()

            if (assignee.isEmpty() || deadline.isEmpty()) {
                Toast.makeText(this, "请填写负责人和截止日期", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault())
            val newTask = DispatchTask(
                id = "T${String.format("%03d", dispatchTasks.size + 1)}",
                hazardId = "H${String.format("%03d", dispatchTasks.size + 1)}",
                hazardName = "新隐患",
                riskLevel = "中",
                assignee = assignee,
                assigneePhone = phone,
                deadline = deadline,
                status = DispatchStatus.PENDING,
                description = description.ifEmpty { "待处理隐患" },
                createdAt = dateFormat.format(Date()),
                updatedAt = dateFormat.format(Date()),
                images = listOf()
            )

            dispatchTasks.add(0, newTask)
            adapter.notifyDataSetChanged()
            updateEmptyView()
            dialog.dismiss()
            Toast.makeText(this, "✅ 派单成功！", Toast.LENGTH_SHORT).show()
                // 同步到手机端

                DispatchSyncHelper.syncToPhone(newTask)
        }

        dialog.show()
    }

    private fun showTaskDetail(task: DispatchTask) {
        val statusMap = mapOf(
            DispatchStatus.PENDING to "⏳ 待派单",
            DispatchStatus.ASSIGNED to "📋 已派单",
            DispatchStatus.PROCESSING to "🔄 处理中",
            DispatchStatus.COMPLETED to "✅ 已完成",
            DispatchStatus.REJECTED to "❌ 已驳回",
            DispatchStatus.CLOSED to "🔒 已闭环"
        )

        val dialogView = layoutInflater.inflate(R.layout.dialog_task_detail, null)
        dialogView.findViewById<TextView>(R.id.tv_task_hazard).text = "🔴 ${task.hazardName}"
        dialogView.findViewById<TextView>(R.id.tv_task_risk).text = "风险: ${task.riskLevel}"
        dialogView.findViewById<TextView>(R.id.tv_task_assignee).text = "👤 ${task.assignee}"
        dialogView.findViewById<TextView>(R.id.tv_task_deadline).text = "⏰ ${task.deadline}"
        dialogView.findViewById<TextView>(R.id.tv_task_status).text = statusMap[task.status] ?: "未知"
        dialogView.findViewById<TextView>(R.id.tv_task_description).text = task.description
        dialogView.findViewById<TextView>(R.id.tv_task_created).text = "创建: ${task.createdAt}"

        dialogView.findViewById<Button>(R.id.btn_update_status).setOnClickListener {
            showStatusUpdateDialog(task)
        }

        AlertDialog.Builder(this)
            .setTitle("派单详情")
            .setView(dialogView)
            .setPositiveButton("关闭", null)
            .show()
    }

    private fun showStatusUpdateDialog(task: DispatchTask) {
        val statuses = listOf(
            DispatchStatus.ASSIGNED to "📋 已派单",
            DispatchStatus.PROCESSING to "🔄 处理中",
            DispatchStatus.COMPLETED to "✅ 已完成",
            DispatchStatus.REJECTED to "❌ 已驳回",
            DispatchStatus.CLOSED to "🔒 已闭环"
        )

        val items = statuses.map { it.second }.toTypedArray()

        AlertDialog.Builder(this)
            .setTitle("更新状态")
            .setItems(items) { _, which ->
                val newStatus = statuses[which].first
                val index = dispatchTasks.indexOfFirst { it.id == task.id }
                if (index != -1) {
                    dispatchTasks[index] = task.copy(
                        status = newStatus,
                        updatedAt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date())
                    )
                    adapter.notifyDataSetChanged()
                    Toast.makeText(this, "✅ 状态已更新", Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun updateEmptyView() {
        tvEmpty.visibility = if (dispatchTasks.isEmpty()) View.VISIBLE else View.GONE
        gridView.visibility = if (dispatchTasks.isEmpty()) View.GONE else View.VISIBLE
    }

    inner class DispatchAdapter(private val tasks: List<DispatchTask>) : BaseAdapter() {
        override fun getCount(): Int = tasks.size
        override fun getItem(position: Int): Any = tasks[position]
        override fun getItemId(position: Int): Long = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val view = convertView ?: LayoutInflater.from(this@DispatchActivity)
                .inflate(R.layout.item_dispatch_task, parent, false)

            val task = tasks[position]
            view.findViewById<TextView>(R.id.tv_dispatch_hazard).text = task.hazardName
            view.findViewById<TextView>(R.id.tv_dispatch_risk).text = "风险: ${task.riskLevel}"
            view.findViewById<TextView>(R.id.tv_dispatch_assignee).text = "👤 ${task.assignee}"
            view.findViewById<TextView>(R.id.tv_dispatch_deadline).text = "⏰ ${task.deadline}"

            val statusMap = mapOf(
                DispatchStatus.PENDING to "待派单",
                DispatchStatus.ASSIGNED to "已派单",
                DispatchStatus.PROCESSING to "处理中",
                DispatchStatus.COMPLETED to "已完成",
                DispatchStatus.REJECTED to "已驳回",
                DispatchStatus.CLOSED to "已闭环"
            )
            view.findViewById<TextView>(R.id.tv_dispatch_status).text = statusMap[task.status] ?: "未知"
            return view
        }
    }
}
