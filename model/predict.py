# model/predict.py
"""오늘 시점의 신호를 낸다. 일 단위 운영에서 부르는 진입점이다.

    python -m model.predict                    # 마트 마지막 날 기준
    python -m model.predict --date 2026-08-07  # 특정 날 기준
    python -m model.predict --out signals.csv  # 파일로도 남기기

`evaluate.py` 가 **과거를 재는 곳**이라면 여기는 **오늘을 예측하는 곳**이다.
둘의 차이는 하나뿐이다: 평가는 라벨이 있는 행을 쓰고, 추론은 **라벨이
아직 없는 행**을 쓴다. 오늘 시점은 7거래일 뒤 가격을 모르기 때문이다.

**엠바고가 여기엔 없다.** 엠바고는 평가에서 미래를 안 보게 하는 장치인데,
추론은 애초에 미래가 존재하지 않으므로 가진 데이터를 전부 학습에 쓴다.
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from model import config as C
from model.data import load_mart
from model.evaluate import item_groups, make_estimator
from model.features import FEATURES, build_features, categorical_indices
from model.store import model_version, spec_version


def train_final(feat: pd.DataFrame, features: list[str] | None = None):
    """가진 데이터 전부로 학습한다. 검증 분할을 떼지 않는다.

    운영 모델은 평가 모델과 **다르게 학습된다** — 평가는 폴드 시작 이전만
    쓰지만 운영은 어제까지 전부 쓴다. 성능 숫자(62.8%·72.1%)는 평가에서
    나온 값이고, 운영 모델은 데이터가 더 많으니 그보다 나쁘지 않다고
    보는 것이 합리적이다. **다만 그건 가정이지 측정이 아니다.**
    """
    use = list(features or FEATURES)
    clf = make_estimator(categorical_features=categorical_indices(use))
    return clf.fit(feat[use], feat.오름), use


def predict_latest(
    source: str = "auto",
    as_of: str | None = None,
    features: list[str] | None = None,
) -> pd.DataFrame:
    """기준일의 품목별 신호를 낸다.

    Args:
        source: 마트 출처(`auto`·`cache`·`bigquery`).
        as_of: 기준일 `YYYY-MM-DD`. 없으면 마트의 마지막 날.

    Returns:
        품목별 한 행. `신호` 는 셋 중 하나다.

        - **미룸**: 오를 확률이 높고 확신도가 임계값 이상 — 창고에 넣는다
        - **오늘 판매**: 내릴 확률이 높고 확신도가 임계값 이상
        - **판단 보류**: 확신도가 임계값 미만 — 평소대로 한다

    Note:
        정체 품목(피마늘·깐마늘·생강)은 아예 빼고 낸다. 19번에서 운영
        대상이 아니라고 정했고, 신호를 내면 쓰게 되기 때문이다.
    """
    df = load_mart(source)
    groups = item_groups(df)

    # 학습셋: 라벨이 있는 행 전부
    train = build_features(df, groups["전체"])
    clf, use = train_final(train, features)

    # 추론셋: 라벨이 없는 행(= 미래를 아직 모르는 최근 구간)
    full = build_features(df, groups["전체"], drop_unlabeled=False)
    unlabeled = full[full.미래가격.isna()]
    if unlabeled.empty:
        raise RuntimeError(
            "라벨 없는 행이 없습니다. 마트가 오래된 것으로 보입니다 — "
            "원천을 적재하고 refresh_cache() 를 돌리세요."
        )

    기준일 = pd.Timestamp(as_of) if as_of else unlabeled.price_date.max()
    today = unlabeled[unlabeled.price_date == 기준일]
    if today.empty:
        가능 = sorted(unlabeled.price_date.unique())[-5:]
        raise ValueError(
            f"{기준일:%Y-%m-%d} 에 예측할 행이 없습니다. "
            f"가능한 최근 날짜: {[str(pd.Timestamp(d).date()) for d in 가능]}"
        )

    p = clf.predict_proba(today[use])[:, 1]
    out = pd.DataFrame({
        "기준일": today.price_date.dt.date.values,
        "품목": today.item.values,
        "오늘 가격": today.price_kg_avg.values,
        "오를 확률": p,
        "확신도": abs(p - 0.5),
    })
    out["저장일수"] = out.품목.map(C.STORAGE_DAYS)
    out["창고 후보"] = out.품목.isin(groups["창고 후보"])
    out["신호"] = "판단 보류"
    확신 = out.확신도 >= C.THRESHOLD
    out.loc[확신 & (out["오를 확률"] > 0.5), "신호"] = "미룸"
    out.loc[확신 & (out["오를 확률"] <= 0.5), "신호"] = "오늘 판매"

    # 운영 대상만 남긴다 — 정체 품목은 신호를 내지 않는다(19번)
    out = out[out.품목.isin(groups["운영 대상"])]
    out = out.sort_values("확신도", ascending=False).reset_index(drop=True)

    # 그날 거래가 없던 품목은 마트에 행이 없어 조용히 빠진다. 휴장인지
    # 수집 실패인지 이 데이터만으로는 구분할 수 없으므로 **반드시 드러낸다.**
    out.attrs["누락"] = [i for i in groups["운영 대상"] if i not in set(out.품목)]
    out.attrs["기준일"] = 기준일
    out.attrs["최근 커버리지"] = recent_coverage(df, groups["운영 대상"], 기준일)

    # 어느 사양·어느 모델이 낸 신호인지 남긴다. 적재할 때 행마다 박힌다.
    # spec_version 은 재학습해도 안 바뀌어 성적을 묶는 키가 되고,
    # model_version 은 매 학습마다 바뀌어 개별 예측을 추적한다.
    out.attrs["train_end"] = train.price_date.max()
    out.attrs["train_rows"] = len(train)
    out.attrs["spec_version"] = spec_version(use)
    out.attrs["model_version"] = model_version(
        train.price_date.max(), len(train), use)
    return out


def recent_coverage(
    df: pd.DataFrame, items: list[str], as_of: pd.Timestamp, lookback: int = 10
) -> pd.Series:
    """기준일 직전 거래일들의 품목 수. 커버리지가 갑자기 준 걸 잡는 데 쓴다.

    최근 며칠이 16/16 이었는데 오늘만 11/16 이면 **휴장이 아니라 수집이
    아직 안 끝난 것**이다. 그대로 신호를 내면 빠진 품목은 판단 없이
    넘어가는데, 그게 중간지대 품목이면 그날 벌 것을 통째로 놓친다.
    """
    d = df[df.item.isin(items)]
    daily = d[d.price_date <= as_of].groupby("price_date").item.nunique()
    return daily.tail(lookback)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="오늘 시점 신호")
    ap.add_argument("--source", default="auto", choices=["auto", "cache", "bigquery"])
    ap.add_argument("--date", default=None,
                    help="기준일 YYYY-MM-DD (기본: 마트 마지막 날)")
    ap.add_argument("--out", default=None, help="CSV 로도 저장할 경로")
    ap.add_argument("--save", action="store_true",
                    help="BigQuery model_prediction 에 적재한다 (append)")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    s = predict_latest(a.source, a.date)

    기준일 = s.기준일.iloc[0]
    print(f"\n=== {기준일} 기준 신호 (확신도 {C.THRESHOLD} 이상) ===")
    print(f"사양 {s.attrs['spec_version']} · 모델 {s.attrs['model_version']} "
          f"(학습 {s.attrs['train_rows']:,}행 · ~{s.attrs['train_end']:%Y-%m-%d})")
    print(s.to_string(index=False,
                      formatters={"오늘 가격": "{:,.0f}".format,
                                  "오를 확률": "{:.3f}".format,
                                  "확신도": "{:.3f}".format}))

    행동 = s[(s.신호 != "판단 보류") & s["창고 후보"]]
    print(f"\n행동할 품목 {len(행동)}개 / 운영 대상 {len(s)}개")
    if len(행동):
        for _, r in 행동.iterrows():
            print(f"  {r.품목:<10} {r.신호:<6} "
                  f"(오를 확률 {r['오를 확률']:.1%} · 저장 {r.저장일수}일)")
    else:
        print("  없음 — 오늘은 평소대로 하면 됩니다.")

    보류 = s[~s["창고 후보"] & (s.신호 != "판단 보류")]
    if len(보류):
        print(f"\n참고: 저장 {C.MIN_STORAGE_DAYS}일 미만이라 미룰 수 없는 품목의 "
              f"신호 {len(보류)}개는 제외했습니다 ({', '.join(보류.품목)}).")

    누락 = s.attrs.get("누락", [])
    if 누락:
        cov = s.attrs["최근 커버리지"]
        직전 = cov.iloc[:-1]
        print(f"\n[확인 필요] 이날 거래 행이 없어 신호를 못 낸 품목 {len(누락)}개: "
              f"{', '.join(누락)}")
        print(f"  기준일 커버리지 {cov.iloc[-1]}품목 · "
              f"직전 {len(직전)} 거래일 중앙값 {직전.median():.0f}품목")
        if 직전.size and cov.iloc[-1] < 직전.median():
            print("  → 최근보다 적습니다. **휴장이 아니라 수집 미완일 가능성이 높습니다.**")
            print(f"     `--date` 로 직전 거래일({직전.index[-1]:%Y-%m-%d})을 쓰거나,")
            print("     원천을 마저 적재하고 refresh_cache() 후 다시 돌리세요.")
        else:
            print("  → 최근 수준과 비슷합니다. 휴장으로 보입니다.")

    if a.out:
        s.to_csv(a.out, index=False)
        print(f"\n저장: {a.out}")
    if a.save:
        from model.store import save_predictions

        n = save_predictions(s)
        print(f"\nBigQuery 적재: model_prediction ← {n}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
