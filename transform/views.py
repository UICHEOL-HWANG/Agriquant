# transform/views.py
"""적재된 원천 테이블 → 분석용 BigQuery 뷰.

원천은 소스마다 grain 이 다르다.
  kamis_wholesale_obs : 날짜 × 품목 × 품종 × 지역 × 시장
  kma_weather_daily   : 날짜 × 관측지점
  datago_shipment     : 날짜 × 도매시장 × 법인 × 소분류

이 계층은 셋을 전부 '날짜 × 표준품목'으로 접어(v_*_daily) 하나로 조인한다
(mart_item_daily). 물리 테이블이 아니라 뷰인 이유는 원천이 append 로 계속
자라기 때문에 매번 재적재할 이유가 없고, 집계 로직을 고칠 때 뒤에서
CREATE OR REPLACE 한 번이면 끝나서다.

품목 매핑은 SQL 에 박지 않고 transform/dim.py 에서 주입한다.
"""
from __future__ import annotations

import logging

from google.cloud.exceptions import NotFound

from extract.database.connection import BigQueryConnection

from .dim import ItemDim, build_item_dims

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 원천 테이블 (extract/database/models.py 의 TableSpec.name 과 일치해야 한다)
T_WHOLESALE_OBS = "kamis_wholesale_obs"
T_WEATHER = "kma_weather_daily"
T_SHIPMENT = "datago_shipment"
T_SPECIAL_DAY = "kasi_special_day"

# 생성할 뷰
V_PRICE = "v_price_daily"
V_WEATHER = "v_weather_daily"
V_SHIPMENT = "v_shipment_daily"
V_CALENDAR = "v_calendar_daily"
V_MART = "mart_item_daily"

# 공휴일로 치지 않을 특일. 제헌절은 2008년부터 법정공휴일이 아닌데도 이 API
# 는 isHoliday='Y' 로 준다. 실제로 2026-07-17 출하량이 975행으로 정상
# 개장이었다. '대체공휴일(제헌절)' 도 있어 LIKE 로 함께 잡는다.
#
# 노동절은 남긴다. 관공서 공휴일은 아니지만 사기업 상당수가 휴무라 소비
# 측 효과가 있고, 도매시장이 열려도(2026-05-01 876행) 수요는 움직인다.
NON_HOLIDAY_LIKE = "%제헌절%"

# 명절. 연휴 전 수요 급등이 이 파이프라인에서 가장 중요한 달력 신호다.
MAJOR_HOLIDAYS = ("설날", "추석")


def _lit(s: str) -> str:
    """문자열을 SQL 리터럴로. 품목명에 작은따옴표가 들어갈 일은 없지만 방어."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


# ── 매핑 CTE (dim.py → SQL) ──────────────────────────────────
def _station_map_cte(dims: list[ItemDim]) -> str:
    """품목 × 관측지점 매핑을 SELECT ... UNION ALL 로 편다."""
    rows = [
        f"  SELECT {_lit(d.item)} AS item, {_lit(stn)} AS stn_id"
        for d in dims
        for stn in d.stations
    ]
    return "station_map AS (\n" + "\n  UNION ALL\n".join(rows) + "\n)"


def _datago_map_cte(dims: list[ItemDim]) -> str:
    """품목 × (대분류, 중분류) 매핑을 편다."""
    rows = [
        f"  SELECT {_lit(d.item)} AS item, {_lit(lc)} AS lclsf_code, {_lit(mc)} AS mclsf_code"
        for d in dims
        for lc, mc in d.datago_keys
    ]
    return "item_map AS (\n" + "\n  UNION ALL\n".join(rows) + "\n)"


# ── 뷰 SQL ───────────────────────────────────────────────────
def sql_price_daily(conn: BigQueryConnection) -> str:
    """날짜 × 품목 일별 도매가.

    no_trade 행은 제외한다. 배추는 관측치의 73%, 감자는 45% 가 미거래라
    (2026-07-30 확인) 그대로 평균에 넣으면 가격이 아니라 '거래 여부'를
    재게 된다. unit_base 는 전 품목 'kg' 이지만 원천이 바뀔 수 있으니
    조건으로 못 박아 단위가 섞이는 순간 조용히 틀리지 않게 한다.

    평균과 중앙값을 둘 다 낸다. 시장·품종이 섞여 있어 이상치 하나가
    평균을 끌고 가는데, 어느 쪽이 나은지는 모델을 돌려봐야 안다.
    """
    return f"""
SELECT
  item,
  price_date,
  AVG(unit_price)                            AS price_kg_avg,
  APPROX_QUANTILES(unit_price, 2)[OFFSET(1)] AS price_kg_med,
  MIN(unit_price)                            AS price_kg_min,
  MAX(unit_price)                            AS price_kg_max,
  STDDEV_SAMP(unit_price)                    AS price_kg_std,
  COUNT(*)                                   AS obs_cnt,
  COUNT(DISTINCT market)                     AS market_cnt,
  COUNT(DISTINCT variety)                    AS variety_cnt
FROM `{conn.table_id(T_WHOLESALE_OBS)}`
WHERE no_trade IS NOT TRUE
  AND unit_price IS NOT NULL
  AND unit_base = 'kg'
GROUP BY item, price_date
"""


def sql_weather_daily(conn: BigQueryConnection, dims: list[ItemDim]) -> str:
    """날짜 × 품목 기상. 해당 품목 주산지 지점들을 접는다.

    수준값(기온·습도·일조)은 지점 평균, 극단값(최저·최고기온)은 지점
    전체의 min/max 를 쓴다. 냉해·고온피해는 '어느 한 산지'에서만 나도
    출하가 무너져 가격에 반영되기 때문에 평균으로 뭉개면 신호가 죽는다.

    강수량은 평균과 최대를 둘 다 낸다. 원천 파서가 '안 온 날'을 0 으로
    채워 보내므로 AVG 에서 빠지지 않는다(models.py WEATHER 주석 참고).
    """
    return f"""
WITH {_station_map_cte(dims)}
SELECT
  m.item,
  w.obs_date,
  AVG(w.avg_temp)       AS temp_avg,
  MIN(w.min_temp)       AS temp_min,
  MAX(w.max_temp)       AS temp_max,
  MIN(w.min_grass_temp) AS grass_temp_min,
  AVG(w.avg_humidity)   AS humidity_avg,
  AVG(w.sunshine_hr)    AS sunshine_hr_avg,
  AVG(w.solar_rad)      AS solar_rad_avg,
  AVG(w.precip_mm)      AS precip_mm_avg,
  MAX(w.precip_mm)      AS precip_mm_max,
  MAX(w.snow_depth_cm)  AS snow_depth_cm_max,
  COUNT(DISTINCT w.stn_id) AS stn_cnt
FROM station_map m
JOIN `{conn.table_id(T_WEATHER)}` w
  ON w.stn_id = m.stn_id
GROUP BY m.item, w.obs_date
"""


def sql_shipment_daily(conn: BigQueryConnection, dims: list[ItemDim]) -> str:
    """날짜 × 품목 출하량. 도매시장·법인·소분류를 합산한다.

    원천의 avg_shipment_qty 는 '해당 행 단위의 평균 출하량'이라 엄밀한
    총량이 아니다. 여기서 SUM 하는 값은 총출하량의 대리지표로 읽어야
    하고, 절대량보다 같은 품목의 시계열 변화에 의미가 있다.

    w1~w4ago 는 원천이 이미 들고 있는 1~4주 전 값이라 그대로 접어둔다.
    LAG 로 다시 만들지 않는 이유는 결측일(미거래일)이 섞여 있어 행 기준
    LAG 가 '7일 전'을 보장하지 않기 때문이다.
    """
    return f"""
WITH {_datago_map_cte(dims)}
SELECT
  m.item,
  s.price_date,
  SUM(s.avg_shipment_qty)   AS shipment_qty,
  SUM(s.avg_shipment_amt)   AS shipment_amt,
  SUM(s.w1ago_shipment_qty) AS shipment_qty_w1ago,
  SUM(s.w2ago_shipment_qty) AS shipment_qty_w2ago,
  SUM(s.w3ago_shipment_qty) AS shipment_qty_w3ago,
  SUM(s.w4ago_shipment_qty) AS shipment_qty_w4ago,
  COUNT(DISTINCT s.market_code) AS market_cnt,
  COUNT(DISTINCT s.corp_code)   AS corp_cnt,
  COUNT(*)                      AS row_cnt
FROM `{conn.table_id(T_SHIPMENT)}` s
JOIN item_map m
  ON s.lclsf_code = m.lclsf_code
 AND s.mclsf_code = m.mclsf_code
GROUP BY m.item, s.price_date
"""


def sql_calendar_daily(conn: BigQueryConnection) -> str:
    """날짜 달력. 특일정보를 '하루 한 행'으로 펴고 전후 거리를 붙인다.

    원천은 특일이 있는 날만 있는 성긴 테이블(8년 407행)이라 그대로는
    조인이 안 된다. GENERATE_DATE_ARRAY 로 전 구간 달력을 세우고 특일을
    붙여야 '명절까지 며칠 남았나' 같은 값이 매일 존재하게 된다.

    이 뷰의 핵심은 is_holiday 가 아니라 days_to_major_holiday 다. 배추·무
    도매가는 명절 *직전*에 뛰고 명절 당일은 휴장이라 가격 행 자체가 없다.
    당일 플래그만으로는 그 급등을 절대 설명하지 못한다.

    is_market_closed 를 '공휴일'로 정의하지 않은 이유도 같은 맥락이다.
    출하량으로 확인해보니 광복절 860행·한글날 650행·노동절 876행·제헌절
    975행 전부 정상 개장이었다. 실제로 멈추는 날은 명절 연휴(0행)와
    신정(9행), 그리고 일요일뿐이라 그 셋만 휴장으로 본다.

    kst(절기 진입 시각)는 쓰지 않는다. 2024년 잡절 7행에만 시각이 아니라
    MMDD 가 들어있어(정월대보름 → '0224') 값을 신뢰할 수 없다.
    """
    src = conn.table_id(T_SPECIAL_DAY)
    majors = ", ".join(_lit(h) for h in MAJOR_HOLIDAYS)
    return f"""
-- 달력 구간은 특일의 최소·최대가 아니라 그 '연도'의 양 끝으로 넓힌다.
-- 원천 그대로 두면 마지막 특일(성탄절)에서 끊겨 12/26~31 이 통째로
-- 빠지고, 그 며칠이 마트에서 조용히 NULL 이 된다.
WITH bounds AS (
  SELECT
    DATE(EXTRACT(YEAR FROM MIN(special_date)), 1, 1)   AS lo,
    DATE(EXTRACT(YEAR FROM MAX(special_date)), 12, 31) AS hi
  FROM `{src}`
),
cal AS (
  SELECT d AS cal_date
  FROM bounds, UNNEST(GENERATE_DATE_ARRAY(lo, hi)) AS d
),
major AS (
  SELECT DISTINCT special_date
  FROM `{src}`
  WHERE date_kind = '01' AND date_name IN ({majors})
),
public_holiday AS (
  SELECT
    special_date,
    STRING_AGG(DISTINCT date_name, ', ' ORDER BY date_name) AS holiday_name
  FROM `{src}`
  WHERE date_kind = '01'
    AND date_name NOT LIKE {_lit(NON_HOLIDAY_LIKE)}
  GROUP BY special_date
),
new_year AS (
  SELECT DISTINCT special_date
  FROM `{src}`
  WHERE date_kind = '01' AND date_name = '1월1일'
),
term AS (
  SELECT special_date, date_name FROM `{src}` WHERE date_kind = '03'
),
sundry AS (
  SELECT special_date, STRING_AGG(date_name, ', ') AS sundry_name
  FROM `{src}`
  WHERE date_kind = '04'
  GROUP BY special_date
),
-- 다가오는/지나간 명절까지의 거리. 407행 × 2,922일이라 CROSS JOIN 해도
-- 120만 행뿐이고, 뷰라서 조회 시점에만 계산된다.
major_dist AS (
  SELECT
    c.cal_date,
    MIN(IF(m.special_date >= c.cal_date,
           DATE_DIFF(m.special_date, c.cal_date, DAY), NULL)) AS days_to_major_holiday,
    MIN(IF(m.special_date <= c.cal_date,
           DATE_DIFF(c.cal_date, m.special_date, DAY), NULL)) AS days_since_major_holiday
  FROM cal c
  CROSS JOIN major m
  GROUP BY c.cal_date
),
-- 직전(당일 포함) 절기 = 계절 위상. 절기는 양력으로 매년 거의 고정이라
-- month 더미와 겹치지만, 경계가 15일 단위라 훨씬 촘촘하다.
term_pos AS (
  SELECT
    c.cal_date,
    ARRAY_AGG(t.date_name ORDER BY t.special_date DESC LIMIT 1)[OFFSET(0)]
      AS current_solar_term,
    DATE_DIFF(c.cal_date, MAX(t.special_date), DAY) AS days_since_solar_term
  FROM cal c
  JOIN term t ON t.special_date <= c.cal_date
  GROUP BY c.cal_date
)
SELECT
  c.cal_date,
  EXTRACT(DAYOFWEEK FROM c.cal_date) AS dayofweek,
  ph.holiday_name IS NOT NULL       AS is_public_holiday,
  ph.holiday_name,
  mj.special_date IS NOT NULL       AS is_major_holiday,
  md.days_to_major_holiday,
  md.days_since_major_holiday,
  md.days_to_major_holiday BETWEEN 1 AND 7 AS is_pre_holiday_week,
  (
    mj.special_date IS NOT NULL
    OR ny.special_date IS NOT NULL
    OR EXTRACT(DAYOFWEEK FROM c.cal_date) = 1
  )                                 AS is_market_closed,
  tm.date_name                      AS solar_term,
  tp.current_solar_term,
  tp.days_since_solar_term,
  sd.sundry_name
FROM cal c
LEFT JOIN public_holiday ph ON ph.special_date = c.cal_date
LEFT JOIN major mj          ON mj.special_date = c.cal_date
LEFT JOIN new_year ny       ON ny.special_date = c.cal_date
LEFT JOIN term tm           ON tm.special_date = c.cal_date
LEFT JOIN sundry sd         ON sd.special_date = c.cal_date
LEFT JOIN major_dist md     ON md.cal_date = c.cal_date
LEFT JOIN term_pos tp       ON tp.cal_date = c.cal_date
"""


def sql_mart(conn: BigQueryConnection, *, with_shipment: bool, with_calendar: bool) -> str:
    """날짜 × 품목 통합 마트. 가격을 기준으로 기상·출하량을 붙인다.

    가격이 예측 대상이라 가격 행을 기준(FROM)으로 두고 나머지를 LEFT JOIN
    한다. 거래가 없던 날은 애초에 행이 없으므로 달력을 채우지 않는다 —
    없는 날의 가격을 0 이나 보간값으로 만들어내면 학습이 그 가짜를 배운다.

    with_shipment=False 면 출하량 컬럼을 뺀 채로 만든다(datago 미적재 상태).
    with_calendar 도 같다(특일정보 미적재 상태).

    달력은 품목과 무관해 날짜만으로 붙는다. 기존 dayofweek 는 여기서 이미
    EXTRACT 로 내고 있어 달력 뷰의 것과 중복이라 가져오지 않는다.
    """
    ship_cols = """,
  s.shipment_qty,
  s.shipment_amt,
  s.shipment_qty_w1ago,
  s.shipment_qty_w2ago,
  s.shipment_qty_w3ago,
  s.shipment_qty_w4ago,
  s.market_cnt AS shipment_market_cnt"""
    ship_join = f"""
LEFT JOIN `{conn.table_id(V_SHIPMENT)}` s
  ON s.item = p.item
 AND s.price_date = p.price_date"""

    cal_cols = """,
  c.is_public_holiday,
  c.holiday_name,
  c.is_major_holiday,
  c.days_to_major_holiday,
  c.days_since_major_holiday,
  c.is_pre_holiday_week,
  c.is_market_closed,
  c.solar_term,
  c.current_solar_term,
  c.days_since_solar_term,
  c.sundry_name"""
    cal_join = f"""
LEFT JOIN `{conn.table_id(V_CALENDAR)}` c
  ON c.cal_date = p.price_date"""

    return f"""
SELECT
  p.item,
  p.price_date,
  EXTRACT(YEAR      FROM p.price_date) AS year,
  EXTRACT(MONTH     FROM p.price_date) AS month,
  EXTRACT(DAYOFWEEK FROM p.price_date) AS dayofweek,
  p.price_kg_avg,
  p.price_kg_med,
  p.price_kg_min,
  p.price_kg_max,
  p.price_kg_std,
  p.obs_cnt,
  p.market_cnt AS price_market_cnt,
  p.variety_cnt,
  w.temp_avg,
  w.temp_min,
  w.temp_max,
  w.grass_temp_min,
  w.humidity_avg,
  w.sunshine_hr_avg,
  w.solar_rad_avg,
  w.precip_mm_avg,
  w.precip_mm_max,
  w.snow_depth_cm_max,
  w.stn_cnt{ship_cols if with_shipment else ""}{cal_cols if with_calendar else ""}
FROM `{conn.table_id(V_PRICE)}` p
LEFT JOIN `{conn.table_id(V_WEATHER)}` w
  ON w.item = p.item
 AND w.obs_date = p.price_date{ship_join if with_shipment else ""}{cal_join if with_calendar else ""}
"""


# ── 실행 ─────────────────────────────────────────────────────
def _table_exists(conn: BigQueryConnection, name: str) -> bool:
    try:
        conn.client.get_table(conn.table_id(name))
        return True
    except NotFound:
        return False


def _create_view(conn: BigQueryConnection, name: str, sql: str) -> None:
    """뷰를 멱등 생성. 정의가 바뀌면 덮어쓴다."""
    conn.client.query(
        f"CREATE OR REPLACE VIEW `{conn.table_id(name)}` AS\n{sql}"
    ).result()
    logging.info(f"[BigQuery] 뷰 생성: {name}")


def build_all(conn: BigQueryConnection | None = None) -> list[str]:
    """분석용 뷰를 전부(가능한 것만) 생성한다. 생성한 뷰 이름 목록 반환.

    datago_shipment 는 수집이 오래 걸려 아직 없을 수 있다. 없으면 출하량
    뷰를 건너뛰고 마트도 출하량 컬럼 없이 만든다 — 뷰는 CREATE OR REPLACE
    라 datago 적재가 끝난 뒤 다시 돌리면 컬럼이 붙는다.
    kasi_special_day(달력)도 같은 방식으로 다룬다.
    """
    conn = conn or BigQueryConnection()
    dims = build_item_dims()
    logging.info(f"=== transform 시작: 품목 {[d.item for d in dims]} ===")

    created: list[str] = []
    _create_view(conn, V_PRICE, sql_price_daily(conn))
    created.append(V_PRICE)
    _create_view(conn, V_WEATHER, sql_weather_daily(conn, dims))
    created.append(V_WEATHER)

    has_shipment = _table_exists(conn, T_SHIPMENT)
    if has_shipment:
        _create_view(conn, V_SHIPMENT, sql_shipment_daily(conn, dims))
        created.append(V_SHIPMENT)
    else:
        logging.warning(
            f"{T_SHIPMENT} 테이블이 없어 출하량 뷰를 건너뜁니다. "
            "datago 적재 후 transform 을 다시 실행하면 마트에 컬럼이 붙습니다."
        )

    has_calendar = _table_exists(conn, T_SPECIAL_DAY)
    if has_calendar:
        _create_view(conn, V_CALENDAR, sql_calendar_daily(conn))
        created.append(V_CALENDAR)
    else:
        logging.warning(
            f"{T_SPECIAL_DAY} 테이블이 없어 달력 뷰를 건너뜁니다. "
            "`--source holiday` 적재 후 transform 을 다시 실행하세요."
        )

    _create_view(
        conn, V_MART,
        sql_mart(conn, with_shipment=has_shipment, with_calendar=has_calendar),
    )
    created.append(V_MART)

    logging.info(f"=== transform 완료: {created} ===")
    return created
