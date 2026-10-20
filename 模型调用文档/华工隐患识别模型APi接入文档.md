# AI Center 服务空间 API 接入文档

## 服务空间信息

- 服务空间名称：广东监理
- 服务空间编码：t2605-gdjl
- 服务空间 ID：65312e3f-7f62-4595-a82c-31d0acde616b
- 空间类型：外部试用
- API Base：https://ai.sentripy.cn/api/v1
- 认证方式：Authorization: Bearer <请替换为单独发送的访问令牌>
- 访问身份：t2505-gzjl-access
- 当前令牌到期时间：2027/05/17 23:59:59
- 当前令牌最近使用时间：2026/05/21 13:52:48
- 令牌占位符：<请替换为单独发送的访问令牌>

## 重要说明

- API 文档不嵌入明文 token；请将占位符替换为单独发送的真实 token。
- 更新访问令牌后，旧 token 立即失效。
- 每次调用都必须带 org_id、environment 和 Bearer token。
- 若能力输入包含文件，请改用 invoke-upload 接口。
- 所有能力都支持同步调用；只有启用“允许异步”的能力才会开放异步提交。
- 每个能力都有独立 timeout_seconds；调用方应按能力超时阈值设置客户端等待、轮询与告警策略。
- 当前异步主状态统一查询 AI Center 的 /runs/{run_id}。
- 输出中的 artifact 下载链接自带 expires_at，调用方应在到期前拉取。
- external_trial / external_service 的同步响应会裁剪平台包装字段：tool 返回 result，workflow 优先返回 workflow_runner_output，为空时回退 tool_result。
- 异步链路中的 progress 回调存在 accepted/ignored 语义；ignored（例如 terminal_status、stale_sequence）是幂等保护，不代表接口故障。

## 能力详细用法

### 1. 现场风险识别

#### 调用总览

- 能力编码：hazard_vlm_detection
- 能力类型：工具
- 能力环境：test
- 授权有效期：2027/05/17 23:59:59
- 说明：利用视觉语言模型分析现场的风险
- 调用地址：https://ai.sentripy.cn/api/v1/tools/hazard_vlm_detection/invoke-upload
- 调用方式：multipart/form-data 文件上传
- 超时时限：30 秒
- 允许异步：否
- 异步调用方式：未开放异步提交
- 执行模式：同步调用（异步提交未开放）
- 文件输入字段：image

#### 同步调用

##### 参数字段说明
本能力没有额外 JSON 参数字段。

##### 文件字段说明
| 字段 | 类型 | 必填 | 说明 | 示例 | 扩展名约束 |
| --- | --- | --- | --- | --- | --- |
| image | file | 是 | - | @/path/to/file | .jpg, .jpeg, .png, .webp |

##### curl 示例
```bash
curl -X POST "https://ai.sentripy.cn/api/v1/tools/hazard_vlm_detection/invoke-upload" \
  -H "Authorization: Bearer <请替换为单独发送的访问令牌>" \
  -F "org_id=65312e3f-7f62-4595-a82c-31d0acde616b" \
  -F "environment=test" \
  -F 'parameters={}' \
  -F "input_name=image" \
  -F "file=@image.jpg"
```

##### Python 示例
```python
import json
import requests

url = "https://ai.sentripy.cn/api/v1/tools/hazard_vlm_detection/invoke-upload"
token = "<请替换为单独发送的访问令牌>"
data = [
    ("org_id", "65312e3f-7f62-4595-a82c-31d0acde616b"),
    ("environment", "test"),
    ("parameters", json.dumps({})),
    ("input_name", "image"),
]
files = [
    ("file", open("image.jpg", "rb")),
]

try:
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        data=data,
        files=files,
    )
    response.raise_for_status()
    print(response.json())
finally:
    for _, file_handle in files:
        file_handle.close()
```

#### 数据结构

##### 输出字段说明
| 字段 | 类型 | 必填 | 说明 | 示例 | 扩展名约束 |
| --- | --- | --- | --- | --- | --- |
| evidence | array<string> | 是 | - | ["string"] | - |
| top1_risk | string | 是 | 风险状态为信息不足时，建议以疑似开头 | string | - |
| risk_status | string | 是 | - | 存在隐患 | - |
| uncertainty | string | 是 | - | string | - |
| risk_category | string | 是 | - | 安全 | - |
| scene_related | string | 是 | - | 施工场景 | - |
| recheck_suggestion | array<string> | 是 | - | ["string"] | - |

##### 输入 Schema
```json
{
  "type": "object",
  "required": [
    "image"
  ],
  "properties": {
    "image": {
      "type": "string",
      "format": "binary",
      "x-upload": {
        "allowed_extensions": [
          ".jpg",
          ".jpeg",
          ".png",
          ".webp"
        ]
      }
    }
  },
  "additionalProperties": false
}
```

##### 输出 Schema
```json
{
  "type": "object",
  "required": [
    "scene_related",
    "risk_status",
    "risk_category",
    "top1_risk",
    "evidence",
    "uncertainty",
    "recheck_suggestion"
  ],
  "properties": {
    "evidence": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "top1_risk": {
      "type": "string",
      "description": "风险状态为信息不足时，建议以疑似开头"
    },
    "risk_status": {
      "enum": [
        "存在隐患",
        "不存在隐患",
        "信息不足",
        "不适用"
      ],
      "type": "string"
    },
    "uncertainty": {
      "type": "string"
    },
    "risk_category": {
      "enum": [
        "安全",
        "质量",
        "文明施工",
        "不适用"
      ],
      "type": "string"
    },
    "scene_related": {
      "enum": [
        "施工场景",
        "非施工场景",
        "与施工场景无关"
      ],
      "type": "string"
    },
    "recheck_suggestion": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  },
  "additionalProperties": false
}
```

##### 输出示例
```json
{
  "evidence": [
    "string"
  ],
  "top1_risk": "string",
  "risk_status": "存在隐患",
  "uncertainty": "string",
  "risk_category": "安全",
  "scene_related": "施工场景",
  "recheck_suggestion": [
    "string"
  ]
}
```