"""기상청 ASOS 일자료 응답 파서.

API 호출·파일 저장 없음. 순수 변환만 한다.

    process_weather()  ASOS item[] → DataFrame

빈 문자열('')의 의미가 컬럼마다 다른 게 이 파일의 핵심이다.

  강수·적설(sumRn, ddMefs) : '' = "비/눈이 안 왔다" → 0.0
      NULL 로 두면 AVG()·SUM() 에서 조용히 빠져 강수량이 실제보다
      높게 계산된다. 실측 결측률 52~57%(sumRn), 95~100%(ddMefs)로
      대부분이 '안 온 날'이다.

  기온·습도·일조·일사 : '' = 관측 결측 → NaN
      실측 결측률 0% 라 사실상 안 나오지만, 결측을 0으로 채우면
      한겨울 기온이 0도로 둔갑하므로 절대 채우지 않는다.

sumRnDur(강수계속시간)은 뺐다. 대관령이 100% 결측이라 지점마다
관측 항목이 달라 피처로 쓸 수 없다.
"""
from __future__ import annotations

import pandas as pd

# '' → 0.0 (안 온 날)
ZERO_FILL_COLS = ["sumRn", "ddMefs"]

# '' → NaN (관측 결측)
NULLABLE_NUMERIC_COLS = [
    "avgTa", "minTa", "maxTa",   # 평균·최저·최고 기온
    "avgRhm",                     # 평균 상대습도
    "minTg",                      # 최저 초상온도 (서리 판정)
    "sumSsHr",                    # 일조시간
    "sumGsr",                     # 일사량
]

# 문자열 컬럼. 누락 시 채워 스키마 안정성 확보.
STR_COLS = ["stnId", "stnNm", "tm"]


def _rows(data) -> list[dict]:
    """iter_daily 결과(list) 또는 원시 응답(dict) → row 리스트로 정규화."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        body = data.get("response", {}).get("body", data)
        item = (body.get("items") or {}).get("item", [])
        if isinstance(item, dict):
            return [item]
        return item or []
    return []


def process_weather(data) -> pd.DataFrame:
    """ASOS 일자료 응답 → Long Format DataFrame.

    data : iter_daily 결과(list[dict]) 또는 원시 응답(dict).
    grain = 관측일 × 지점.
    """
    rows = _rows(data)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    for col in STR_COLS + ZERO_FILL_COLS + NULLABLE_NUMERIC_COLS:
        if col not in df.columns:
            df[col] = None

    # 관측일(YYYY-MM-DD) → DATE
    df["obs_date"] = pd.to_datetime(df["tm"], errors="coerce")

    # 안 온 날('')은 0, 그 외는 수치화
    for col in ZERO_FILL_COLS:
        df[col] = pd.to_numeric(
            df[col].replace(r"^\s*$", "0", regex=True), errors="coerce"
        ).fillna(0.0)

    # 관측 결측('')은 NaN 으로 남긴다
    for col in NULLABLE_NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
