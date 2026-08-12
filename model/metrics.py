# model/metrics.py
"""평가지표. 예측 표를 받아 숫자를 내는 순수 계산이고 파일을 건드리지 않는다.

**지표 이름은 쉬운 말로 쓴다.** 15번에서 '이득'·'손익분기 보관비' 같은 말이
매번 막혀서 갈았고 13·14번에 소급 적용했다. 코드 식별자는 영문이지만
사람이 보는 라벨은 한국어를 유지한다.

퍼센트 둘이 갈리는 건 **나누는 대상이 달라서다**:

- `더 받은 돈 %` 는 **출하 전량**으로 나눈다 — "내 매출이 몇 % 늘었나"
- `창고비 한도 %` 는 **창고에 넣은 물량**으로 나눈다 — "그 물건에 창고비를
  얼마까지 쓸 수 있나"

원 단위로는 같은 금액이다. 둘을 빼거나 직접 비교하면 안 된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from model.config import THRESHOLD


def add_derived(r: pd.DataFrame) -> pd.DataFrame:
    """`오를확률` 에서 `방향맞음`·`확신도` 를 파생한다.

    확신도는 `|P(오름) − 0.5|` 다. 0 이면 반반이라는 뜻이고 0.5 가 최대다.
    """
    r = r.copy()
    r["방향맞음"] = (r.오를확률 > 0.5) == r.오름
    r["확신도"] = (r.오를확률 - 0.5).abs()
    return r


def score(
    r: pd.DataFrame,
    n_items: int | None = None,
    threshold: float = THRESHOLD,
) -> dict[str, float]:
    """예측 표 하나를 성적표로 바꾼다.

    Args:
        r: 예측 표. `확신도` 가 없으면 여기서 파생한다.
        n_items: 연 창고행을 나눌 품목 수. 표를 부분집합으로 걸렀다면
            **원래 대상 품목 수를 명시해야 한다** — 표에 안 나타난 품목이
            있으면 `nunique()` 가 작게 세어 연 창고행이 부풀려진다.
        threshold: 확신도 임계값.

    Returns:
        지표 dict. 정의되지 않는 값은 NaN 을 넣는다(0 이 아니다 — "창고에
        한 번도 안 넣었다"와 "넣었는데 0원 벌었다"는 다르다).

    Note:
        기간은 **표에 실제로 있는 날짜 범위**로 잰다. 전체 데이터 기간을
        쓰면 안 된다 — 18번에서 5.56년으로 나눠 연 창고행이 32.8 로 나왔고,
        17번의 51.1 과 안 맞아 한참 헤맸다. 평가 구간은 3.57년이다.
    """
    if "확신도" not in r.columns:
        r = add_derived(r)

    확신 = r[r.확신도 >= threshold]
    넣음 = ((r.오를확률 > 0.5) & (r.확신도 >= threshold)).to_numpy()

    # 미룬 날은 미래가격을, 그냥 판 날은 오늘 가격을 받는다
    실현 = np.where(넣음, r.미래가격.to_numpy(), r.price_kg_avg.to_numpy())
    더받은돈 = (실현.mean() / r.price_kg_avg.mean() - 1) * 100
    비율 = 넣음.mean()

    days = (r.price_date.max() - r.price_date.min()).days
    기간 = days / 365.25 if days > 0 else np.nan

    p = r.오를확률.to_numpy()
    y = r.오름.to_numpy().astype(float)
    eps = 1e-15                      # log(0) 방어

    return {
        "적중률 %": r.방향맞음.mean() * 100,
        "확신한 날 적중률 %": 확신.방향맞음.mean() * 100 if len(확신) else np.nan,
        "확신한 날 비율 %": (r.확신도 >= threshold).mean() * 100,
        "더 받은 돈 %": 더받은돈,
        "창고비 한도 %": 더받은돈 / 비율 if 비율 > 0 else np.nan,
        "품목당 연 창고행": (
            넣음.sum() / 기간 / (n_items or r.item.nunique())
            if not np.isnan(기간) else np.nan
        ),
        # 확률이 숫자로서 믿을 만한가. 운영 규칙이 |P-0.5| >= 0.20 이라
        # 보정이 어긋나면 임계값이 엉뚱한 날을 고른다. 둘 다 낮을수록 좋다.
        "브라이어 점수": float(np.mean((p - y) ** 2)),
        "로그손실": float(-np.mean(y * np.log(np.clip(p, eps, 1))
                                 + (1 - y) * np.log(np.clip(1 - p, eps, 1)))),
    }


def quantile_threshold(r: pd.DataFrame, coverage: float) -> float:
    """원하는 커버리지가 나오도록 확신도 임계값을 분위수로 잡는다.

    **서로 다른 모델을 비교할 때 고정 임계값을 쓰면 안 된다**(21번 교훈).
    확신도 분포가 모델마다 다르면 같은 0.20 이 다른 비율의 날을 고르고,
    그러면 성능 차이인지 표본 크기 차이인지 못 가린다. 커버리지를 맞춰
    놓고 비교해야 공정하다. 16번의 결론 일부가 이것 때문에 뒤집혔다.

    Args:
        coverage: 남기고 싶은 날의 비율(0~1). 0.32 면 상위 32%.
    """
    # np.arange 로 만든 값은 1.0000000000000002 처럼 아주 조금 넘칠 수 있다.
    # 의미상 1.0 이므로 그 폭만 흡수하고, 진짜로 범위를 벗어나면 막는다.
    if 1 < coverage <= 1 + 1e-9:
        coverage = 1.0
    if not 0 < coverage <= 1:
        raise ValueError(f"coverage 는 (0, 1] 이어야 합니다: {coverage}")
    if "확신도" not in r.columns:
        r = add_derived(r)
    return float(r.확신도.quantile(1 - coverage))


def score_at_coverage(
    r: pd.DataFrame, coverage: float, n_items: int | None = None
) -> dict[str, float]:
    """커버리지를 맞춘 뒤 성적을 낸다. **모델 간 비교는 이걸 쓴다.**"""
    return score(r, n_items=n_items, threshold=quantile_threshold(r, coverage))


def by_item(r: pd.DataFrame, threshold: float = THRESHOLD) -> pd.DataFrame:
    """품목별 성적표. 어디서 벌고 어디서 잃는지 보려고 쓴다.

    품목 단위 숫자는 노이즈가 크다(1.3~2.8%p). 26·27번에서 여기 낚여
    "파·미나리가 원인"이라는 결론을 냈다가 폴드로 쪼개니 무너졌다.
    **품목별 차이를 근거로 삼기 전에 노이즈 플로어를 먼저 재라.**
    """
    if "확신도" not in r.columns:
        r = add_derived(r)
    return pd.DataFrame({
        item: score(g, n_items=1, threshold=threshold)
        for item, g in r.groupby("item")
    }).T
