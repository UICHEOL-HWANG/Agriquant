# Cloud Run Job 용 이미지. scripts/daily.sh 를 하루 한 번 돌린다.
#
#   docker build -t agriquant .
#   docker run --rm -e KAMIS_CERT_KEY=... -e KAMIS_CERT_ID=... agriquant
#
# 파이썬 버전을 3.11 로 못 박는다. pyproject 는 >=3.11 이지만 로컬이
# 3.11 이라, 컨테이너만 3.13 이면 재현되지 않는 차이가 생긴다.
FROM python:3.11-slim-bookworm

# ── KAMIS TLS ────────────────────────────────────────────────
# KAMIS 서버가 레거시 암호군만 제시해서 SECLEVEL=1 이 필요하다
# (base_client._LegacyTLSAdapter 가 컨텍스트 단위로 낮춘다).
#
# **여기서 시스템 openssl.cnf 를 건드리지 않는다.** 이 베이스 이미지의
# /etc/ssl/openssl.cnf 에는 CipherString 줄이 아예 없어서(2026-08-11 확인,
# OpenSSL 3.0.20) 낮출 시스템 기본값 자체가 없다. 어댑터만으로 충분하다.
#
# 그래도 **이 이미지에서 가장 먼저 깨질 곳**이라, 수집이 빈 응답만 받으면
# 여기부터 의심할 것. 확인 방법은 README 의 컨테이너 절에 있다.

# ── 의존성 ───────────────────────────────────────────────────
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 락파일만 먼저 넣어 의존성 레이어를 캐시한다. 소스가 바뀌어도
# 의존성이 그대로면 이 레이어를 다시 만들지 않는다.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ── 소스 ─────────────────────────────────────────────────────
# extract/.env 는 넣지 않는다. 비밀은 Secret Manager 로 주입한다.
COPY main.py ./
COPY extract/ ./extract/
COPY transform/ ./transform/
COPY model/ ./model/
COPY scripts/ ./scripts/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
    && chmod +x scripts/daily.sh

# 컨테이너는 파일시스템이 매 실행 새것이라 파케이 캐시가 안 남는다.
# 캐시를 만들어봐야 실행이 끝나면 사라지므로 BigQuery 를 직접 읽는다.
# TZ 를 박는 이유: 컨테이너 기본이 UTC 라 셸의 `date +%F` 가 한국 날짜와
# 어긋난다(KST 00~09시에 돌면 전날을 잡는다). 파이썬 쪽은 이미
# ZoneInfo("Asia/Seoul") 로 KST 를 쓰므로, 맞춰두지 않으면 셸과 파이썬이
# 서로 다른 '오늘'을 보게 된다.
ENV TZ=Asia/Seoul \
    MART_SOURCE=bigquery \
    REFRESH_CACHE=0 \
    DAYS=7 \
    PATH=/app/.venv/bin:$PATH

ENTRYPOINT ["./scripts/daily.sh"]
