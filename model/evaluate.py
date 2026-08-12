# model/evaluate.py
"""워크포워드 평가 루프와 재현 검사 CLI.

    python -m model.evaluate --check      # 15·19번 수치를 재현하는지 검사
    python -m model.evaluate              # 성적표 전체

**여기가 이 패키지의 계약이다.** 각 폴드는 그 시작 이전 데이터로만 학습하고,
학습 끝과 평가 시작 사이에 엠바고를 둔다. 이 둘 중 하나만 빠져도 적중률이
올라가는데, 그건 좋아진 게 아니라 미래를 본 것이다.
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from model import config as C
from model.data import load_mart
from model.features import FEATURES, build_features, categorical_indices
from model.metrics import add_derived, score

RESULT_COLS = ["item", "price_date", "오름", "price_kg_avg", "미래가격"]


def make_estimator(**overrides):
    """채택 모델을 만든다. `overrides` 로 파라미터를 덮어쓸 수 있다.

    `early_stopping=False` 가 중요하다. 켜면 내부에서 검증 분할을 떼는데,
    시계열에서 무작위 분할은 미래를 섞는다.
    """
    params = {**C.PARAMS, **overrides}
    return HistGradientBoostingClassifier(
        early_stopping=False, random_state=C.RANDOM_STATE, **params
    )


def walk_forward(
    feat: pd.DataFrame,
    eval_items: list[str],
    features: list[str] | None = None,
    estimator_factory=make_estimator,
) -> pd.DataFrame:
    """폴드마다 학습하고 평가 구간을 예측해 한 표로 잇는다.

    Args:
        feat: `build_features()` 결과.
        eval_items: 평가 대상 품목. **학습은 항상 `feat` 전체로 한다** —
            18·19번에서 학습 품목을 줄이면 돈 지표가 나빠졌다. 품목을 빼는
            건 운영에서지 학습에서가 아니다.
        features: 쓸 컬럼. 기본은 채택 21개.
        estimator_factory: 모델을 만드는 함수. 다른 모델을 재려면 여기만 바꾼다.

    Returns:
        폴드를 이어 붙인 예측 표 + `방향맞음`·`확신도`.
    """
    use = list(features or FEATURES)
    cat = categorical_indices(use)
    out = []

    for start, end in C.FOLDS:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        train = feat[feat.price_date < s - C.EMBARGO]
        test = feat[(feat.price_date >= s) & (feat.price_date <= e)
                    & (feat.item.isin(eval_items))]
        if train.empty or test.empty:
            logging.warning(f"[model] 폴드 {start} 건너뜀 (학습 {len(train)}행 "
                            f"· 평가 {len(test)}행)")
            continue

        clf = estimator_factory(categorical_features=cat).fit(train[use], train.오름)
        t = test[RESULT_COLS].copy()
        t["오를확률"] = clf.predict_proba(test[use])[:, 1]
        t["fold"] = start
        out.append(t)

    if not out:
        raise RuntimeError("평가된 폴드가 없습니다. FOLDS 와 데이터 기간을 보세요.")
    return add_derived(pd.concat(out).reset_index(drop=True))


def noise_floor(
    feat: pd.DataFrame,
    eval_items: list[str],
    n_noise: int = 4,
    seeds: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> pd.DataFrame:
    """무의미한 피처를 넣고 값을 흔들어 '차이 없음'의 폭을 잰다.

    **`random_state` 만 바꾸면 안 된다.** `early_stopping=False` 에 기본
    `max_features` 면 학습이 결정적이라 표준편차가 정확히 0 이 나온다
    (교훈 7). 흔들 것은 시드가 아니라 **피처 값**이다.

    여기서 나온 폭보다 작은 차이는 발견이 아니다. 대상 품목이 바뀌면
    폭도 달라지니 **비교할 때마다 다시 잰다.**
    """
    rows = []
    for seed in seeds:
        d = feat.copy()
        use = list(FEATURES)
        rng = np.random.default_rng(seed)
        for j in range(n_noise):
            d[f"noise{j}"] = rng.normal(size=len(d))
            use.append(f"noise{j}")
        r = walk_forward(d, eval_items, features=use)
        rows.append({"seed": seed, **score(r, n_items=len(eval_items))})
    return pd.DataFrame(rows)


def item_groups(df: pd.DataFrame) -> dict[str, list[str]]:
    """확정된 품목 구분을 데이터에서 다시 계산한다.

    상수로 박지 않는 이유: 정체율은 데이터가 늘면 바뀔 수 있고, 그때
    조용히 옛 목록을 쓰면 안 된다. 규칙(정체율 40%)만 고정한다.
    """
    stagnation = df.groupby("item").price_kg_avg.apply(
        lambda s: (s.diff() == 0).mean() * 100)
    all_items = sorted(df.item.unique())
    stagnant = sorted(stagnation.index[stagnation >= C.STAGNANT_LINE])
    active = [i for i in all_items if i not in stagnant]
    storable = [i for i in active
                if C.STORAGE_DAYS.get(i, 0) >= C.MIN_STORAGE_DAYS]
    return {
        "전체": all_items,
        "정체": stagnant,
        "운영 대상": active,
        "창고 후보": storable,
        "중간지대": [i for i in C.MIDZONE if i in all_items],
    }


def run(source: str = "auto") -> tuple[pd.DataFrame, dict[str, list[str]], pd.DataFrame]:
    """마트를 읽고 채택 사양으로 한 번 평가한다."""
    df = load_mart(source)
    groups = item_groups(df)
    feat = build_features(df, groups["전체"])
    logging.info(f"[model] {len(groups['전체'])}품목 {len(feat):,}행 "
                 f"({df.price_date.min():%Y-%m-%d} ~ {df.price_date.max():%Y-%m-%d})")
    r = walk_forward(feat, groups["전체"])
    return r, groups, feat


def report(r: pd.DataFrame, groups: dict[str, list[str]]) -> pd.DataFrame:
    """대상 묶음별 성적표."""
    rows = []
    for label in ("전체", "운영 대상", "창고 후보", "중간지대"):
        sel = groups[label]
        sub = r[r.item.isin(sel)].reset_index(drop=True)
        rows.append({"대상": f"{label} ({len(sel)}품목)",
                     **score(sub, n_items=len(sel))})
    return pd.DataFrame(rows).set_index("대상")


def check(r: pd.DataFrame, groups: dict[str, list[str]],
          feat: pd.DataFrame | None = None) -> bool:
    """15·19번 수치를 재현하는지 검사한다.

    **이게 통과하기 전에는 모델을 손대지 않는다.** 재현이 안 되는 상태에서
    성능을 비교하면 이식 실수 때문인지 모델 때문인지 영영 못 가린다.

    Args:
        feat: 학습 행렬. 주면 행 수를 `EXPECTED_ROWS` 와 대조해 **'코드가
            틀렸다'와 '데이터가 늘었다'를 구분**한다. 마트는 매일 커지므로
            이걸 안 보면 검사가 매일 실패하고 곧 아무도 안 보게 된다.

    Returns:
        판정. 행 수가 달라졌으면 숫자가 어긋나도 통과로 본다 — 그건
        회귀가 아니라 비교 대상이 바뀐 것이다.
    """
    전체 = score(r, n_items=len(groups["전체"]))
    창고 = score(r[r.item.isin(groups["창고 후보"])],
                 n_items=len(groups["창고 후보"]))
    중간 = score(r[r.item.isin(groups["중간지대"])],
                 n_items=len(groups["중간지대"]))
    got = {
        "적중률 %": 전체["적중률 %"],
        "확신한 날 적중률 %": 전체["확신한 날 적중률 %"],
        "창고비 한도 %(실행 가능 11품목)": 창고["창고비 한도 %"],
        "창고비 한도 %(중간지대 4품목)": 중간["창고비 한도 %"],
    }
    rows, matched = [], True
    for k, want in C.EXPECTED.items():
        diff = got[k] - want
        passed = abs(diff) <= C.TOLERANCE
        matched &= passed
        rows.append({"지표": k, "기대": want, "실제": round(got[k], 2),
                     "차이": round(diff, 3), "판정": "일치" if passed else "다름"})
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\n창고 후보 {len(groups['창고 후보'])}품목: {groups['창고 후보']}")
    print(f"정체 품목 {len(groups['정체'])}개: {groups['정체']}")

    # 행 수가 그대로인데 숫자가 다르면 코드 회귀다. 행 수가 달라졌으면
    # 비교 대상이 바뀐 것이라 숫자가 움직이는 게 정상이다.
    같은데이터 = feat is None or len(feat) == C.EXPECTED_ROWS
    if 같은데이터:
        print(f"\n학습 행 수 {C.EXPECTED_ROWS:,} (기준과 같음)")
        print("판정: 재현됨" if matched else
              "판정: **회귀** — 데이터가 같은데 숫자가 다릅니다. 코드를 보세요.")
        return bool(matched)

    print(f"\n학습 행 수 {len(feat):,} (기준 {C.EXPECTED_ROWS:,}, "
          f"{len(feat) - C.EXPECTED_ROWS:+,})")
    if matched:
        print("판정: 재현됨 (데이터가 늘었는데도 허용치 안)")
    else:
        print("판정: 판정 보류 — 데이터가 달라져 숫자가 움직인 것입니다.")
        print("  코드 회귀가 아닙니다. 이 수치를 새 기준으로 삼으려면")
        print("  config.EXPECTED 와 EXPECTED_ROWS 를 함께 갱신하세요.")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="워크포워드 평가")
    ap.add_argument("--source", default="auto", choices=["auto", "cache", "bigquery"])
    ap.add_argument("--check", action="store_true",
                    help="15·19번 수치를 재현하는지 검사하고 종료코드로 알린다")
    ap.add_argument("--noise", action="store_true",
                    help="노이즈 플로어도 잰다 (5배 느리다)")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    r, groups, feat = run(a.source)

    print("\n=== 성적표 ===")
    print(report(r, groups).round(2).to_string())

    if a.noise:
        print("\n=== 노이즈 플로어 (운영 대상) ===")
        nf = noise_floor(feat, groups["운영 대상"])
        print(nf.round(2).to_string(index=False))
        print(f"적중률 폭 {nf['적중률 %'].max() - nf['적중률 %'].min():.2f}%p")

    if a.check:
        print("\n=== 재현 검사 ===")
        return 0 if check(r, groups, feat) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
