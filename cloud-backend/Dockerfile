# 监理通后端镜像
# ---------------------------------------------------------------------------
# 目的：把「系统依赖（ffmpeg）+ Python 依赖」烘进镜像层，让容器重建变成
# 秒级操作。此前 compose 直接在 command 里 `apt-get install` + `pip install`，
# 每次重建都要从零重装约 600MB 依赖，期间服务不可用十几到几十分钟。
#
# 代码仍然通过 compose 的 bind mount（./cloud-backend:/app）挂载，
# 所以改代码走 uvicorn --reload，不需要重新 build 镜像。
FROM python:3.12-slim

ENV TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ffmpeg 用于语音/视频转码；fonts-wqy-zenhei 提供中文字形（水印必须，否则中文变方块）
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-wqy-zenhei \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 只复制依赖清单：requirements.txt 不变时这一层直接命中缓存，不重装
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir dashscope \
 && pip install --no-cache-dir -r /tmp/requirements.txt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
