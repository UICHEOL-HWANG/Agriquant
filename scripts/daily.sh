#!/usr/bin/env bash
# 일 단위 파이프라인. cron 이나 Cloud Run Job 이 이걸 부른다.
#
#   ./scripts/daily.sh            # 최근 7일 구간으로 수집 후 오늘 신호
#   DAYS=14 ./scripts/daily.sh    # 구간을 넓혀서 (연휴 뒤 밀린 것 메울 때)
#
# 컨테이너(Cloud Run Job)에서는 이렇게 부른다:
#
#   MART_SOURCE=bigquery REFRESH_CACHE=0 ./scripts/daily.sh
#
# 파일시스템이 매 실행 새것이라 파케이 캐시가 안 남는다. 캐시를 만들어봐야
# 그 실행이 끝나면 사라지므로 건너뛰고 BigQuery 를 직접 읽는다.
# **스크립트를 둘로 나누지 않는 이유**는 곧 갈라지기 때문이다. 로컬에서
# 초록불인 것과 클라우드에서 도는 것이 같은 파일이어야 한다.
#
# bash 를 쓰는 이유: 컨테이너 베이스(Debian slim)에 zsh 이 없다.
#
# 순서가 계약이다:
#
#   수집 → 캐시 갱신 → 감시 → 예측 적재
#
# **감시가 예측 앞에 있다.** 커버리지가 깨진 날 신호를 내면 빠진 품목의
# 판단을 조용히 건너뛴다. 2026-08-07 에 실제로 그럴 뻔했고(16 → 11품목)
# 빠진 것 중에 배추가 있었다. `set -e` 와 monitor 의 종료코드 1 이 짝이다.
#
# 수집에 --replace-range 를 쓰는 이유는 멱등해서다. KAMIS 는 요청보다 넓게
# 돌려주는데(pipelines._clip_to_range 참조) append 로 받으면 돌릴 때마다
# 중복이 쌓인다. 이 조합이면 몇 번을 돌려도 결과가 같다.

set -euo pipefail

# cron 은 PATH 가 /usr/bin:/bin:/usr/sbin:/sbin 뿐이라 uv 를 못 찾는다.
# crontab 이 아니라 여기에 두는 이유는, 어디서 불려도 돌게 하려는 것이다.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

command -v uv >/dev/null || { echo "uv 를 찾을 수 없습니다: PATH=$PATH" >&2; exit 127; }

DAYS="${DAYS:-7}"
# KAMIS 는 당일 자료를 준다(2026-08-11 확인). 그래도 구간을 며칠 잡는 이유는
# 늦게 들어오거나 정정되는 행을 같이 덮으려는 것이다.
END="$(date +%F)"
START="$(date -v-${DAYS}d +%F 2>/dev/null || date -d "-${DAYS} days" +%F)"

MART_SOURCE="${MART_SOURCE:-auto}"     # auto | cache | bigquery
REFRESH_CACHE="${REFRESH_CACHE:-1}"    # 0 이면 캐시 갱신을 건너뛴다

echo "=== [1/4] 수집 ${START} ~ ${END} ==="
uv run python main.py --source backfill \
    --start "$START" --end "$END" --replace-range

if [ "$REFRESH_CACHE" = "1" ]; then
    echo "=== [2/4] 캐시 갱신 ==="
    uv run python -c "from model import refresh_cache; refresh_cache()"
else
    echo "=== [2/4] 캐시 갱신 건너뜀 (REFRESH_CACHE=0) ==="
fi

echo "=== [3/4] 상태 검사 (source=${MART_SOURCE}) ==="
# 실패하면 set -e 가 여기서 멈춘다. 예측은 안 나간다.
uv run python -m model.monitor --source "$MART_SOURCE"

echo "=== [4/4] 예측 적재 ==="
uv run python -m model.predict --source "$MART_SOURCE" --save

echo "=== 완료: $(date '+%F %T') ==="
