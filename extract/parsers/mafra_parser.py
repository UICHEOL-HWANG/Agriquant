"""MAFRA(농림축산식품부) Grid OpenAPI 응답 파서.

API 호출·파일 저장 없음. 순수 변환만 한다.

    process_contract()  농협계약재배 물량·도매가격  row[] → DataFrame

응답 필드는 대문자 코드명이라 그대로 두고, repository 에서 영문
snake_case 로 rename 한다. CRTR_YM(YYYYMM)은 base_month(date)로 파생,
농가수·물량·금액·가격은 수치화한다. grain = 기준월 × 시도·시군구 × 품목·품종·등급.
"""
from __future__ import annotations

import pandas as pd

# 수치화할 컬럼 (재배 농가수/물량/금액 + 월별·연별 가격 통계)
NUMERIC_COLS = [
    "GROW_APLY_FARM_CNT", "GROW_CTRT_FARM_CNT",
    "GROW_APLY_QTY", "GROW_CTRT_QTY",
    "GROW_APLY_AMT", "GROW_CTRT_AMT",
    "PMM_AVGPRC", "PMM_HGPRC", "PMM_LWPRC", "PMM_STDDVTN",
    "PYY_AVGPRC", "PYY_HGPRC", "PYY_LWPRC",
]

# 문자열 컬럼(코드·명칭·일시). 누락 시 채워 스키마 안정성 확보.
STR_COLS = [
    "CRTR_YM",
    "CTPRVN_NM", "CTNDCT_NM",
    "ITEM_NM", "ITEM_DTL_NM",
    "MART_ITEM_NM", "VRTY_NM", "GRD_NM",
    "UNIT", "UNIT_SZ",
    "REG_DT", "REG_USERID", "UPD_DT", "UPD_USERID",
]


def _rows(data) -> list[dict]:
    """iter_contract 결과(list) 또는 원시 응답(dict) → row 리스트로 정규화."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # {GRID_ID: {row: [...]}} 또는 {row: [...]} 모두 허용
        node = data
        if len(data) == 1 and isinstance(next(iter(data.values())), dict):
            node = next(iter(data.values()))
        row = node.get("row", [])
        if isinstance(row, dict):
            return [row]
        return row or []
    return []


def process_contract(data) -> pd.DataFrame:
    """MAFRA 농협계약재배 응답 → Long Format DataFrame.

    data : iter_contract_crop_price 결과(list[dict]) 또는 원시 응답(dict).
    """
    rows = _rows(data)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    for col in STR_COLS + NUMERIC_COLS:
        if col not in df.columns:
            df[col] = None

    # 기준월(YYYYMM) → 해당 월 1일 날짜
    df["base_month"] = pd.to_datetime(df["CRTR_YM"], format="%Y%m", errors="coerce")

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
