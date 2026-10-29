# 监理通后端镜像
# ---------------------------------------------------------------------------
# 目的：把「系统依赖（ffmpeg）+ Python 依赖」烘进镜像层，让容器重建变成
# 秒级操作。此前 compose 直接在 command 里 `apt-get install` + `pip install`，
# 每次重建都要从零重装约 600MB 依赖，期间服务不可用十几到几十分钟。
#
# 代码仍然通过 compose 的 bind mount（./cloud-backend:/app）挂载，
# 所以改代码走 uvicorn --reload，不需要重新 build 镜像。
FROM python:3.12-slim

ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn

# RapidOCR is not mirrored by Tsinghua; use official PyPI so fresh images include OCR.
ENV TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.org/simple \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ffmpeg 用于语音/视频转码；fonts-wqy-zenhei 提供中文字形（水印必须，否则中文变方块）
# libreoffice-writer：省统表 docx→PDF 必须与 Word 同版式，Linux 下只有 soffice 能做到；
# 之前镜像没装，导出 PDF / 签认后生成 PDF 全部 501。
RUN sed -i s@deb.debian.org@${APT_MIRROR}@g /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-wqy-zenhei libreoffice-writer \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# RapidOCR 已同步到阿里云 PyPI；官方 PyPI 在本项目服务器上只有约 25KB/s。
# 仅覆盖 pip 命令的索引，避免改动前面的 apt 层缓存。
ARG PIP_MIRROR=https://mirrors.aliyun.com/pypi/simple/

# 只复制依赖清单：requirements.txt 不变时这一层直接命中缓存，不重装
COPY requirements.txt /tmp/requirements.txt
RUN PIP_INDEX_URL=${PIP_MIRROR} pip install --no-cache-dir dashscope \
 && PIP_INDEX_URL=${PIP_MIRROR} pip install --no-cache-dir -r /tmp/requirements.txt

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
