"""KAMIS 응답 파서.

API 호출·파일 저장 없음. 순수 변환만 한다.
클라이언트를 import하지 않으므로 인증키 없이도 동작한다.

    process_price_json()  monthlySalesList           caption + m1~m12 wide
    process_yearly_json() yearlySalesList            caption + div(연도) 통계 long
    clean()               periodWholesaleProductList long, 관측치·평균·평년 혼재
"""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

CAPTION_FIELDS = ["거래단계", "부류", "품목", "품종", "등급", "단위"]

# 집계행 식별자 — 관측치가 아니다(grain이 다름)
AGG_LABELS = {"평균", "평년"}

UNIT_RE = re.compile(r"([\d.]+)\s*(kg|g|개|포기|단|망|상자|박스)")


# ──────────────────────────────────────────────────────────────
# 공통
# ──────────────────────────────────────────────────────────────
def parse_unit(unit_text: str | None) -> tuple[float | None, str | None]:
    """'10kg(그물망 3포기)' → (10.0, 'kg'). 파싱 실패 시 (None, None).

    caption 필드 수가 품목마다 달라 단위가 없을 수 있다(None/NaN).
    float NaN 은 truthy 라 `or ''` 로 못 거르므로 타입으로 방어한다.
    """
    if not isinstance(unit_text, str):
        return (None, None)
    m = UNIT_RE.search(unit_text)
    return (float(m.group(1)), m.group(2)) if m else (None, None)


def _item_list(entry: dict) -> list:
    """entry['item'] 를 리스트로 정규화.

    KAMIS는 원소가 1개면 리스트가 아니라 dict 로, 없으면 None/생략으로 준다.
    (예: 생강 소매 중품 = 단일 dict, 미나리 소매 중품 = None)
    """
    it = entry.get("item")
    if it is None:
        return []
    return it if isinstance(it, list) else [it]


def _price_entries(data) -> list:
    """응답 전체(dict) 또는 price 값을 계열 리스트로 정규화."""
    if isinstance(data, dict):
        data = data.get("price", [])
    if isinstance(data, dict):   # 계열이 1개면 dict 로 온다
        data = [data]
    return data or []


def _to_price(s: pd.Series) -> pd.Series:
    """'16,760' → 16760.0 / '-' → NaN. 0원과 미거래를 구분한다."""
    cleaned = s.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned.replace({"-": None, "": None}), errors="coerce")


def _to_date(yyyy: pd.Series, regday: pd.Series) -> pd.Series:
    """yyyy(2025) + regday(01/02) → 2025-01-02"""
    return pd.to_datetime(yyyy + "/" + regday, format="%Y/%m/%d", errors="coerce")


# ──────────────────────────────────────────────────────────────
# 월별 가격 (monthlySalesList)
# ──────────────────────────────────────────────────────────────
def process_price_json(data, cutoff: date | None = None) -> pd.DataFrame:
    """KAMIS 월별 가격 응답 → Long Format DataFrame.

    data   : 응답 전체(dict) 또는 price 리스트 둘 다 허용
    cutoff : '오늘'. 생략하면 한국 시간 기준 현재 날짜를 쓴다.
             미래 월 판별과 연평균 룩어헤드 차단에 사용.
    """
    if cutoff is None:
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()

    data = _price_entries(data)
    if not data:
        return pd.DataFrame()

    rows = []
    for entry in data:
        caption = entry.get("caption", "")
        parts = [p.strip() for p in caption.split(">")]
        meta = dict(zip(CAPTION_FIELDS, parts))   # 인덱스 하드코딩 제거
        meta["caption_원문"] = caption            # ★ 원문 보존
        meta["caption_필드수"] = len(parts)       # 구조 변경 감지용

        for item in _item_list(entry):
            year_raw = str(item.get("yyyy", ""))
            year = int(year_raw) if year_raw.isdigit() else None

            for m in range(1, 13):
                # 아직 오지 않은 미래 월과 진짜 결측을 구분
                is_future = year is not None and (
                    (year, m) > (cutoff.year, cutoff.month)
                )
                rows.append({**meta, "연도": year, "월": m,
                             "가격_원문": item.get(f"m{m}", "-"),
                             "연평균_원문": item.get("yearavg", "-"),
                             "미래시점": is_future})

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["가격"] = _to_price(df["가격_원문"])
    df["연평균"] = _to_price(df["연평균_원문"])
    # ★ 연평균은 그 해가 끝나야 확정 → 진행 중인 해는 마스킹(룩어헤드 차단)
    df.loc[df["연도"] >= cutoff.year, "연평균"] = pd.NA

    df["날짜"] = pd.to_datetime(
        df["연도"].astype("Int64").astype(str) + "-"
        + df["월"].astype(str).str.zfill(2) + "-01",
        errors="coerce")

    df[["단위_수량", "단위_기준"]] = df["단위"].apply(lambda u: pd.Series(parse_unit(u)))
    df["단위당가격"] = df["가격"] / df["단위_수량"]   # 예: 원/kg
    return df


# ──────────────────────────────────────────────────────────────
# 일별 도매가 (periodWholesaleProductList)
#
# 이 응답에는 성격이 다른 3종류가 섞여 있다. 반드시 분리한다.
#   관측치 : 시장별 실제 도매가    (countyname = 지역명)
#   평균   : 전체 시장 평균        (countyname = '평균')
#   평년   : 다년 평균 기준선      (countyname = '평년')  ★ 날짜 축이 다름
# 한 테이블에 두면 같은 날짜가 중복 집계된다.
# ──────────────────────────────────────────────────────────────
def _split_kind(kindname: pd.Series) -> pd.DataFrame:
    """'봄(10kg(그물망 3포기))' → 품종='봄', 단위원문='10kg(그물망 3포기)'"""
    unit_raw = kindname.str.extract(r"^[^(]*\((.*)\)$")[0]
    parsed = unit_raw.apply(
        lambda u: pd.Series(parse_unit(u), index=["단위_수량", "단위_기준"]))
    return pd.concat([
        kindname.str.split("(", n=1).str[0].str.strip().rename("품종"),
        unit_raw.rename("단위_원문"),
        parsed,
    ], axis=1)


def clean(src) -> dict[str, pd.DataFrame]:
    """일별 도매가 응답 → 성격별 3개 DataFrame.

    src: CSV 경로 / DataFrame / 응답 dict 모두 허용
    """
    if isinstance(src, pd.DataFrame):
        df = src.copy()
    elif isinstance(src, dict):
        df = pd.DataFrame(_item_list(src.get("data", {})))
    else:
        df = pd.read_csv(src, dtype=str, encoding="utf-8-sig")   # BOM 제거
    if df.empty:
        return {k: pd.DataFrame() for k in ("관측치", "전체평균", "평년기준선")}

    df = df.astype(str).replace({"nan": None})
    df["날짜"] = _to_date(df["yyyy"], df["regday"])
    df["가격"] = _to_price(df["price"])
    df["미거래"] = df["price"].eq("-")            # 결측과 구분

    is_agg = df["countyname"].isin(AGG_LABELS)

    # ── 관측치 ──────────────────────────────────────────────
    obs = df[~is_agg].copy()
    obs["원문_kindname"] = obs["kindname"]        # 원문 보존
    obs = pd.concat([obs, _split_kind(obs["kindname"])], axis=1)
    obs["단위당가격"] = obs["가격"] / obs["단위_수량"]   # 원/kg
    obs = obs.rename(columns={"itemname": "품목", "countyname": "지역",
                              "marketname": "시장"})
    obs = obs[["날짜", "품목", "품종", "지역", "시장", "가격", "단위당가격",
               "단위_수량", "단위_기준", "단위_원문", "미거래", "원문_kindname"]]

    # ── 집계값 (grain이 다르므로 분리) ───────────────────────
    agg = df[is_agg].rename(columns={"countyname": "집계구분"})[
        ["날짜", "집계구분", "가격", "미거래"]]

    return {
        "관측치": obs.sort_values(["시장", "품종", "날짜"]).reset_index(drop=True),
        "전체평균": agg[agg.집계구분 == "평균"].reset_index(drop=True),
        "평년기준선": agg[agg.집계구분 == "평년"].reset_index(drop=True),
    }


# ──────────────────────────────────────────────────────────────
# 연도별 가격 (yearlySalesList)
#
# 응답 item: div(연도) + avg/max/min/stddev/cv/af _data.
#   cv_data = 변동계수(coefficient of variation), af_data = 진폭계수(amplitude)
# 월별과 달리 '연도' 단위 통계라 grain이 1행 = 계열×연도 다.
# ──────────────────────────────────────────────────────────────
def process_yearly_json(data, cutoff: date | None = None) -> pd.DataFrame:
    """KAMIS 연도별 가격 응답 → Long Format DataFrame.

    data   : 응답 전체(dict) 또는 price 리스트 둘 다 허용
    cutoff : '오늘'. 생략하면 한국 시간 기준 현재 날짜.
             진행 중인 해(연평균 미확정) 판별에 사용.
    """
    if cutoff is None:
        cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date()

    data = _price_entries(data)
    if not data:
        return pd.DataFrame()

    rows = []
    for entry in data:
        caption = entry.get("caption", "")
        parts = [p.strip() for p in caption.split(">")]
        meta = dict(zip(CAPTION_FIELDS, parts))
        meta["caption_원문"] = caption            # ★ 원문 보존
        meta["caption_필드수"] = len(parts)       # 구조 변경 감지용
        meta["거래구분코드"] = entry.get("productclscode")

        for item in _item_list(entry):
            div = str(item.get("div", ""))
            year = int(div) if div.isdigit() else None
            rows.append({**meta, "연도": year,
                         "평균가_원문": item.get("avg_data", "-"),
                         "최고가_원문": item.get("max_data", "-"),
                         "최저가_원문": item.get("min_data", "-"),
                         "표준편차_원문": item.get("stddev_data", "-"),
                         "변동계수": item.get("cv_data"),
                         "af": item.get("af_data"),
                         # 진행 중인 해는 연간 통계가 아직 확정되지 않았다
                         "진행중": year is not None and year >= cutoff.year})

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for col in ("평균가", "최고가", "최저가", "표준편차"):
        df[col] = _to_price(df[f"{col}_원문"])
    df["변동계수"] = pd.to_numeric(df["변동계수"], errors="coerce")   # 이미 % 수치
    df["af"] = pd.to_numeric(df["af"], errors="coerce")

    df[["단위_수량", "단위_기준"]] = df["단위"].apply(lambda u: pd.Series(parse_unit(u)))
    df["날짜"] = pd.to_datetime(
        df["연도"].astype("Int64").astype(str) + "-01-01", errors="coerce")
    return df


def quality_report(obs: pd.DataFrame) -> pd.DataFrame:
    """시장·품종별 유효 관측 요약. 사실상 무의미한 계열을 골라낸다."""
    g = obs.groupby(["시장", "품종"]).agg(
        관측일수=("날짜", "count"),
        유효=("가격", "count"),
        시작=("날짜", "min"), 종료=("날짜", "max"))
    g["유효율%"] = (g.유효 / g.관측일수 * 100).round(1)
    return g.sort_values("유효율%")