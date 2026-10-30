"""全局配置"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent.parent


def _resolve_knowledge_base_path() -> str:
    # 仓库根目录下的"知识库"（本地开发默认路径）
    repo_root = BACKEND_DIR.parent / "知识库"
    if repo_root.exists() and any(repo_root.iterdir()):
        return str(repo_root)
    # 项目自带（与 app 包同级）的"知识库"，兼容容器 bind-mount / 镜像部署
    nested = BACKEND_DIR / "知识库"
    if nested.exists() and any(nested.iterdir()):
        return str(nested)
    return str(repo_root)


# 仓库内公开的占位密钥。一旦它在生产生效，JWT 即可被任意第三方伪造
# （payload 全公开、算法已知），等同于认证完全失守，故单列常量便于自检比对。
STOCK_SECRET_KEY = "gdjl-supervision-secret-change-in-production"


class Settings(BaseSettings):
    # 记录日志气象适配器：未配置时明确待补填，不虚构气象值。
    weather_forecast_url: str = ""
    weather_archive_url: str = ""
    weather_api_key: str = ""
    model_config = SettingsConfigDict(
        # 始终读取后端目录内的配置文件，避免从项目根目录、systemd 或容器
        # 启动时因当前工作目录不同而丢失默认模型密钥。
        env_file=os.getenv("GDJL_ENV_FILE", str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # 服务配置
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    database_echo: bool = os.getenv("DATABASE_ECHO", "false").lower() == "true"
    strict_production_config: bool = (
        os.getenv("STRICT_PRODUCTION_CONFIG", "true").lower() == "true"
    )

    # 周期文书自动生成（监理日志 / 周报 / 月报）
    # 到点后由后台守护遍历所有项目生成。生成端 force=False：同周期只保留一份，
    # 且不会覆盖客户端已复核或编辑过的草稿。
    daily_doc_auto: bool = os.getenv("DAILY_DOC_AUTO", "true").lower() == "true"
    daily_doc_hour: int = int(os.getenv("DAILY_DOC_HOUR", "8"))
    daily_doc_minute: int = int(os.getenv("DAILY_DOC_MINUTE", "0"))
    # 生成"哪一天"的文书：运行日往回推 N 天。默认 1 —— 早上 8 点出稿时，
    # 要写的是**昨天**那一整天的日志（当日下班前补录的记录才不会被漏掉）。
    daily_doc_offset_days: int = int(os.getenv("DAILY_DOC_OFFSET_DAYS", "1"))
    # 启动后延迟多久开始判定，避开启动时的初始化风暴
    daily_doc_initial_delay: float = float(
        os.getenv("DAILY_DOC_INITIAL_DELAY", "120")
    )
    # 周报/月报是否走 LLM 润色（失败自动降级为规则汇总）
    daily_doc_use_llm: bool = os.getenv("DAILY_DOC_USE_LLM", "true").lower() == "true"
    secret_key: str = os.getenv("SECRET_KEY", STOCK_SECRET_KEY)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    allow_public_registration: bool = (
        os.getenv("ALLOW_PUBLIC_REGISTRATION", "false").lower() == "true"
    )
    # 微信自助注册（路径 A）：小程序 wx.login → 一键注册，零短信成本。
    # 与 allow_public_registration（手机号+短信）并列；A+B 方案默认开启前者、
    # 关闭后者。生产 .env 不填腾讯云密钥 → 短信走 mock = 零费用。
    allow_wechat_registration: bool = (
        os.getenv("ALLOW_WECHAT_REGISTRATION", "true").lower() == "true"
    )
    # 公开注册只能授予白名单内角色；项目绑定和角色提升由管理员完成。
    # Step 1：角色值已 key 化为英文，默认白名单随之改英文（原值「监理员」）。
    public_registration_roles: str = os.getenv(
        "PUBLIC_REGISTRATION_ROLES", "supervisor"
    )

    # CORS 白名单：生产环境应配置为具体域名，逗号分隔
    # 开发模式（debug=True）默认允许所有来源；生产模式必须通过环境变量指定
    cors_allowed_origins: str = os.getenv("CORS_ALLOWED_ORIGINS", "")

    # AI Center 配置（从环境变量读取，避免明文泄露）
    ai_center_base_url: str = os.getenv("AI_CENTER_BASE_URL", "https://ai.sentripy.cn/api/v1")
    ai_center_token: str = os.getenv("AI_CENTER_TOKEN", "")
    ai_center_org_id: str = os.getenv("AI_CENTER_ORG_ID", "65312e3f-7f62-4595-a82c-31d0acde616b")
    ai_center_environment: str = os.getenv("AI_CENTER_ENVIRONMENT", "test")

    # 华工大模型配置（隐患识别 VLM）
    # key/url 存放在 模型调用key.txt 与 模型调用文档.md 中，由运维导入环境变量
    huagong_vlm_url: str = os.getenv(
        "HUAGONG_VLM_URL", "https://ai.sentripy.cn/api/v1/tools/hazard_vlm_detection/invoke-upload"
    )
    huagong_vlm_token: str = os.getenv("HUAGONG_VLM_TOKEN", ai_center_token)

    # 模型配置默认值。真实密钥只允许出现在服务端环境变量或数据库密文中。
    # MODEL_CONFIG_ENCRYPTION_KEY 用于加密数据库内的模型密钥，生产环境必须独立配置。
    model_config_encryption_key: str = os.getenv(
        "MODEL_CONFIG_ENCRYPTION_KEY", secret_key
    )
    default_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "天翼云")
    default_llm_api_key: str = os.getenv("DEFAULT_LLM_API_KEY", "")
    default_llm_base_url: str = os.getenv(
        "DEFAULT_LLM_BASE_URL", "https://wishub-x6.ctyun.cn/v1"
    )
    default_llm_model_id: str = os.getenv(
        "DEFAULT_LLM_MODEL_ID", "Qwen3.5-35B-A3B"
    )

    default_tts_provider: str = os.getenv("DEFAULT_TTS_PROVIDER", "阿里云")
    default_tts_api_key: str = os.getenv("DEFAULT_TTS_API_KEY", "")
    default_tts_base_url: str = os.getenv(
        "DEFAULT_TTS_BASE_URL",
        "https://llm-wtca05a6w9nkuqgi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    default_tts_model_id: str = os.getenv(
        "DEFAULT_TTS_MODEL_ID", "cosyvoice-v3.5-plus"
    )
    # CosyVoice 3.5 不提供系统音色，必须填写克隆或设计得到的 voice id。
    default_tts_voice: str = os.getenv("DEFAULT_TTS_VOICE", "")

    default_stt_provider: str = os.getenv("DEFAULT_STT_PROVIDER", "阿里云")
    default_stt_api_key: str = os.getenv("DEFAULT_STT_API_KEY", "")
    default_stt_base_url: str = os.getenv(
        "DEFAULT_STT_BASE_URL",
        "https://llm-wtca05a6w9nkuqgi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    default_stt_model_id: str = os.getenv(
        "DEFAULT_STT_MODEL_ID", "paraformer-realtime-v2"
    )

    default_vlm_provider: str = os.getenv("DEFAULT_VLM_PROVIDER", "华工")
    default_vlm_api_key: str = os.getenv(
        "DEFAULT_VLM_API_KEY", huagong_vlm_token
    )
    default_vlm_base_url: str = os.getenv(
        "DEFAULT_VLM_BASE_URL", huagong_vlm_url
    )
    default_vlm_model_id: str = os.getenv(
        "DEFAULT_VLM_MODEL_ID", "hazard_vlm_detection"
    )
    vlm_input_name: str = os.getenv("VLM_INPUT_NAME", "image")

    # 华工文本模型（小程序工作台「千问 / 华工」下拉的第二项）。
    # 未配置时前端选「华工」会自动回退默认文本模型，不会报错。
    huagong_llm_provider: str = os.getenv("HUAGONG_LLM_PROVIDER", "华工")
    huagong_llm_api_key: str = os.getenv("HUAGONG_LLM_API_KEY", "")
    huagong_llm_base_url: str = os.getenv("HUAGONG_LLM_BASE_URL", "")
    huagong_llm_model_id: str = os.getenv("HUAGONG_LLM_MODEL_ID", "")

    # 旧版变量保留为只读兼容，实际调用统一由数据库模型配置驱动。
    tts_api_url: str = os.getenv("TTS_API_URL", "")
    tts_api_token: str = os.getenv("TTS_API_TOKEN", "")
    tts_model: str = os.getenv("TTS_MODEL", "")
    tts_voice: str = os.getenv("TTS_VOICE", "")

    # 微信小程序（Secret 仅服务端持有）
    wechat_app_id: str = os.getenv("WECHAT_APP_ID", "")
    wechat_app_secret: str = os.getenv("WECHAT_APP_SECRET", "")
    wechat_api_base: str = os.getenv("WECHAT_API_BASE", "https://api.weixin.qq.com")
    wechat_mock_login_enabled: bool = os.getenv("WECHAT_MOCK_LOGIN_ENABLED", "false").lower() == "true"
    # 微信服务通知模板ID（订阅消息）
    wechat_template_ids: dict = {
        "task_assigned": os.getenv("WECHAT_TEMPLATE_TASK_ASSIGNED", ""),
        "task_urged": os.getenv("WECHAT_TEMPLATE_TASK_URGED", ""),
        "document_approved": os.getenv("WECHAT_TEMPLATE_DOC_APPROVED", ""),
        "document_rejected": os.getenv("WECHAT_TEMPLATE_DOC_REJECTED", ""),
        "hazard_detected": os.getenv("WECHAT_TEMPLATE_HAZARD_DETECTED", ""),
    }

    # TRTC secrets remain server-side. Advanced room permission control is mandatory.
    rtc_provider: str = ""
    trtc_sdk_app_id: int = 0
    trtc_secret_key: str = ""
    trtc_private_map_key_enabled: bool = False
    rtc_credential_ttl_seconds: int = 300
    rtc_ring_timeout_seconds: int = 45
    rtc_disconnect_timeout_seconds: int = 60
    rtc_max_call_seconds: int = 7200

    public_api_base_url: str = os.getenv("PUBLIC_API_BASE_URL", "http://localhost:8000")
    public_ws_base_url: str = os.getenv("PUBLIC_WS_BASE_URL", "ws://localhost:8000")

    # 上传限制
    max_image_bytes: int = int(os.getenv("MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))
    max_video_bytes: int = int(os.getenv("MAX_VIDEO_BYTES", str(200 * 1024 * 1024)))
    max_video_duration_seconds: int = int(os.getenv("MAX_VIDEO_DURATION_SECONDS", "300"))
    max_audio_bytes: int = int(os.getenv("MAX_AUDIO_BYTES", str(30 * 1024 * 1024)))
    max_document_bytes: int = int(
        os.getenv("MAX_DOCUMENT_BYTES", str(50 * 1024 * 1024))
    )

    # 知识库路径。优先使用环境变量，其次仓库根目录下的"知识库"，
    # 最后回退到项目自带（app 包同级）的"知识库"，以兼容容器挂载/镜像部署。
    knowledge_base_path: Path = Path(
        os.getenv("KNOWLEDGE_BASE_PATH")
        or _resolve_knowledge_base_path()
    )

    # 数据库（MySQL）
    database_url: str = os.getenv(
        "DATABASE_URL",
        "mysql+aiomysql://root@127.0.0.1:3306/gdjl?charset=utf8mb4"
    )

    # 文件存储
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    export_dir: Path = Path(os.getenv("EXPORT_DIR", "./exports"))

    # 天气 API（可选）
    weather_api_key: str = os.getenv("WEATHER_API_KEY", "")
    weather_api_url: str = os.getenv("WEATHER_API_URL", "https://devapi.qweather.com/v7/weather/now")

    # ============ 短信验证码（手机号注册 / 登录 OTP） ============
    # provider: mock(默认, 不发真实短信, 开发自测) | tencent | aliyun(待补)
    sms_provider: str = os.getenv("SMS_PROVIDER", "mock")
    sms_code_ttl: int = int(os.getenv("SMS_CODE_TTL", "300"))              # 验证码有效期(秒)
    sms_code_resend_seconds: int = int(os.getenv("SMS_CODE_RESEND_SECONDS", "60"))  # 同号重发间隔
    sms_code_max_daily: int = int(os.getenv("SMS_CODE_MAX_DAILY", "10"))   # 同号每日上限
    # 注册默认组织（裸用户归属，避免 org_id 空）
    registration_default_org_id: int = int(os.getenv("REGISTRATION_DEFAULT_ORG_ID", "1"))
    # 腾讯云短信（SMS_PROVIDER=tencent 时生效）
    sms_tencent_secret_id: str = os.getenv("SMS_TENCENT_SECRET_ID", "")
    sms_tencent_secret_key: str = os.getenv("SMS_TENCENT_SECRET_KEY", "")
    sms_tencent_sdk_app_id: str = os.getenv("SMS_TENCENT_SDK_APP_ID", "")
    sms_tencent_sign: str = os.getenv("SMS_TENCENT_SIGN", "")
    sms_tencent_template_id: str = os.getenv("SMS_TENCENT_TEMPLATE_ID", "")
    # ============ 短信验证码结束 ============

settings = Settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.export_dir.mkdir(parents=True, exist_ok=True)


def get_cors_origins() -> list[str]:
    """获取 CORS 允许的来源列表

    开发模式（debug=True）且未配置白名单时，返回 ["*"]。
    生产模式（debug=False）时，必须通过 CORS_ALLOWED_ORIGINS 环境变量配置白名单；
    未配置则仅允许 localhost。
    """
    if settings.cors_allowed_origins:
        return [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    if settings.debug:
        return ["*"]
    # 生产模式未配置白名单时，仅允许本地
    return ["http://localhost:3000", "http://localhost:5174", "http://127.0.0.1:3000"]


def check_production_security() -> list[str]:
    """生产环境安全检查，返回警告消息列表"""
    warnings: list[str] = []
    if not settings.debug:
        def missing_or_placeholder(value: str) -> bool:
            normalized = value.strip().lower()
            return not normalized or normalized.startswith(("replace", "change-me", "changeme"))

        if (
            settings.secret_key == STOCK_SECRET_KEY
            or missing_or_placeholder(settings.secret_key)
            or len(settings.secret_key) < 32
        ):
            warnings.append("SECRET_KEY 必须使用至少 32 位的随机密钥")
        database_lower = settings.database_url.lower()
        if (
            "://root" in database_lower
            or ":password@" in database_lower
            or ":replace" in database_lower
        ):
            warnings.append("DATABASE_URL 必须使用独立数据库账户和强密码")
        if not settings.cors_allowed_origins or "*" in settings.cors_allowed_origins:
            warnings.append("CORS_ALLOWED_ORIGINS 未配置，生产环境必须指定允许的前端域名")
        if not settings.public_api_base_url.startswith("https://"):
            warnings.append("PUBLIC_API_BASE_URL 生产环境必须使用 HTTPS")
        if not settings.public_ws_base_url.startswith("wss://"):
            warnings.append("PUBLIC_WS_BASE_URL 生产环境必须使用 WSS")
        if missing_or_placeholder(settings.wechat_app_id) or missing_or_placeholder(
            settings.wechat_app_secret
        ):
            warnings.append("微信小程序 AppID/AppSecret 未配置")
        if settings.wechat_mock_login_enabled:
            warnings.append("WECHAT_MOCK_LOGIN_ENABLED 生产环境必须关闭")
        if not settings.wechat_api_base.startswith("https://"):
            warnings.append("WECHAT_API_BASE 生产环境必须使用 HTTPS")
        if missing_or_placeholder(settings.huagong_vlm_token):
            warnings.append("HUAGONG_VLM_TOKEN/AI_CENTER_TOKEN 未配置，隐患识别不可用")
        if (
            settings.model_config_encryption_key == settings.secret_key
            or missing_or_placeholder(settings.model_config_encryption_key)
            or len(settings.model_config_encryption_key) < 32
        ):
            warnings.append("MODEL_CONFIG_ENCRYPTION_KEY 必须使用独立的至少 32 位随机密钥")
        if missing_or_placeholder(settings.default_llm_api_key):
            warnings.append("DEFAULT_LLM_API_KEY 未配置，文本模型不可用")
        if missing_or_placeholder(settings.default_tts_api_key):
            warnings.append("DEFAULT_TTS_API_KEY 未配置，语音合成不可用")
        if missing_or_placeholder(settings.default_stt_api_key):
            warnings.append("DEFAULT_STT_API_KEY 未配置，语音识别不可用")
    return warnings


def check_critical_security() -> list[str]:
    """致命项自检——**不受 DEBUG 开关影响**，每次启动都必须检查。

    背景：``check_production_security()`` 只在 ``debug=False`` 时才真正执行，
    而 ``debug`` 的默认值就是 True。生产若漏配 ``DEBUG``（compose 里只写业务
    变量是常见情形），整套生产自检会被静默跳过：JWT 仍用仓库公开的占位密钥
    签发，任何人读一遍仓库即可伪造超管令牌。此类问题不能依赖"记得配 DEBUG"。

    因此把"一旦成立就等于认证失守"的几项独立出来，无条件告警。
    """
    issues: list[str] = []
    if settings.secret_key == STOCK_SECRET_KEY:
        issues.append(
            "SECRET_KEY 仍为仓库公开的占位值，JWT 可被任意伪造（必须设置独立随机密钥）"
        )
    elif len(settings.secret_key) < 32:
        issues.append(f"SECRET_KEY 长度仅 {len(settings.secret_key)}，建议 ≥32 位随机串")
    if settings.model_config_encryption_key == settings.secret_key:
        issues.append("MODEL_CONFIG_ENCRYPTION_KEY 与 SECRET_KEY 相同，密钥用途必须隔离")
    return issues
