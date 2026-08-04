"""한국천문연구원 특일정보 응답 파서.

API 호출·파일 저장 없음. 순수 변환만 한다.

    process_special_day()  SpcdeInfoService item[] → DataFrame

grain = 특일 날짜 × 특일명. 한 날짜에 여러 행이 올 수 있다(설 연휴가
사흘 각각 한 행, 절기와 잡절이 겹치는 날 등).

isHoliday 는 원문 그대로만 담고 '휴일 판정'에는 쓰지 않는다. 이 API 는
2026년 기준 제헌절·노동절까지 전부 'Y' 로 주는데 둘 다 관공서 공휴일이
아니다. 실제 휴장 여부는 여기가 아니라 뷰(transform)에서 정한다.
"""
from __future__ import annotations

import pandas as pd

# dateKind → 사람이 읽는 구분명. 수집하지 않는 02(기념일)도 혹시 섞여
# 들어올 때 코드가 그대로 남지 않게 적어둔다.
DATE_KIND_NAMES: dict[str, str] = {
    "01": "공휴일",
    "02": "기념일",
    "03": "24절기",
    "04": "잡절",
}

# 응답 문자열 컬럼. 누락 시 채워 스키마 안정성 확보.
#
# kst 는 거의 24절기 전용이다(2021~2028 실측: 절기 192행 전부 보유,
# 공휴일 0행). 다만 2024년 잡절 7행에만 예외로 값이 있는데, 그 값이
# 시각이 아니라 MMDD 날짜다(정월대보름 2024-02-24 → '0224'). 제공처
# 입력 오류로 보이므로 kst 를 시각으로 해석해 쓰면 안 된다. 원문 보존용.
STR_COLS = ["dateKind", "dateName", "isHoliday", "kst"]


def _items(data) -> list[dict]:
    """iter_special_days 결과(list) 또는 원시 응답(dict) → row 리스트로 정규화."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        body = data.get("response", {}).get("body", data)
        item = (body.get("items") or {}).get("item", [])
        if isinstance(item, dict):
            return [item]
        return item or []
    return []


def process_special_day(data) -> pd.DataFrame:
    """특일정보 응답 → Long Format DataFrame.

    data : iter_special_days 결과(list[dict]) 또는 원시 응답(dict).
    """
    rows = _items(data)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    for col in STR_COLS:
        if col not in df.columns:
            df[col] = None

    # locdate 는 int(20260216) 로 온다. 문자열로 세운 뒤 파싱해야
    # to_datetime 이 숫자를 epoch 으로 오해하지 않는다.
    df["special_date"] = pd.to_datetime(
        df["locdate"].astype("string"), format="%Y%m%d", errors="coerce"
    )

    df["date_kind_name"] = df["dateKind"].map(DATE_KIND_NAMES)

    # 'Y'/'N' → BOOLEAN. 그 외 값은 NA 로 남긴다(임의로 False 로 만들지 않음).
    df["isHoliday"] = df["isHoliday"].map({"Y": True, "N": False}).astype("boolean")

    # 절기 시각은 '0502      ' 처럼 공백 패딩이 붙어 온다.
    df["kst"] = df["kst"].astype("string").str.strip().replace("", pd.NA)

    df["seq"] = pd.to_numeric(df.get("seq"), errors="coerce")

    return df
