# database/repository.py
"""파서 DataFrame → BigQuery 적재 계층.

파서가 만든 한글 컬럼 DataFrame을 받아
  1) 스펙(rename)에 정의된 컬럼만 골라 영문으로 이름을 바꾸고
  2) 스키마 타입에 맞춰 값을 정리(DATE/INTEGER/FLOAT/BOOLEAN)한 뒤
  3) load_table_from_dataframe 로 BigQuery에 적재한다.

값 변환 규칙은 파서와 동일한 사상(관측치 보존, 결측 유지)을 따른다.
"""
from __future__ import annotations

import logging

import pandas as pd
from google.cloud import bigquery

from extract.config.items import ItemTarget

from .connection import BigQueryConnection
from .models import (
    MAFRA_CONTRACT,
    MONTHLY_PRICE,
    SHIPMENT,
    SPECIAL_DAY,
    WEATHER,
    WHOLESALE_AGG,
    WHOLESALE_OBS,
    YEARLY_PRICE,
    TableSpec,
)

# item_code 테이블 이름 (dataset 안)
ITEM_CODE_TABLE = "item_code"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 사용자 mode → BigQuery write_disposition
_WRITE_MODE = {
    "append": bigquery.WriteDisposition.WRITE_APPEND,     # 이어 쌓기(기본)
    "replace": bigquery.WriteDisposition.WRITE_TRUNCATE,  # 테이블 통째 교체
}


class BigQueryRepository:
    """DataFrame을 BigQuery 테이블로 저장하는 리포지토리."""

    def __init__(self, conn: BigQueryConnection | None = None):
        self.conn = conn or BigQueryConnection()
        self.conn.ensure_dataset()

    # ── 내부: 스펙에 맞춰 DataFrame 정리 ─────────────────────
    @staticmethod
    def _prepare(df: pd.DataFrame, spec: TableSpec) -> pd.DataFrame:
        """rename → 타입 캐스팅 → 스키마 순서로 정렬."""
        present = [c for c in spec.rename if c in df.columns]
        out = df[present].rename(columns=spec.rename)

        for field in spec.schema:
            col = field.name
            if col not in out.columns:
                out[col] = None            # 스펙엔 있으나 원본에 없는 컬럼 → NULL
            if field.field_type == "DATE":
                out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
            elif field.field_type == "INTEGER":
                out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
            elif field.field_type == "FLOAT":
                out[col] = pd.to_numeric(out[col], errors="coerce")
            elif field.field_type == "BOOLEAN":
                out[col] = out[col].astype("boolean")

        return out[[f.name for f in spec.schema]]   # 스키마 순서 고정

    # ── 조회: 수집 대상 품목 ─────────────────────────────────
    def load_item_targets(
        self,
        item_names: list[str] | None = None,
        category_code: str | None = None,
    ) -> list[ItemTarget]:
        """item_code 테이블에서 수집 대상 품목을 읽어온다.

        item_names    : 품목명 화이트리스트(예: ["배추","감자"]). 각 품목의
                        category_code 는 테이블 값을 그대로 쓴다(감자=100 대응).
        category_code : 부류코드로 한정(예: "200" → 채소류 전체).
        둘 다 주면 AND, 둘 다 없으면 테이블 전체.
        """
        conds, params = [], []
        if item_names:
            conds.append("item_name IN UNNEST(@names)")
            params.append(bigquery.ArrayQueryParameter("names", "STRING", list(item_names)))
        if category_code:
            conds.append("category_code = @cat")
            params.append(bigquery.ScalarQueryParameter("cat", "STRING", category_code))

        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        query = (
            "SELECT DISTINCT category_code, item_code, item_name "
            f"FROM `{self.conn.table_id(ITEM_CODE_TABLE)}`{where} "
            "ORDER BY item_code"
        )
        rows = self.conn.client.query(
            query, job_config=bigquery.QueryJobConfig(query_parameters=params)
        ).result()
        targets = [ItemTarget(r.category_code, r.item_code, r.item_name) for r in rows]

        if item_names:   # 테이블에 없는 이름은 경고
            found = {t.item_name for t in targets}
            for miss in set(item_names) - found:
                logging.warning(f"[item_code] '{miss}' 품목을 테이블에서 찾지 못함")
        return targets

    # ── 범용 저장 ────────────────────────────────────────────
    def save(self, df: pd.DataFrame | None, spec: TableSpec, mode: str = "append") -> int:
        """DataFrame 하나를 spec 테이블에 적재. 적재 행 수 반환."""
        if df is None or df.empty:
            logging.info(f"[BigQuery] {spec.name}: 빈 DataFrame → 건너뜀")
            return 0

        prepared = self._prepare(df, spec)
        table_id = self.conn.table_id(spec.name)

        job_config = bigquery.LoadJobConfig(
            schema=spec.schema,
            write_disposition=_WRITE_MODE[mode],
        )
        if spec.partition_field:
            job_config.time_partitioning = bigquery.TimePartitioning(
                field=spec.partition_field
            )

        job = self.conn.client.load_table_from_dataframe(
            prepared, table_id, job_config=job_config
        )
        job.result()   # 완료 대기 (실패 시 예외)

        logging.info(f"[BigQuery] {spec.name} ← {len(prepared):,}행 적재 ({mode})")
        return len(prepared)

    # ── 파서별 편의 메서드 ───────────────────────────────────
    def save_monthly(self, df: pd.DataFrame, mode: str = "append") -> int:
        """process_price_json() 결과 저장."""
        return self.save(df, MONTHLY_PRICE, mode)

    def save_yearly(self, df: pd.DataFrame, mode: str = "append") -> int:
        """process_yearly_json() 결과 저장."""
        return self.save(df, YEARLY_PRICE, mode)

    def save_shipment(self, df: pd.DataFrame, mode: str = "append") -> int:
        """process_shipment() 결과 저장 → datago_shipment."""
        return self.save(df, SHIPMENT, mode)

    def save_contract(self, df: pd.DataFrame, mode: str = "append") -> int:
        """process_contract() 결과 저장 → mafra_contract_crop."""
        return self.save(df, MAFRA_CONTRACT, mode)

    def save_weather(self, df: pd.DataFrame, mode: str = "append") -> int:
        """process_weather() 결과 저장 → kma_weather_daily."""
        return self.save(df, WEATHER, mode)

    def save_special_day(self, df: pd.DataFrame, mode: str = "replace") -> int:
        """process_special_day() 결과 저장 → kasi_special_day.

        기본값이 replace 인 것은 save_contract 와 같은 이유다. 전 기간을
        받아도 400여 행뿐인 전량 스냅샷이라 append 하면 실행할 때마다
        통째로 중복된다. 게다가 2028년 대체공휴일처럼 뒤늦게 고시되는
        값이 있어 재수집으로 덮어써야 최신이 된다.
        """
        return self.save(df, SPECIAL_DAY, mode)

    def save_wholesale(
        self, parts: dict[str, pd.DataFrame], mode: str = "append"
    ) -> dict[str, int]:
        """clean() 결과(관측치·전체평균·평년기준선) 저장.

        관측치는 관측 테이블에, 두 집계값은 agg_type으로 구분해 한 테이블에 담는다.
        반환: {테이블명: 적재행수}
        """
        obs = parts.get("관측치")
        agg_frames = [f for f in (parts.get("전체평균"), parts.get("평년기준선")) if f is not None]
        agg = pd.concat(agg_frames, ignore_index=True) if agg_frames else None

        return {
            WHOLESALE_OBS.name: self.save(obs, WHOLESALE_OBS, mode),
            WHOLESALE_AGG.name: self.save(agg, WHOLESALE_AGG, mode),
        }
