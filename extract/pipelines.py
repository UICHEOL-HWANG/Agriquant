# extract/pipelines.py
"""수집 → 파싱 → BigQuery 적재 파이프라인.

수집 대상 품목은 BigQuery `item_code` 테이블에서 동적으로 읽어온다
(repo.load_item_targets). 각 run_* 은 품목 목록을 돌며 품목별로
KAMIS를 호출·파싱하고, 전 품목을 concat 해 한 번에 적재한다.

품목마다 category_code 가 다를 수 있으므로(감자=100) 항상 ItemTarget 의
category_code 를 그대로 API에 넘긴다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from extract.clients.datago_client import DatagoClient
from extract.clients.kamis_client import KamisClient
from extract.clients.kasi_client import OPERATIONS, KasiClient
from extract.clients.kma_client import KmaClient
from extract.clients.mafra_client import MafraClient
from extract.config.items import (
    DATAGO_VEGETABLES,
    DEFAULT_VEGETABLES,
    WEATHER_STATIONS,
    DatagoTarget,
    ItemTarget,
    WeatherStation,
)
from extract.database import BigQueryRepository
from extract.parsers.datago_parser import process_shipment
from extract.parsers.kamis_parser import clean, process_price_json, process_yearly_json
from extract.parsers.kasi_parser import process_special_day
from extract.parsers.kma_parser import process_weather
from extract.parsers.mafra_parser import process_contract

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """비어있지 않은 DataFrame 만 이어붙인다."""
    frames = [f for f in frames if f is not None and not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── 개별 파이프라인 (items 를 순회) ──────────────────────────
def run_monthly(
    kamis: KamisClient,
    repo: BigQueryRepository,
    items: list[ItemTarget],
    *,
    p_yyyy: str = "2025",
    p_period: str = "3",
    mode: str = "append",
) -> int:
    """월별 도·소매 가격 → kamis_monthly_price."""
    frames = []
    for it in items:
        resp = kamis.monthlySalesList(
            p_yyyy=p_yyyy,
            p_period=p_period,
            p_itemcategorycode=it.category_code,
            p_itemcode=it.item_code,
        )
        df = process_price_json(resp)
        logging.info(f"  monthly {it.item_name}({it.item_code}): {len(df):,}행")
        frames.append(df)
    return repo.save_monthly(_concat(frames), mode)


def run_yearly(
    kamis: KamisClient,
    repo: BigQueryRepository,
    items: list[ItemTarget],
    *,
    p_yyyy: str = "2026",
    mode: str = "append",
) -> int:
    """연도별 가격 통계 → kamis_yearly_price."""
    frames = []
    for it in items:
        resp = kamis.yearlySalesList(p_yyyy, it.category_code, it.item_code)
        df = process_yearly_json(resp)
        logging.info(f"  yearly {it.item_name}({it.item_code}): {len(df):,}행")
        frames.append(df)
    return repo.save_yearly(_concat(frames), mode)


def run_wholesale(
    kamis: KamisClient,
    repo: BigQueryRepository,
    items: list[ItemTarget],
    *,
    mode: str = "append",
    product_cls_code: str = "01",
    **date_kwargs,   # p_startday / p_endday 오버라이드용
) -> dict[str, int]:
    """일별 도매가 → kamis_wholesale_obs / kamis_wholesale_agg."""
    buckets: dict[str, list[pd.DataFrame]] = {"관측치": [], "전체평균": [], "평년기준선": []}
    for it in items:
        resp = kamis.get_period_wholesale_product_list(
            category_code=it.category_code,
            p_itemcode=it.item_code,
            product_cls_code=product_cls_code,
            **date_kwargs,
        )
        parts = clean(resp)
        logging.info(f"  wholesale {it.item_name}({it.item_code}): 관측치 {len(parts['관측치']):,}행")
        for key, bucket in buckets.items():
            bucket.append(parts[key])
    merged = {key: _concat(bucket) for key, bucket in buckets.items()}
    return repo.save_wholesale(merged, mode)


# ── KAMIS 도매가 과거 이력 백필 ──────────────────────────────
def _year_ranges(start: date, end: date) -> list[tuple[str, str]]:
    """[start, end] 를 연도 경계로 쪼갠다.

    KAMIS periodWholesaleProductList 는 1회 요청 최대 1년이라
    여러 해를 한 번에 못 받는다. 양 끝 해는 부분 구간으로 잘린다.
    """
    ranges = []
    for y in range(start.year, end.year + 1):
        s = max(start, date(y, 1, 1))
        e = min(end, date(y, 12, 31))
        ranges.append((s.isoformat(), e.isoformat()))
    return ranges


def run_wholesale_backfill(
    kamis: KamisClient,
    repo: BigQueryRepository,
    items: list[ItemTarget],
    *,
    start: date,
    end: date,
    mode: str = "append",
    product_cls_code: str = "01",
) -> dict[str, int]:
    """일별 도매가 과거 이력 백필 → kamis_wholesale_obs / _agg.

    연 단위로 끊어 수집·적재한다. replace 모드는 첫 해만 테이블을 교체하고
    이후는 append 로 이어 붙인다(run_shipment 과 같은 사상). 해마다 적재해서
    중간에 끊겨도 그때까지는 남는다.
    """
    totals: dict[str, int] = {}
    first = True
    for s, e in _year_ranges(start, end):
        logging.info(f"=== wholesale 백필 {s} ~ {e} ===")
        res = run_wholesale(
            kamis, repo, items,
            mode=mode if first else "append",
            product_cls_code=product_cls_code,
            p_startday=s, p_endday=e,
        )
        first = False
        for table, n in res.items():
            totals[table] = totals.get(table, 0) + n
    logging.info(f"=== 백필 완료: {totals} ===")
    return totals


def run_kamis_backfill(
    *,
    item_names: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    years: int = 5,
    mode: str = "append",
) -> dict[str, int]:
    """KAMIS 일별 도매가 백필 진입점.

    기간을 안 주면 '최근 years 개 연도 + 올해'를 받는다. years=5, 오늘이
    2026년이면 2021-01-01 ~ 오늘. 연 단위로 끊는 편이 계절성 비교에 깔끔해
    딱 5년 전 같은 날이 아니라 연초로 맞춘다.
    """
    kamis = KamisClient()
    repo = BigQueryRepository()

    items = repo.load_item_targets(item_names=item_names or DEFAULT_VEGETABLES)
    if not items:
        logging.warning("수집 대상 품목이 없습니다. 중단.")
        return {}

    if end is None:
        end = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if start is None:
        start = date(end.year - years, 1, 1)

    logging.info(
        f"=== KAMIS 도매가 백필 시작: {start}~{end}, "
        f"품목 {[i.item_name for i in items]} ==="
    )
    return run_wholesale_backfill(kamis, repo, items, start=start, end=end, mode=mode)


# ── 기상청 ASOS 일자료 파이프라인 ────────────────────────────
def run_weather(
    *,
    stations: list[WeatherStation] | None = None,
    start: date | None = None,
    end: date | None = None,
    years: int = 5,
    mode: str = "append",
) -> int:
    """기상청 ASOS 일자료 → kma_weather_daily.

    지점 × 연도로 끊어 수집한다(ASOS 는 요청당 999행 상한이라 1년이 딱
    1요청). 연 단위로 모아 적재해서 중간에 끊겨도 그때까지는 남는다.
    replace 모드는 첫 flush 만 테이블을 교체하고 이후는 append.

    기간 기본값은 run_kamis_backfill 과 맞춰 '최근 years 개 연도 + 올해'.
    가격 데이터와 같은 구간이어야 obs_date 로 조인된다.
    """
    client = KmaClient()
    repo = BigQueryRepository()
    stations = stations or WEATHER_STATIONS

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if end is None:
        end = today
    if start is None:
        start = date(end.year - years, 1, 1)

    # ASOS 는 '전날 자료까지' 제공한다. 오늘/미래를 포함해 요청하면
    # code=99 로 거부되므로 상한을 어제로 낮춘다.
    yesterday = today - timedelta(days=1)
    if end > yesterday:
        logging.info(f"  ASOS 는 전날 자료까지 제공 → 종료일 {end} → {yesterday}")
        end = yesterday

    logging.info(
        f"=== ASOS 기상 수집 시작: {start}~{end}, "
        f"지점 {len(stations)}개 {[s.stn_name for s in stations]} ==="
    )

    total = 0
    first = True
    for s, e in _year_ranges(start, end):
        ys, ye = date.fromisoformat(s), date.fromisoformat(e)
        frames: list[pd.DataFrame] = []
        for st in stations:
            rows = client.iter_daily(st.stn_id, ys, ye)
            df = process_weather(rows)
            logging.info(f"  weather {s[:4]} {st.stn_name}({st.stn_id}): {len(df):,}행")
            frames.append(df)
        n = repo.save_weather(_concat(frames), mode if first else "append")
        first = False
        total += n

    logging.info(f"=== ASOS 완료: {total:,}행 적재 ===")
    return total


# ── datago 출하량 파이프라인 ──────────────────────────────────
def _daterange(start: date, end: date):
    """start~end(포함) 일자 순회."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def run_shipment(
    client: DatagoClient,
    repo: BigQueryRepository,
    targets: list[DatagoTarget],
    *,
    start: date,
    end: date,
    mode: str = "append",
) -> int:
    """datago 출하량 추이 → datago_shipment.

    날짜(start~end)를 하루씩 돌며 품목별(대분류·중분류) 전량을 수집한다.
    메모리·재개 안정성을 위해 '월' 단위로 모아 적재(flush)한다.
    replace 모드는 첫 flush 만 테이블을 교체(WRITE_TRUNCATE)하고, 이후
    월은 append 로 이어 붙인다(월별 truncate 로 앞 데이터가 지워지지 않게).
    """
    total = 0
    frames: list[pd.DataFrame] = []
    cur_month: tuple[int, int] | None = None
    first_flush = True

    def flush() -> int:
        nonlocal frames, first_flush
        if not frames:
            return 0
        merged = _concat(frames)
        frames = []
        m = mode if first_flush else "append"
        first_flush = False
        return repo.save_shipment(merged, m)

    for d in _daterange(start, end):
        ym = (d.year, d.month)
        if cur_month is not None and ym != cur_month:
            total += flush()
        cur_month = ym

        ymd = d.strftime("%Y%m%d")
        day_rows = 0
        for t in targets:
            rows = client.iter_shipment(ymd, t.lclsf_code, t.mclsf_code)
            df = process_shipment(rows)
            day_rows += len(df)
            frames.append(df)
        logging.info(f"  shipment {ymd}: {day_rows:,}행 (품목 {len(targets)}개)")

    total += flush()   # 마지막 달
    logging.info(f"=== shipment 완료: {total:,}행 적재 ===")
    return total


def run_datago(
    *,
    targets: list[DatagoTarget] | None = None,
    start: date | None = None,
    end: date | None = None,
    days: int = 365,
    mode: str = "append",
) -> int:
    """datago 출하량 수집 진입점.

    기간을 안 주면 '최근 days 일'(기본 365 = 최근 1년)을 수집한다.
    """
    client = DatagoClient()
    repo = BigQueryRepository()
    targets = targets or DATAGO_VEGETABLES

    if end is None:
        end = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if start is None:
        start = end - timedelta(days=days - 1)

    logging.info(
        f"=== datago shipment 시작: {start}~{end} "
        f"({(end - start).days + 1}일), 품목 {[t.item_name for t in targets]} ==="
    )
    return run_shipment(client, repo, targets, start=start, end=end, mode=mode)


# ── KASI 특일정보 파이프라인 ─────────────────────────────────
# 미래 몇 해까지 받을지. 특일정보는 미래 날짜도 확정 제공하는데, 예측
# 시점에 '앞으로 올 명절'을 알아야 days_to_major_holiday 가 성립한다.
# 2026-08-04 실측: 2028년까지 제공(대체공휴일 일부 미확정), 2029년부터 0행.
_HOLIDAY_FUTURE_YEARS = 2


def run_holiday(
    *,
    start_year: int | None = None,
    end_year: int | None = None,
    years: int = 5,
    mode: str = "replace",
) -> int:
    """한국천문연구원 특일정보 → kasi_special_day.

    연 단위로 공휴일·24절기·잡절을 받는다. 전 구간을 받아도 400여 행뿐이라
    datago 처럼 중간 flush 를 할 이유가 없어 전량을 모아 한 번에 적재한다.
    그래서 mode 기본값도 replace 다(run_mafra 와 같은 사상) — append 하면
    실행할 때마다 통째로 중복되고, 뒤늦게 고시되는 대체공휴일도 갱신되지
    않는다.

    기간 기본값은 run_kamis_backfill·run_weather 와 맞춘 '최근 years 개
    연도'에 미래 2년을 더한 구간이다. 가격·기상과 같은 구간이어야 뷰에서
    날짜로 조인되고, 미래분은 예측용 달력 피처로 쓴다.

    아직 고시되지 않은 연도는 정상적으로 0행이라 오류로 보지 않고 넘어간다.
    """
    client = KasiClient()
    repo = BigQueryRepository()

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if end_year is None:
        end_year = today.year + _HOLIDAY_FUTURE_YEARS
    if start_year is None:
        start_year = today.year - years

    logging.info(
        f"=== KASI 특일정보 수집 시작: {start_year}~{end_year} "
        f"({end_year - start_year + 1}개 연도 × {len(OPERATIONS)}종) ==="
    )

    frames: list[pd.DataFrame] = []
    for year in range(start_year, end_year + 1):
        rows = client.iter_special_days(year)
        df = process_special_day(rows)
        if df.empty:
            logging.warning(f"  holiday {year}: 0행 (미고시 연도로 보임)")
            continue
        kinds = df["date_kind_name"].value_counts().to_dict()
        logging.info(f"  holiday {year}: {len(df):,}행 {kinds}")
        frames.append(df)

    merged = _concat(frames)
    n = repo.save_special_day(merged, mode)
    logging.info(f"=== KASI 특일정보 완료: {n:,}행 적재 ({mode}) ===")
    return n


# ── MAFRA 계약재배 파이프라인 ────────────────────────────────
def run_mafra(*, mode: str = "replace") -> int:
    """MAFRA 농협계약재배 물량·도매가격 → mafra_contract_crop.

    데이터가 작은 스냅샷(수백 행, 특정 기준월)이라 전량을 한 번에
    수집·적재한다. 매번 전량이므로 기본 mode 는 replace(멱등 재적재).

    수집이 totalCnt 에 못 미치면 iter_grid 가 GridFetchError 를 던져
    save_contract 도달 전에 중단된다. 잘린 데이터로 테이블을 덮어쓰지
    않게 하려는 것이므로 여기서 잡지 않고 그대로 올린다.
    """
    client = MafraClient()
    repo = BigQueryRepository()

    logging.info("=== MAFRA 계약재배 수집 시작 ===")
    rows = client.iter_contract_crop_price()
    df = process_contract(rows)
    logging.info(f"  contract: {len(df):,}행 수집")
    n = repo.save_contract(df, mode)
    logging.info(f"=== MAFRA 완료: {n:,}행 적재 ({mode}) ===")
    return n


# 이름 → 파이프라인 (CLI 대상 선택용)
PIPELINES = {
    "monthly": run_monthly,
    "yearly": run_yearly,
    "wholesale": run_wholesale,
}


# ── 통합 실행 ────────────────────────────────────────────────
def run_all(
    targets: list[str] | None = None,
    *,
    item_names: list[str] | None = None,
    mode: str = "append",
) -> dict:
    """대상 파이프라인(기본: 전체)을 수집 품목 전체에 대해 실행한다.

    targets    : ["monthly", "yearly"] 처럼 일부만. None 이면 전체.
    item_names : 수집할 품목명. None 이면 DEFAULT_VEGETABLES.
    mode       : "append"(이어 쌓기) 또는 "replace"(테이블 교체).
    """
    kamis = KamisClient()
    repo = BigQueryRepository()

    items = repo.load_item_targets(item_names=item_names or DEFAULT_VEGETABLES)
    logging.info(f"수집 대상 {len(items)}개: {[i.item_name for i in items]}")
    if not items:
        logging.warning("수집 대상 품목이 없습니다. 중단.")
        return {}

    targets = targets or list(PIPELINES)
    results: dict = {}
    for name in targets:
        logging.info(f"=== {name} 파이프라인 시작 ===")
        results[name] = PIPELINES[name](kamis, repo, items, mode=mode)

    logging.info(f"=== 완료: {results} ===")
    return results
