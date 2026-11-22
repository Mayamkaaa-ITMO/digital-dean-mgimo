FROM node:22-bookworm-slim AS youtube-runtime

FROM debian:bookworm-slim AS whisper
ARG WHISPER_VERSION=v1.8.4
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl cmake build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir /build && curl --fail --retry 3 --location \
    "https://codeload.github.com/ggml-org/whisper.cpp/tar.gz/refs/tags/${WHISPER_VERSION}" \
    | tar -xz -C /build --strip-components=1 \
    && cmake -S /build -B /build/out -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
       -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DWHISPER_BUILD_TESTS=OFF \
    && cmake --build /build/out --target whisper-cli --parallel 2

FROM python:3.12-slim-bookworm AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DEAN_DB=/var/lib/dean/dean.sqlite3 \
    DEAN_WHISPER_MODEL=/opt/models/whisper-base.bin \
    XDG_CONFIG_HOME=/tmp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tzdata ffmpeg tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 dean \
    && useradd --uid 10001 --gid dean --no-create-home --shell /usr/sbin/nologin dean \
    && install -d -m 0700 -o dean -g dean /var/lib/dean /var/lib/dean/lectures \
    && install -d /opt/models
COPY --from=whisper /build/out/bin/whisper-cli /usr/local/bin/whisper-cli
RUN curl --fail --retry 3 --location \
    'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin' -o "/opt/models/whisper-base.bin"
WORKDIR /opt/dean
COPY deploy/requirements.txt requirements-auth.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -r requirements-auth.txt
ARG DEAN_INSTALL_FIELD=1
RUN if [ "$DEAN_INSTALL_FIELD" = "1" ]; then \
      python -m pip install --no-cache-dir torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu \
      && python -m pip install --no-cache-dir 'ultralytics==8.4.212' \
      && curl --fail --retry 3 --location \
        'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt' -o /opt/models/yolo11n.pt \
      && echo '0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1  /opt/models/yolo11n.pt' | sha256sum -c -; \
    fi
ARG YTDLP_VERSION=2026.10.26
COPY --from=youtube-runtime /usr/local/bin/node /usr/local/bin/node
RUN python -m pip install --no-cache-dir "yt-dlp[default]==${YTDLP_VERSION}" \
    && node --version && yt-dlp --version
COPY dean/ dean/
COPY scripts/ scripts/
COPY samples/ samples/
COPY deploy/check_http.py deploy/gunicorn.conf.py deploy/entrypoint.sh deploy/
# An archive extracted with a private umask can leave nested directories
# accessible only to root. The service user needs read and search access.
RUN chmod -R a+rX dean scripts samples deploy
ENV DEAN_FIELD_PYTHON=/usr/local/bin/python DEAN_FIELD_MODEL=/opt/models/yolo11n.pt
USER 10001:10001
RUN python -c 'from dean.db import schema_version; print("SQLite schema:", schema_version())'
CMD ["sh", "deploy/entrypoint.sh"]
