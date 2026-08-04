# config/items.py
"""수집 대상 품목 정의.

품목코드는 BigQuery `<dataset>.item_code` 테이블에 있으므로 여기서는
'어떤 품목을 가져올지'(이름)만 정한다. 실제 (category_code, item_code)는
repository.load_item_targets() 가 테이블에서 조회해 채운다.

주의: 감자는 부류코드 200(채소류)이 아니라 100(식량작물)이다.
      그래서 '카테고리 200 필터'가 아니라 '품목명 화이트리스트'로 고른다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ItemTarget:
    """수집 단위 품목. category_code 는 품목마다 다를 수 있다(감자=100)."""

    category_code: str   # 부류코드 (KAMIS itemcategorycode)
    item_code: str       # 품목코드 (KAMIS itemcode)
    item_name: str = ""  # 품목명 (로그·가독성용)


# 기본 수집 대상 채소. item_code 테이블에서 이 이름들로 조회한다.
DEFAULT_VEGETABLES: list[str] = ["배추", "양파", "감자", "미나리", "생강", "파"]


# ──────────────────────────────────────────────────────────────
# datago(공공데이터포털) 출하량 추이 수집 대상.
#
# datago는 KAMIS 와 코드 체계가 다르다. 상품 대분류(lclsf)·중분류(mclsf)
# 코드로 조회하며, 아래 값은 shipmentSequel API 응답에서 직접 확인해 확정했다.
#   주의: 대분류 12는 '조미채소류'라 채소 전체가 아니다.
#         배추·미나리=엽경채류(10), 감자=서류(05).
#   '파'는 KAMIS 기준 대파(12-02)로 매핑한다(쪽파 03·실파 04 제외).
# ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DatagoTarget:
    """datago 출하량 수집 단위 품목."""

    lclsf_code: str      # 상품 대분류 코드 (gds_lclsf_cd)
    mclsf_code: str      # 상품 중분류 코드 (gds_mclsf_cd)
    item_name: str = ""  # 품목명 (로그·가독성용)


# KAMIS DEFAULT_VEGETABLES 6품목과 1:1 매칭.
DATAGO_VEGETABLES: list[DatagoTarget] = [
    DatagoTarget("10", "01", "배추"),
    DatagoTarget("12", "01", "양파"),
    DatagoTarget("05", "01", "감자"),
    DatagoTarget("10", "09", "미나리"),
    DatagoTarget("12", "10", "생강"),
    DatagoTarget("12", "02", "대파"),
]


# ──────────────────────────────────────────────────────────────
# 기상청 ASOS 관측 지점.
#
# 지점번호는 추측이 아니라 ASOS 응답의 stnNm 으로 직접 확인해 확정했다.
# 전국 평균은 가격 설명력이 없어서 '주산지' 지점만 고른다.
# ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class WeatherStation:
    """ASOS 관측 지점."""

    stn_id: str        # 지점번호 (stnIds)
    stn_name: str      # 지점명 (응답 stnNm 으로 검증)
    note: str = ""     # 어떤 품목의 주산지인지


WEATHER_STATIONS: list[WeatherStation] = [
    WeatherStation("100", "대관령", "고랭지 배추·감자"),
    WeatherStation("105", "강릉", "강원 감자"),
    WeatherStation("129", "서산", "충남 생강"),
    WeatherStation("143", "대구", "경북 청도 미나리"),
    WeatherStation("146", "전주", "전북 완주 생강"),
    WeatherStation("165", "목포", "전남 양파·대파(무안·진도)"),
    WeatherStation("184", "제주", "겨울 감자"),
    WeatherStation("192", "진주", "경남 채소"),
    WeatherStation("232", "천안", "충남 채소"),
    WeatherStation("253", "김해시", "경남 시설채소"),
    WeatherStation("261", "해남", "가을·김장 배추"),
    WeatherStation("288", "밀양", "경남 양파·미나리"),
    WeatherStation("108", "서울", "대조군(소비지)"),
]

# 품목 → 주산지 지점(다대일).
#
# 배추·감자는 계절에 따라 산지가 갈린다(여름 고랭지 ↔ 가을 해남,
# 강원 노지 ↔ 겨울 제주). 계절마다 지점을 '전환'하면 경계를 사람이
# 자의적으로 정해야 하고 산지가 겹치는 기간도 있다. 그래서 관련 지점을
# 모두 붙여두고 가중치는 모델이 학습하게 둔다.
ITEM_STATIONS: dict[str, list[str]] = {
    "배추": ["100", "261"],           # 여름 고랭지 / 가을·김장
    "감자": ["100", "105", "184"],    # 강원 노지 / 겨울 제주
    "양파": ["165", "288"],           # 무안·함평 / 창녕
    "파": ["165", "232"],             # 진도·신안 / 충남
    "생강": ["129", "146"],           # 서산 / 완주
    "미나리": ["143", "288"],          # 청도 / 밀양
}
