# model/features.py
"""마트 → 학습 행렬. 파일을 읽지도 쓰지도 않는 순수 변환이다.

**여기 있는 21개가 채택 피처의 전부다.** 06번에서 조합 22개를 훑고,
16·21·25·26·27번에서 출하량·기상을 붙였다 뗐다. 출하량은 여러 품목을
묶었을 때만 값을 하고 품목 단위로는 귀속이 안 돼서 뺐다.

피처를 더할 때 지킬 것: **모든 파생은 `groupby("item")` 안에서 한다.**
품목 경계를 넘어 rolling 을 걸면 배추의 과거가 감자의 피처로 새어 들어간다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from model.config import HORIZON

CALENDAR = [
    "month", "dayofweek",
    "days_to_major_holiday", "days_since_major_holiday", "days_since_solar_term",
]
"""달력 피처. `v_calendar_daily` 가 만들어 마트에 실려 온다.

`kasi_special_day.is_holiday_raw` 를 직접 쓰면 안 된다 — 제헌절·노동절까지
true 로 온다. 실측상 실제 휴장은 명절 연휴·신정·일요일뿐이다.
"""

LAGS = (1, 2, 3, 5, 10, 20)
WINDOWS = (5, 20, 60)

FEATURES = (
    [f"ret{lag}" for lag in LAGS]
    + [f"dev{w}" for w in WINDOWS]
    + [f"vol{w}" for w in WINDOWS]
    + CALENDAR
    + ["doy_sin", "doy_cos", "seas_dev", "item_code"]
)
"""학습에 넣는 컬럼 21개. 순서가 의미를 갖는다 —
`categorical_features` 를 인덱스로 넘기므로 순서가 바뀌면 조용히 틀린다.
그래서 인덱스는 손으로 적지 말고 `categorical_indices()` 로 뽑는다.
"""

CATEGORICAL = ["item_code", "month", "dayofweek"]
"""범주형으로 다룰 컬럼. 숫자로 들어 있지만 크기 비교가 무의미하다
(12월이 1월보다 크지 않다).
"""


def build_features(
    df: pd.DataFrame, categories: list[str], drop_unlabeled: bool = True
) -> pd.DataFrame:
    """마트에 라벨과 피처를 붙인다.

    Args:
        df: `load_mart()` 가 돌려준 DataFrame.
        categories: `item_code` 를 매길 품목 순서. **평가마다 같은 목록을
            넘겨야 한다** — 목록이 달라지면 같은 품목이 다른 코드를 받아
            폴드 간 비교가 깨진다.
        drop_unlabeled: True 면 라벨이 없는 행을 버린다(학습·평가용).
            **추론에는 False 를 쓴다** — 오늘 시점은 7거래일 뒤 가격을
            모르므로 라벨이 없고, 그 행이 바로 예측 대상이다.

    Returns:
        `y`(로그수익률) · `오름`(라벨) · `미래가격` 과 피처 21개가 붙은
        DataFrame. `drop_unlabeled=True` 면 끝부분 HORIZON 행이 품목마다
        잘려 나간다.

    Note:
        추론용 피처를 따로 짜지 않고 **같은 함수에 플래그만 둔 이유**는
        학습과 추론의 피처가 어긋나는 걸 막기 위해서다. 두 벌로 나누면
        한쪽만 고쳐지는 순간 조용히 틀린 예측이 나온다.

        `dropna` 는 `y`·`미래가격` 에만 건다. 피처 쪽 NaN(초기 구간의
        rolling)은 남겨서 HistGradientBoosting 이 직접 다루게 한다. 그게
        `min_periods` 로 억지로 채우는 것보다 정직하다.
    """
    b = df.sort_values(["item", "price_date"]).reset_index(drop=True).copy()
    b["_logp"] = np.log(b.price_kg_avg)

    def g(col: str):
        return b.groupby("item")[col]

    # 라벨: HORIZON 거래일 뒤의 로그가격 차이
    b["y"] = g("_logp").shift(-HORIZON) - b._logp
    b["오름"] = b.y > 0
    b["미래가격"] = g("price_kg_avg").shift(-HORIZON)

    # 과거 수익률
    for lag in LAGS:
        b[f"ret{lag}"] = b._logp - g("_logp").shift(lag)

    # 이동평균 이탈도와 변동성
    for w in WINDOWS:
        mp = max(2, w // 3)
        b[f"dev{w}"] = b._logp - g("_logp").transform(
            lambda s: s.rolling(w, min_periods=mp).mean())
        b[f"vol{w}"] = g("_logp").transform(
            lambda s: s.diff().rolling(w, min_periods=mp).std())

    # 계절: 연중 위치를 원으로 감아 12월과 1월이 붙게 한다
    doy = b.price_date.dt.dayofyear
    b["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    b["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)

    # 같은 달의 과거 평균 대비 이탈도.
    # shift(1) 이 핵심이다 — 이걸 빼면 오늘 값이 자기 평균에 들어가 미래를 본다.
    b["seas_dev"] = b._logp - (
        b.groupby(["item", "month"])["_logp"]
        .transform(lambda s: s.shift(1).expanding(min_periods=20).mean())
    )

    b["item_code"] = pd.Categorical(b.item, categories=categories).codes
    if drop_unlabeled:
        b = b.dropna(subset=["y", "미래가격"])
    return b.reset_index(drop=True)


def categorical_indices(feature_names: list[str]) -> list[int]:
    """`categorical_features` 에 넘길 인덱스를 컬럼 목록에서 뽑는다.

    피처를 더하거나 순서를 바꿔도 따라오도록 이름으로 찾는다. 인덱스를
    손으로 적으면 피처가 하나 늘 때 조용히 엉뚱한 컬럼이 범주형이 된다.
    """
    return sorted(feature_names.index(c) for c in CATEGORICAL)
