# database/models.py
"""BigQuery 테이블 스펙.

파서가 만든 '한글 컬럼' DataFrame을 BigQuery로 적재하기 위한
(테이블명 · 컬럼 rename · 스키마 · 파티션) 묶음을 한곳에 모은다.

BigQuery 컬럼은 영문 snake_case로 통일한다.
Repository 는 rename 에 정의된 컬럼만 골라 담고, 나머지 원문/디버그
컬럼(caption_원문 등)은 자연스럽게 버려진다.
"""
from __future__ import annotations

from dataclasses import dataclass

from google.cloud import bigquery

_F = bigquery.SchemaField


@dataclass(frozen=True)
class TableSpec:
    """하나의 논리 테이블을 BigQuery에 적재하는 방법."""

    name: str                                # BigQuery 테이블 ID
    rename: dict[str, str]                    # 파서(한글) → BigQuery(영문)
    schema: list[bigquery.SchemaField]        # 최종 컬럼/타입 (순서 = 적재 순서)
    partition_field: str | None = None        # DATE 파티셔닝 기준 컬럼

    @property
    def columns(self) -> list[str]:
        return [f.name for f in self.schema]


# ──────────────────────────────────────────────────────────────
# 월별 도·소매 가격 (process_price_json)
# ──────────────────────────────────────────────────────────────
MONTHLY_PRICE = TableSpec(
    name="kamis_monthly_price",
    rename={
        "날짜": "price_date",
        "거래단계": "trade_stage",
        "부류": "category",
        "품목": "item",
        "품종": "variety",
        "등급": "grade",
        "단위": "unit_raw",
        "연도": "year",
        "월": "month",
        "가격": "price",
        "연평균": "year_avg",
        "단위_수량": "unit_qty",
        "단위_기준": "unit_base",
        "단위당가격": "unit_price",
        "미래시점": "is_future",
    },
    schema=[
        _F("price_date", "DATE"),
        _F("trade_stage", "STRING"),
        _F("category", "STRING"),
        _F("item", "STRING"),
        _F("variety", "STRING"),
        _F("grade", "STRING"),
        _F("unit_raw", "STRING"),
        _F("year", "INTEGER"),
        _F("month", "INTEGER"),
        _F("price", "FLOAT"),
        _F("year_avg", "FLOAT"),
        _F("unit_qty", "FLOAT"),
        _F("unit_base", "STRING"),
        _F("unit_price", "FLOAT"),
        _F("is_future", "BOOLEAN"),
    ],
    partition_field="price_date",
)


# ──────────────────────────────────────────────────────────────
# 일별 도매가 - 관측치 (clean() → "관측치")
# ──────────────────────────────────────────────────────────────
WHOLESALE_OBS = TableSpec(
    name="kamis_wholesale_obs",
    rename={
        "날짜": "price_date",
        "품목": "item",
        "품종": "variety",
        "지역": "region",
        "시장": "market",
        "가격": "price",
        "단위당가격": "unit_price",
        "단위_수량": "unit_qty",
        "단위_기준": "unit_base",
        "단위_원문": "unit_raw",
        "미거래": "no_trade",
        "원문_kindname": "kindname_raw",
    },
    schema=[
        _F("price_date", "DATE"),
        _F("item", "STRING"),
        _F("variety", "STRING"),
        _F("region", "STRING"),
        _F("market", "STRING"),
        _F("price", "FLOAT"),
        _F("unit_price", "FLOAT"),
        _F("unit_qty", "FLOAT"),
        _F("unit_base", "STRING"),
        _F("unit_raw", "STRING"),
        _F("no_trade", "BOOLEAN"),
        _F("kindname_raw", "STRING"),
    ],
    partition_field="price_date",
)


# ──────────────────────────────────────────────────────────────
# 일별 도매가 - 집계값 (clean() → "전체평균" + "평년기준선")
# grain이 다르므로 관측치와 분리. agg_type 으로 평균/평년 구분.
# ──────────────────────────────────────────────────────────────
WHOLESALE_AGG = TableSpec(
    name="kamis_wholesale_agg",
    rename={
        "날짜": "price_date",
        "집계구분": "agg_type",
        "가격": "price",
        "미거래": "no_trade",
    },
    schema=[
        _F("price_date", "DATE"),
        _F("agg_type", "STRING"),
        _F("price", "FLOAT"),
        _F("no_trade", "BOOLEAN"),
    ],
    partition_field="price_date",
)


# ──────────────────────────────────────────────────────────────
# 연도별 가격 통계 (process_yearly_json)
# ──────────────────────────────────────────────────────────────
YEARLY_PRICE = TableSpec(
    name="kamis_yearly_price",
    rename={
        "날짜": "price_date",
        "거래단계": "trade_stage",
        "부류": "category",
        "품목": "item",
        "품종": "variety",
        "등급": "grade",
        "단위": "unit_raw",
        "거래구분코드": "product_cls_code",
        "연도": "year",
        "평균가": "avg_price",
        "최고가": "max_price",
        "최저가": "min_price",
        "표준편차": "stddev",
        "변동계수": "cv",           # coefficient of variation
        "af": "amp_coef",           # 진폭계수 (amplitude coefficient)
        "단위_수량": "unit_qty",
        "단위_기준": "unit_base",
        "진행중": "in_progress",
    },
    schema=[
        _F("price_date", "DATE"),
        _F("trade_stage", "STRING"),
        _F("category", "STRING"),
        _F("item", "STRING"),
        _F("variety", "STRING"),
        _F("grade", "STRING"),
        _F("unit_raw", "STRING"),
        _F("product_cls_code", "STRING"),
        _F("year", "INTEGER"),
        _F("avg_price", "FLOAT"),
        _F("max_price", "FLOAT"),
        _F("min_price", "FLOAT"),
        _F("stddev", "FLOAT"),
        _F("cv", "FLOAT"),          # 변동계수
        _F("amp_coef", "FLOAT"),    # 진폭계수
        _F("unit_qty", "FLOAT"),
        _F("unit_base", "STRING"),
        _F("in_progress", "BOOLEAN"),
    ],
    partition_field="price_date",
)


# ──────────────────────────────────────────────────────────────
# datago 출하량 추이 (process_shipment)
#
# 파서 컬럼은 이미 영문이라 rename 은 '원문 영문 → 정돈된 영문' 이다.
# grain = 출하일 × 도매시장 × 법인 × 소분류. avg_spmt_amt/qty 는 평균
# 출하량/수량이고 ww1~4 는 각 1~4주 전 값(선행지표로 활용).
# ──────────────────────────────────────────────────────────────
SHIPMENT = TableSpec(
    name="datago_shipment",
    rename={
        "price_date": "price_date",
        "spmt_ymd": "shipment_ymd",
        "whsl_mrkt_cd": "market_code",
        "whsl_mrkt_nm": "market_name",
        "corp_cd": "corp_code",
        "corp_nm": "corp_name",
        "gds_lclsf_cd": "lclsf_code",
        "gds_lclsf_nm": "lclsf_name",
        "gds_mclsf_cd": "mclsf_code",
        "gds_mclsf_nm": "mclsf_name",
        "gds_sclsf_cd": "sclsf_code",
        "gds_sclsf_nm": "sclsf_name",
        "unit_cd": "unit_code",
        "unit_nm": "unit_name",
        "unit_qty": "unit_qty",
        "avg_spmt_qty": "avg_shipment_qty",
        "avg_spmt_amt": "avg_shipment_amt",
        "ww1_bfr_avg_spmt_qty": "w1ago_shipment_qty",
        "ww1_bfr_avg_spmt_amt": "w1ago_shipment_amt",
        "ww2_bfr_avg_spmt_qty": "w2ago_shipment_qty",
        "ww2_bfr_avg_spmt_amt": "w2ago_shipment_amt",
        "ww3_bfr_avg_spmt_qty": "w3ago_shipment_qty",
        "ww3_bfr_avg_spmt_amt": "w3ago_shipment_amt",
        "ww4_bfr_avg_spmt_qty": "w4ago_shipment_qty",
        "ww4_bfr_avg_spmt_amt": "w4ago_shipment_amt",
    },
    schema=[
        _F("price_date", "DATE"),
        _F("shipment_ymd", "STRING"),
        _F("market_code", "STRING"),
        _F("market_name", "STRING"),
        _F("corp_code", "STRING"),
        _F("corp_name", "STRING"),
        _F("lclsf_code", "STRING"),
        _F("lclsf_name", "STRING"),
        _F("mclsf_code", "STRING"),
        _F("mclsf_name", "STRING"),
        _F("sclsf_code", "STRING"),
        _F("sclsf_name", "STRING"),
        _F("unit_code", "STRING"),
        _F("unit_name", "STRING"),
        _F("unit_qty", "FLOAT"),
        _F("avg_shipment_qty", "FLOAT"),
        _F("avg_shipment_amt", "FLOAT"),
        _F("w1ago_shipment_qty", "FLOAT"),
        _F("w1ago_shipment_amt", "FLOAT"),
        _F("w2ago_shipment_qty", "FLOAT"),
        _F("w2ago_shipment_amt", "FLOAT"),
        _F("w3ago_shipment_qty", "FLOAT"),
        _F("w3ago_shipment_amt", "FLOAT"),
        _F("w4ago_shipment_qty", "FLOAT"),
        _F("w4ago_shipment_amt", "FLOAT"),
    ],
    partition_field="price_date",
)


# ──────────────────────────────────────────────────────────────
# MAFRA 농협계약재배 물량·도매가격 (process_contract)
#
# 파서가 대문자 코드 컬럼 + base_month 를 준다. grain = 기준월 ×
# 시도·시군구 × 품목·품종·등급. GROW_* 는 재배신청/계약(농가수·물량·금액),
# PMM_* 는 월별 가격통계, PYY_* 는 연별 가격통계.
# ──────────────────────────────────────────────────────────────
MAFRA_CONTRACT = TableSpec(
    name="mafra_contract_crop",
    rename={
        "base_month": "base_month",
        "CRTR_YM": "base_ym",
        "CTPRVN_NM": "sido",
        "CTNDCT_NM": "sigungu",
        "ITEM_NM": "item",
        "ITEM_DTL_NM": "item_detail",
        "GROW_APLY_FARM_CNT": "apply_farm_cnt",
        "GROW_CTRT_FARM_CNT": "contract_farm_cnt",
        "GROW_APLY_QTY": "apply_qty",
        "GROW_CTRT_QTY": "contract_qty",
        "GROW_APLY_AMT": "apply_amt",
        "GROW_CTRT_AMT": "contract_amt",
        "MART_ITEM_NM": "mart_item",
        "VRTY_NM": "variety",
        "GRD_NM": "grade",
        "UNIT": "unit",
        "UNIT_SZ": "unit_size",
        "PMM_AVGPRC": "month_avg_price",
        "PMM_HGPRC": "month_high_price",
        "PMM_LWPRC": "month_low_price",
        "PMM_STDDVTN": "month_stddev",
        "PYY_AVGPRC": "year_avg_price",
        "PYY_HGPRC": "year_high_price",
        "PYY_LWPRC": "year_low_price",
        "REG_DT": "reg_date",
        "REG_USERID": "reg_userid",
        "UPD_DT": "upd_date",
        "UPD_USERID": "upd_userid",
    },
    schema=[
        _F("base_month", "DATE"),
        _F("base_ym", "STRING"),
        _F("sido", "STRING"),
        _F("sigungu", "STRING"),
        _F("item", "STRING"),
        _F("item_detail", "STRING"),
        _F("apply_farm_cnt", "FLOAT"),
        _F("contract_farm_cnt", "FLOAT"),
        _F("apply_qty", "FLOAT"),
        _F("contract_qty", "FLOAT"),
        _F("apply_amt", "FLOAT"),
        _F("contract_amt", "FLOAT"),
        _F("mart_item", "STRING"),
        _F("variety", "STRING"),
        _F("grade", "STRING"),
        _F("unit", "STRING"),
        _F("unit_size", "STRING"),
        _F("month_avg_price", "FLOAT"),
        _F("month_high_price", "FLOAT"),
        _F("month_low_price", "FLOAT"),
        _F("month_stddev", "FLOAT"),
        _F("year_avg_price", "FLOAT"),
        _F("year_high_price", "FLOAT"),
        _F("year_low_price", "FLOAT"),
        _F("reg_date", "DATE"),
        _F("reg_userid", "STRING"),
        _F("upd_date", "DATE"),
        _F("upd_userid", "STRING"),
    ],
    partition_field="base_month",
)


# ──────────────────────────────────────────────────────────────
# 기상청 ASOS 일자료 (process_weather)
#
# grain = 관측일 × 지점. 가격·출하량과 같은 '일' 단위라 obs_date 로 바로
# 조인된다. precip_mm·snow_depth_cm 은 파서가 '안 온 날'을 0 으로 채워
# 보내므로 NULL 이 아니다(NULL 이면 AVG 에서 빠져 강수량이 과대계산된다).
# ──────────────────────────────────────────────────────────────
WEATHER = TableSpec(
    name="kma_weather_daily",
    rename={
        "obs_date": "obs_date",
        "stnId": "stn_id",
        "stnNm": "stn_name",
        "avgTa": "avg_temp",
        "minTa": "min_temp",
        "maxTa": "max_temp",
        "avgRhm": "avg_humidity",
        "minTg": "min_grass_temp",
        "sumSsHr": "sunshine_hr",
        "sumGsr": "solar_rad",
        "sumRn": "precip_mm",
        "ddMefs": "snow_depth_cm",
    },
    schema=[
        _F("obs_date", "DATE"),
        _F("stn_id", "STRING"),
        _F("stn_name", "STRING"),
        _F("avg_temp", "FLOAT"),
        _F("min_temp", "FLOAT"),
        _F("max_temp", "FLOAT"),
        _F("avg_humidity", "FLOAT"),
        _F("min_grass_temp", "FLOAT"),
        _F("sunshine_hr", "FLOAT"),
        _F("solar_rad", "FLOAT"),
        _F("precip_mm", "FLOAT"),
        _F("snow_depth_cm", "FLOAT"),
    ],
    partition_field="obs_date",
)


# ──────────────────────────────────────────────────────────────
# 한국천문연구원 특일정보 (process_special_day)
#
# grain = 특일 날짜 × 특일명. 한 날짜에 여러 행이 올 수 있다(설 연휴
# 사흘이 각각 한 행, 절기와 잡절이 겹치는 날 등).
#
# is_holiday_raw 는 API 원문 그대로다. 이름에 _raw 를 붙인 이유는 이 값이
# 제헌절·노동절까지 true 로 오는데 둘 다 관공서 공휴일이 아니어서다.
# 휴일 판정은 반드시 뷰(transform/views.py)를 거쳐야 한다.
#
# 파티셔닝하지 않는다. 연 53행 × 8년 ≈ 420행짜리 차원 테이블이라
# 파티션이 스캔량을 줄이지 못하고 메타데이터만 늘린다.
# ──────────────────────────────────────────────────────────────
SPECIAL_DAY = TableSpec(
    name="kasi_special_day",
    rename={
        "special_date": "special_date",
        "dateKind": "date_kind",
        "date_kind_name": "date_kind_name",
        "dateName": "date_name",
        "isHoliday": "is_holiday_raw",
        "seq": "seq",
        "kst": "kst",
    },
    schema=[
        _F("special_date", "DATE"),
        _F("date_kind", "STRING"),       # 01 공휴일 / 03 24절기 / 04 잡절
        _F("date_kind_name", "STRING"),
        _F("date_name", "STRING"),
        _F("is_holiday_raw", "BOOLEAN"),
        _F("seq", "INTEGER"),
        # 절기 진입 시각(HHMM). 사실상 24절기 전용이고 원문 보존용이다.
        # 시각으로 해석하면 안 된다 — 2024년 잡절 7행만 예외로 시각이
        # 아니라 MMDD 날짜가 들어있다(정월대보름 2024-02-24 → '0224').
        _F("kst", "STRING"),
    ],
)
