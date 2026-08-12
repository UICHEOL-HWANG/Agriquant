# model/data.py
"""마트를 읽어오는 곳. 평가 경로에서는 읽기만 한다.

원천은 BigQuery 의 `mart_item_daily` 뷰다. 파케이 캐시는 **같은 것의 사본**
이지 별도 진실이 아니다. 노트북이 캐시를 쓰는 이유는 폴드를 수십 번 돌리는데
매번 조회하면 느려서고, 프로덕트에서도 같은 이유로 캐시를 기본으로 둔다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "notebooks" / ".cache" / "mart.parquet"

REQUIRED = [
    "item", "price_date", "price_kg_avg",
    "month", "dayofweek",
    "days_to_major_holiday", "days_since_major_holiday", "days_since_solar_term",
]
"""피처 생성에 반드시 있어야 하는 컬럼.

마트에는 이보다 많은 컬럼이 있지만(출하량·기상 등) 채택 피처는 이것뿐이다.
16·21·25·26·27번에서 출하량·기상은 품목 단위로 귀속되지 않아 뺐다.
"""


def load_mart(source: str = "auto") -> pd.DataFrame:
    """마트를 품목·날짜순으로 정렬해 돌려준다.

    Args:
        source: `"bigquery"` · `"cache"` · `"auto"`. auto 는 캐시가 있으면
            캐시를, 없으면 BigQuery 를 쓴다.

    Raises:
        ValueError: 필수 컬럼이 빠졌을 때. **조용히 NaN 으로 흘리지 않는다** —
            달력 피처 하나가 통째로 없어도 모델은 학습되고 성능만 조금
            떨어져서, 검사하지 않으면 원인을 못 찾는다.
    """
    if source == "auto":
        source = "cache" if CACHE.exists() else "bigquery"

    if source == "cache":
        if not CACHE.exists():
            raise FileNotFoundError(
                f"캐시가 없습니다: {CACHE}\n"
                "`--source bigquery` 로 돌리거나 refresh_cache() 를 먼저 부르세요."
            )
        df = pd.read_parquet(CACHE)
        logging.info(f"[model] 캐시에서 읽음: {CACHE.name}")
    elif source == "bigquery":
        from extract.database.connection import BigQueryConnection

        conn = BigQueryConnection()
        table = conn.table_id("mart_item_daily")
        df = conn.client.query(f"SELECT * FROM `{table}`").to_dataframe()
        logging.info(f"[model] BigQuery 에서 읽음: {table}")
    else:
        raise ValueError(f"source 는 bigquery·cache·auto 중 하나입니다: {source!r}")

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(
            f"마트에 필수 컬럼이 없습니다: {missing}\n"
            "transform.build_all() 이 원천 없이 뷰를 만들면 그 컬럼이 빠집니다. "
            "원천을 적재한 뒤 다시 돌리면 붙습니다."
        )

    df = df.copy()
    df["price_date"] = pd.to_datetime(df["price_date"])
    return df.sort_values(["item", "price_date"]).reset_index(drop=True)


def refresh_cache() -> Path:
    """BigQuery 에서 새로 받아 파케이 캐시를 덮어쓴다.

    **원천을 새로 적재했으면 반드시 부른다.** 뷰는 조회 시점 계산이라 원천에
    행이 붙으면 자동 반영되지만, 캐시는 사본이라 자동으로 안 바뀐다.

    `price_date` 를 datetime64 로 바꿔 저장한다. BigQuery 가 주는 dbdate 를
    그대로 파케이에 넣으면 나중에 읽을 때 깨진다.
    """
    df = load_mart(source="bigquery")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    logging.info(f"[model] 캐시 갱신: {CACHE} ({len(df):,}행)")
    return CACHE
