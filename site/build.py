# site/build.py
"""BigQuery → docs/data.json. GitHub Pages 가 읽을 데이터를 만든다.

    uv run python site/build.py

**HTML 을 만들지 않는다.** `docs/` 의 html·js·css 는 한 번 커밋하고 거의
안 바뀌며, 매일 갱신되는 건 이 파일이 뱉는 `data.json` 하나다. 그래야
일일 커밋 diff 가 수십 줄에 그치고, 그 커밋 타임스탬프가 "결과를 알기 전에
예측했다"는 증거로 남는다.

## 무엇을 담나

| 키 | 출처 | 성격 |
|---|---|---|
| `signals` | `model_prediction` 최신 날짜 | 잡이 적재한 것을 **그대로 읽는다** |
| `live` | `v_model_score` | 실전 성적. 채점 전이면 null |
| `backtest` | `model.run()` 워크포워드 | 매 빌드 재계산 |
| `by_fold`·`by_confidence` | 같은 워크포워드 | 한계·임계값 근거 |

**신호를 여기서 다시 계산하지 않는다.** 학습이 12초라 느린 것도 있지만,
더 중요한 건 사이트가 보여주는 것과 `model_prediction` 에 박힌 것이
달라지면 어느 쪽이 진짜인지 알 수 없어서다. 기록은 하나여야 한다.

반대로 `backtest` 는 매번 다시 잰다. 데이터가 늘면 백테스트 값도 조금씩
움직이는데, 그걸 고정해두면 실전 열과 비교할 때 기준이 낡는다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import model as M
from model import config as CFG
from model.store import PREDICTION, V_SCORE

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data.json"
KST = ZoneInfo("Asia/Seoul")


def _conn():
    from extract.database.connection import BigQueryConnection

    return BigQueryConnection()


def latest_signals(conn) -> tuple[str, list[dict]]:
    """잡이 마지막으로 적재한 날의 신호. 같은 날 여러 번 돌았으면 최신 실행."""
    sql = f"""
    WITH ranked AS (
        SELECT p.*, ROW_NUMBER() OVER (
                   PARTITION BY p.price_date, p.item
                   ORDER BY p.predicted_at DESC) AS rn
        FROM `{conn.table_id(PREDICTION.name)}` AS p
        WHERE p.price_date = (
            SELECT MAX(price_date) FROM `{conn.table_id(PREDICTION.name)}`)
    )
    SELECT item, signal, prob_up, confidence, price_kg_avg,
           storage_days, is_storable, price_date
    FROM ranked WHERE rn = 1
    ORDER BY confidence DESC
    """
    df = conn.client.query(sql).to_dataframe()
    if df.empty:
        return "", []
    as_of = str(pd.Timestamp(df.price_date.iloc[0]).date())
    rows = [{
        "item": r.item,
        "signal": r.signal,
        "prob_up": round(float(r.prob_up), 4),
        "confidence": round(float(r.confidence), 4),
        "price": round(float(r.price_kg_avg)),
        "storage_days": int(r.storage_days),
        "storable": bool(r.is_storable),
    } for r in df.itertuples()]
    return as_of, rows


def live_score(conn) -> dict:
    """실전 성적. 채점된 예측이 없으면 accuracy 가 null 로 남는다.

    **`spec_version` 으로 묶는다.** 매 실행 재학습이라 `model_version` 은
    매일 바뀌어서, 그걸로 묶으면 "N개 모델이 각 16건씩" 이 된다.
    """
    sql = f"""
    SELECT
        spec_version,
        COUNT(DISTINCT model_version)                       AS refits,
        COUNT(*)                                            AS predictions,
        COUNTIF(direction_correct IS NOT NULL)              AS scored,
        AVG(IF(direction_correct IS NULL, NULL,
               IF(direction_correct, 1.0, 0.0))) * 100      AS accuracy,
        AVG(IF(confidence >= threshold AND direction_correct IS NOT NULL,
               IF(direction_correct, 1.0, 0.0), NULL)) * 100 AS confident_accuracy,
        MIN(price_date)                                     AS first_date,
        MAX(price_date)                                     AS last_date
    FROM `{conn.table_id(V_SCORE)}`
    WHERE spec_version IS NOT NULL
    GROUP BY spec_version
    ORDER BY last_date DESC
    LIMIT 1
    """
    df = conn.client.query(sql).to_dataframe()
    if df.empty:
        return {"spec_version": None, "predictions": 0, "scored": 0,
                "accuracy": None, "confident_accuracy": None}
    r = df.iloc[0]

    def f(v):
        return None if pd.isna(v) else round(float(v), 2)

    return {
        "spec_version": r.spec_version,
        "refits": int(r.refits),
        "predictions": int(r.predictions),
        "scored": int(r.scored),
        "accuracy": f(r.accuracy),
        "confident_accuracy": f(r.confident_accuracy),
        "first_date": str(r.first_date),
        "last_date": str(r.last_date),
    }


def cumulative(conn) -> list[dict]:
    """날짜순 누적 적중률. 사이트의 메인 차트다.

    채점된 예측이 없으면 빈 배열이고, 화면은 "검증 중"으로 표시한다.
    **비어 있는 것 자체가 메시지**라 숨기지 않는다.
    """
    sql = f"""
    SELECT price_date,
           COUNTIF(direction_correct) AS hit,
           COUNT(*)                   AS n
    FROM `{conn.table_id(V_SCORE)}`
    WHERE direction_correct IS NOT NULL
    GROUP BY price_date ORDER BY price_date
    """
    df = conn.client.query(sql).to_dataframe()
    if df.empty:
        return []
    df["cum_hit"] = df.hit.cumsum()
    df["cum_n"] = df.n.cumsum()
    return [{
        "date": str(r.price_date),
        "n": int(r.cum_n),
        "accuracy": round(r.cum_hit / r.cum_n * 100, 2),
    } for r in df.itertuples()]


def backtest(r: pd.DataFrame, items: list[str]) -> dict:
    """워크포워드 기준선. 실전 열과 나란히 놓을 값이다."""
    sub = r[r.item.isin(items)]
    확신 = sub[sub.확신도 >= CFG.THRESHOLD]
    return {
        "accuracy": round(sub.방향맞음.mean() * 100, 2),
        "confident_accuracy": round(확신.방향맞음.mean() * 100, 2),
        "coverage": round((sub.확신도 >= CFG.THRESHOLD).mean() * 100, 2),
        "n": int(len(sub)),
        "n_confident": int(len(확신)),
        "threshold": CFG.THRESHOLD,
        "chance": round(float(sub.오름.mean()) * 100, 2),
    }


def by_fold(r: pd.DataFrame, items: list[str]) -> list[dict]:
    """폴드별 적중률. 57~68% 로 벌어지는 걸 보여주는 한계 차트."""
    sub = r[r.item.isin(items)]
    g = sub.groupby("fold").방향맞음.agg(["mean", "size"])
    return [{"fold": i, "accuracy": round(v["mean"] * 100, 2), "n": int(v["size"])}
            for i, v in g.iterrows()]


def by_confidence(r: pd.DataFrame, items: list[str]) -> list[dict]:
    """임계값별 커버리지와 적중률. 0.20 이 왜 거기인지 보여준다."""
    sub = r[r.item.isin(items)]
    out = []
    for t in np.arange(0, 0.45, 0.05):
        s = sub[sub.확신도 >= t]
        if len(s) < 50:
            break
        out.append({
            "threshold": round(float(t), 2),
            "coverage": round(len(s) / len(sub) * 100, 2),
            "accuracy": round(s.방향맞음.mean() * 100, 2),
            "n": int(len(s)),
        })
    return out


def by_month(r: pd.DataFrame, items: list[str]) -> list[dict]:
    """월별 확신한 날의 수와 '미룸' 비중. 여름 쏠림을 보여준다."""
    sub = r[r.item.isin(items)].copy()
    sub["월"] = sub.price_date.dt.month
    확신 = sub[sub.확신도 >= CFG.THRESHOLD]
    out = []
    for m, s in 확신.groupby("월"):
        out.append({
            "month": int(m),
            "n": int(len(s)),
            "delay_ratio": round(float((s.오를확률 > 0.5).mean()) * 100, 1),
        })
    return out


def by_item(r: pd.DataFrame, items: list[str]) -> list[dict]:
    """품목별 적중률. 품목마다 편차가 큰 걸 보여준다.

    품목 단위 숫자는 노이즈가 크다(1.3~2.8%p). 순위를 근거로 삼지 말고
    "고르게 잘 맞히지는 않는다" 정도로만 읽어야 한다.
    """
    sub = r[r.item.isin(items)]
    out = []
    for it, s in sub.groupby("item"):
        확신 = s[s.확신도 >= CFG.THRESHOLD]
        out.append({
            "item": it,
            "accuracy": round(float(s.방향맞음.mean()) * 100, 2),
            "confident_accuracy": (round(float(확신.방향맞음.mean()) * 100, 2)
                                   if len(확신) else None),
            "n": int(len(s)),
        })
    return sorted(out, key=lambda x: x["accuracy"])


def confidence_hist(r: pd.DataFrame, items: list[str], bins: int = 25) -> list[dict]:
    """확신도 분포. 임계값 0.20 이 어디를 자르는지 눈으로 보게 한다."""
    v = r[r.item.isin(items)].확신도.to_numpy()
    cnt, edges = np.histogram(v, bins=bins, range=(0, 0.5))
    return [{"x": round(float((edges[i] + edges[i + 1]) / 2), 3),
             "n": int(cnt[i])} for i in range(len(cnt))]


def reliability(r: pd.DataFrame, items: list[str], bins: int = 10) -> list[dict]:
    """신뢰도 곡선: 모델이 말한 확률 vs 실제로 오른 비율.

    대각선에 붙을수록 확률이 정직하다. 30번에서 보정을 걸어도 나아지지
    않았는데, 애초에 잘 맞아 있어서였다.
    """
    sub = r[r.item.isin(items)]
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (sub.오를확률 >= edges[i]) & (sub.오를확률 < edges[i + 1])
        s = sub[m]
        if len(s) < 40:          # 표본이 얇은 구간은 버린다
            continue
        out.append({
            "predicted": round(float(s.오를확률.mean()) * 100, 2),
            "actual": round(float(s.오름.mean()) * 100, 2),
            "n": int(len(s)),
        })
    return out


def price_series(df: pd.DataFrame, items: list[str], years: int = 2) -> dict:
    """품목별 가격 시계열. 주 단위로 접어 용량을 줄인다.

    일별로 두면 data.json 이 수백 KB 가 되고, 매일 커밋되므로 저장소가
    빠르게 불어난다. 주간이면 형태는 그대로 보이면서 1/5 로 준다.
    """
    cut = df.price_date.max() - pd.Timedelta(days=365 * years)
    d = df[(df.price_date >= cut) & (df.item.isin(items))]
    w = (d.set_index("price_date").groupby("item").price_kg_avg
         .resample("W").mean().reset_index())
    dates = sorted(w.price_date.unique())
    idx = {d_: i for i, d_ in enumerate(dates)}
    series = {}
    for it, g in w.groupby("item"):
        arr = [None] * len(dates)
        for d_, v in zip(g.price_date, g.price_kg_avg):
            arr[idx[d_]] = None if pd.isna(v) else round(float(v))
        series[it] = arr
    return {"dates": [str(pd.Timestamp(x).date()) for x in dates],
            "series": series}


def seasonal(df: pd.DataFrame, items: list[str]) -> dict:
    """품목 x 월 계절 편차(%). 그 품목 자기 평균 대비라 품목끼리 비교된다.

    01번에서 '계절이 가장 큰 신호'라고 나왔는데, 그게 눈에 보이는 형태다.
    """
    d = df[df.item.isin(items)].copy()
    d["월"] = d.price_date.dt.month
    base = d.groupby("item").price_kg_avg.mean()
    g = d.groupby(["item", "월"]).price_kg_avg.mean()
    cells = []
    order = sorted(items)
    for i, it in enumerate(order):
        for m in range(1, 13):
            if (it, m) not in g.index:
                continue
            dev = (g[(it, m)] / base[it] - 1) * 100
            cells.append([m - 1, i, round(float(dev), 1)])
    return {"items": order, "cells": cells}


def main() -> Path:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    conn = _conn()

    # 백테스트는 매 빌드 다시 잰다 — 데이터가 늘면 기준선도 움직인다
    r, groups, feat = M.run(source="bigquery")
    운영 = groups["운영 대상"]

    df_mart = M.load_mart(source="bigquery")
    last = df_mart.price_date.max()
    today = pd.Timestamp(datetime.now(KST).date())

    as_of, signals = latest_signals(conn)
    payload = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "as_of": as_of,
        "mart": {
            "rows": int(len(df_mart)),
            "last_date": str(last.date()),
            "freshness_days": int((today - last).days),
            "items_total": len(groups["전체"]),
            "items_operating": len(운영),
            "items_storable": len(groups["창고 후보"]),
        },
        "signals": signals,
        "backtest": backtest(r, 운영),
        "live": live_score(conn),
        "cumulative": cumulative(conn),
        "by_fold": by_fold(r, 운영),
        "by_confidence": by_confidence(r, 운영),
        "by_month": by_month(r, 운영),
        "by_item": by_item(r, 운영),
        "confidence_hist": confidence_hist(r, 운영),
        "reliability": reliability(r, 운영),
        "prices": price_series(df_mart, 운영),
        "seasonal": seasonal(df_mart, 운영),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    logging.info(f"[site] {OUT} ({OUT.stat().st_size:,} bytes)")
    logging.info(f"[site] 신호 {len(signals)}건 · 채점 {payload['live']['scored']}건")
    return OUT


if __name__ == "__main__":
    main()
