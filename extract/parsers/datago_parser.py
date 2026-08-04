"""datago(공공데이터포털) 출하량 추이 응답 파서.

API 호출·파일 저장 없음. 순수 변환만 한다.

    process_shipment()  shipmentSequel  items.item[] → long DataFrame

응답 필드는 이미 영문 snake_case 라 컬럼명을 그대로 둔다.
숫자 필드는 문자열/None 으로 오므로 수치화하고, spmt_ymd(YYYYMMDD)를
날짜(price_date)로 파싱한다. grain = 출하일 × 도매시장 × 법인 × 소분류.
"""
from __future__ import annotations

import pandas as pd

# 수치화할 컬럼 (평균 출하수량/출하량 + 1~4주전)
NUMERIC_COLS = [
    "unit_qty",
    "avg_spmt_qty", "avg_spmt_amt",
    "ww1_bfr_avg_spmt_qty", "ww1_bfr_avg_spmt_amt",
    "ww2_bfr_avg_spmt_qty", "ww2_bfr_avg_spmt_amt",
    "ww3_bfr_avg_spmt_qty", "ww3_bfr_avg_spmt_amt",
    "ww4_bfr_avg_spmt_qty", "ww4_bfr_avg_spmt_amt",
]

# 응답의 전체 문자열 컬럼(코드·명칭). 누락 시 채워 스키마 안정성 확보.
STR_COLS = [
    "spmt_ymd",
    "whsl_mrkt_cd", "whsl_mrkt_nm",
    "corp_cd", "corp_nm",
    "gds_lclsf_cd", "gds_lclsf_nm",
    "gds_mclsf_cd", "gds_mclsf_nm",
    "gds_sclsf_cd", "gds_sclsf_nm",
    "unit_cd", "unit_nm",
]


def _items(data) -> list[dict]:
    """응답(dict) 또는 item 리스트를 item 리스트로 정규화.

    iter_shipment 는 list[dict] 를, 원시 응답은 response.body.items.item 을
    준다. 둘 다 허용한다.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        body = data.get("response", {}).get("body", data)
        item = body.get("items", {})
        item = item.get("item", []) if isinstance(item, dict) else item
        if isinstance(item, dict):
            return [item]
        return item or []
    return []


def process_shipment(data) -> pd.DataFrame:
    """datago 출하량 추이 응답 → Long Format DataFrame.

    data : iter_shipment 결과(list[dict]) 또는 원시 응답(dict) 둘 다 허용.
    """
    items = _items(data)
    if not items:
        return pd.DataFrame()

    df = pd.DataFrame(items)

    # 스펙 컬럼이 응답에서 빠졌으면 채운다(품목·날짜별로 필드가 없을 수 있음)
    for col in STR_COLS + NUMERIC_COLS:
        if col not in df.columns:
            df[col] = None

    df["price_date"] = pd.to_datetime(df["spmt_ymd"], format="%Y%m%d", errors="coerce")

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
