"""按项目坐标获取气象；失败保留待补填状态，禁止虚构值。"""
import logging
import os
from datetime import datetime, timedelta, timezone
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


def weather_label(code):
    if code == 0: return "晴"
    if code in (1, 2): return "多云"
    if code == 3: return "阴"
    if code in (45, 48): return "雾"
    if code in (71, 73, 75, 77, 85, 86): return "雪"
    if code in (95, 96, 99): return "雷雨"
    if code in (65, 67, 82): return "大雨"
    if code in (51, 53, 55, 56, 57, 61, 63, 66, 80, 81): return "雨"
    return ""


async def daily_weather(project, target_date, overrides=None):
    manual = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    result = {"temp_high": "", "temp_low": "", "weather": "", "weather_source": "待补填"}
    if all(k in manual for k in ("temp_high", "temp_low", "weather")):
        return {**result, **manual, "weather_source": "人工填写", "weather_missing": []}
    latitude, longitude = getattr(project, "latitude", None), getattr(project, "longitude", None)
    if latitude not in (None, "") and longitude not in (None, ""):
        today = datetime.now(timezone(timedelta(hours=8))).date()
        archive = target_date < today - timedelta(days=5)
        # URL由部署配置提供，支持商业账户；不偷偷为生产选择供应商。
        key = "WEATHER_ARCHIVE_URL" if archive else "WEATHER_FORECAST_URL"
        endpoint = os.getenv(key, settings.weather_archive_url if archive else settings.weather_forecast_url)
        if endpoint:
            try:
                params = {"latitude": float(latitude), "longitude": float(longitude),
                          "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                          "start_date": target_date.isoformat(), "end_date": target_date.isoformat(),
                          "timezone": "Asia/Shanghai"}
                api_key = os.getenv("WEATHER_API_KEY", settings.weather_api_key)
                if api_key:
                    params["apikey"] = api_key
                async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                    response = await client.get(endpoint, params=params)
                    response.raise_for_status()
                    daily = response.json().get("daily", {})
                if daily.get("time", [None])[0] != target_date.isoformat():
                    raise ValueError("气象日期不匹配")
                high, low = daily.get("temperature_2m_max", [None])[0], daily.get("temperature_2m_min", [None])[0]
                label = weather_label(daily.get("weather_code", [None])[0])
                if high is None or low is None or not label:
                    raise ValueError("气象返回缺字段")
                result.update(temp_high=high, temp_low=low, weather=label,
                              weather_source="Open-Meteo（项目坐标，Asia/Shanghai）")
            except (httpx.HTTPError, ValueError, TypeError, IndexError, KeyError):
                logger.warning("项目 %s 的 %s 气象获取失败，需人工补填", getattr(project, "id", None), target_date)
    result.update(manual)
    if manual:
        result["weather_source"] += "；人工补填/覆盖"
    result["weather_missing"] = [k for k in ("temp_high", "temp_low", "weather") if result[k] in (None, "")]
    return result
