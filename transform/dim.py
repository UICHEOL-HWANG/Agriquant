# transform/dim.py
"""소스별 품목 표기를 하나의 표준 품목명으로 잇는 차원 정의.

소스마다 품목을 부르는 방식이 다르다.
  KAMIS   : item 문자열 ("파")
  datago  : (대분류코드, 중분류코드) 쌍 ("12","02")
  기상청  : 품목이 없고 지점번호뿐 → ITEM_STATIONS 로 주산지를 잇는다
  MAFRA   : item 문자열 (일부 품목만 존재)

표준 품목명은 KAMIS 표기를 따른다. datago 는 '대파'라고 부르지만 KAMIS 가
'파'라서 여기서 '파'로 접는다. 매핑을 SQL 에 문자열로 박지 않고 여기서
정의해 뷰 생성 시 주입하는 이유는, extract 쪽 수집 대상(config/items.py)이
바뀌면 조인도 같이 따라가야 하기 때문이다.
"""
from __future__ import annotations

from dataclasses import dataclass

from extract.config.items import DATAGO_VEGETABLES, ITEM_STATIONS

# datago item_name → 표준 품목명. 표기가 같은 품목은 적지 않는다.
_DATAGO_ALIAS: dict[str, str] = {"대파": "파"}

# MAFRA 계약재배에 실제로 존재하는 품목만 (미나리·생강·파는 없음).
# 확인: 2026-07-30 mafra_contract_crop 822행 기준 item DISTINCT.
MAFRA_ITEMS: set[str] = {"배추", "양파", "감자"}


@dataclass(frozen=True)
class ItemDim:
    """표준 품목 하나와 각 소스에서의 식별자."""

    item: str                            # 표준 품목명 (= KAMIS item)
    stations: list[str]                  # 기상 관측지점 (주산지, 다대일)
    datago_keys: list[tuple[str, str]]   # (lclsf_code, mclsf_code) 쌍
    in_mafra: bool                       # MAFRA 계약재배에 존재하는지


def _datago_keys_by_item() -> dict[str, list[tuple[str, str]]]:
    """DATAGO_VEGETABLES 를 표준 품목명 기준으로 뒤집는다."""
    out: dict[str, list[tuple[str, str]]] = {}
    for t in DATAGO_VEGETABLES:
        name = _DATAGO_ALIAS.get(t.item_name, t.item_name)
        out.setdefault(name, []).append((t.lclsf_code, t.mclsf_code))
    return out


def build_item_dims() -> list[ItemDim]:
    """ITEM_STATIONS 와 DATAGO_VEGETABLES 의 합집합으로 품목 차원을 조립한다.

    2026-08-10 이전에는 ITEM_STATIONS 만 기준으로 삼았다. "기상 지점이 없는
    품목은 조인해도 피처가 반쪽이라 마트에 넣을 이유가 없다"는 이유였는데,
    그 전제가 깨졌다.

      notebooks/16 — 기상은 방향 예측에 값을 하지 않는다(생강·양파·파는
                     오히려 악화). 전 구간에서 노이즈 이내이거나 마이너스다.
      notebooks/21 — 출하량은 값을 한다. 커버리지 30~60% 구간에서 최대
                     +2.97%p 이고, 하필 운영 지점(32%)이 거기다.

    반쪽이 아니라 **쓸모 있는 쪽만 남는 것**이라, 출하량만 있는 품목도
    넣는다. 기상 지점이 없으면 stations=[] 이고 station_map 에 행이 안
    생겨 기상 컬럼만 NULL 로 남는다. 마트는 가격 행 기준 LEFT JOIN 이라
    행이 사라지지는 않는다.

    순서는 ITEM_STATIONS 를 먼저 두어 기존 6품목의 SQL 위치를 유지한다.
    """
    datago = _datago_keys_by_item()
    items = list(ITEM_STATIONS) + [i for i in datago if i not in ITEM_STATIONS]
    return [
        ItemDim(
            item=item,
            stations=list(ITEM_STATIONS.get(item, ())),
            datago_keys=datago.get(item, []),
            in_mafra=item in MAFRA_ITEMS,
        )
        for item in items
    ]
