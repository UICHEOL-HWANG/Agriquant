# model/store.py
"""예측을 남기고 나중에 채점하는 곳.

**지금까지 예측은 화면에만 찍히고 사라졌다.** 그러면 "62.8% 가 실제로
유지되는가"를 영영 못 잰다. 여기서 매일의 신호를 BigQuery 에 남기고,
7거래일 뒤 실측이 들어오면 뷰가 알아서 채점한다.

## 모델 레지스트리를 따로 두지 않는 이유

아티팩트(pickle)를 저장하는 대신 **재현 가능한 식별자**를 예측 행마다
박는다. `model_version` 은
`sha256(하이퍼파라미터 + 피처 목록 + 학습 마지막 날 + 학습 행 수)` 의
앞 12자리다.

학습이 결정적이라(`early_stopping=False` · 시드 고정) **같은 식별자면
같은 모델이 재현된다.** 그래서 이 문자열이 pickle 과 같은 구실을 하면서
버전이 꼬이거나 저장소가 비대해지지 않는다.

바꿔 말하면 식별자가 달라졌다는 건 넷 중 하나가 바뀌었다는 뜻이고,
그게 성적 변화의 첫 번째 용의자다.

## 적재 규칙

**append 전용이다.** `--replace-range` 를 쓰지 않는다 — 되돌릴 수 없는
삭제를 아예 만들지 않는 쪽이 안전하고, 하루 19행이라 쌓여도 연 7천 행이다.
같은 날을 여러 번 돌리면 행이 여러 개 생기고, 채점 뷰가
`(price_date, item)` 별 최신 `predicted_at` 만 고른다.
"""
from __future__ import annotations

import hashlib
import json
import logging

import pandas as pd
from google.cloud import bigquery

from extract.database.connection import BigQueryConnection
from extract.database.models import TableSpec
from extract.database.repository import BigQueryRepository
from model import config as C
from model.features import FEATURES

_F = bigquery.SchemaField

_COLS = (
    "predicted_at", "price_date", "item", "price_kg_avg",
    "prob_up", "confidence", "signal", "threshold",
    "storage_days", "is_storable",
    "spec_version", "model_version", "train_end", "train_rows", "horizon",
)

PREDICTION = TableSpec(
    name="model_prediction",
    # 파서를 거치지 않아 한글 컬럼이 없다. rename 은 항등이지만 Repository
    # 가 이 dict 로 컬럼을 고르므로 생략할 수 없다.
    rename={c: c for c in _COLS},
    schema=[
        _F("predicted_at", "TIMESTAMP"),   # 언제 돌렸나 (같은 날 재실행 구분)
        _F("price_date", "DATE"),          # 기준일 = 예측의 as-of
        _F("item", "STRING"),
        _F("price_kg_avg", "FLOAT"),       # 기준일 가격. 채점의 분모다
        _F("prob_up", "FLOAT"),
        _F("confidence", "FLOAT"),
        _F("signal", "STRING"),            # 미룸 / 오늘 판매 / 판단 보류
        # 임계값을 박아두는 이유: 나중에 0.20 을 바꾸면 옛 예측을 새 기준으로
        # 재해석하게 된다. 그날 실제로 쓴 값이 무엇이었는지가 남아야 한다.
        _F("threshold", "FLOAT"),
        _F("storage_days", "INTEGER"),
        _F("is_storable", "BOOLEAN"),
        # 식별자가 둘인 이유: 매 실행 재학습이라 model_version 은 매일 바뀐다.
        # 그걸로 성적을 묶으면 "120개 모델이 각 16건씩" 이 되어 정작 사양의
        # 성적을 증명할 수 없다. spec_version 은 재학습해도 안 바뀌므로
        # 성적은 이걸로 묶고, 개별 예측 추적은 model_version 으로 한다.
        _F("spec_version", "STRING"),      # 사양(파라미터·피처·지평)
        _F("model_version", "STRING"),     # 사양 + 학습 데이터
        _F("train_end", "DATE"),           # 학습에 쓴 마지막 날
        _F("train_rows", "INTEGER"),
        _F("horizon", "INTEGER"),
    ],
    partition_field="price_date",
)

V_SCORE = "v_model_score"


def _spec_payload(features: list[str] | None = None) -> dict:
    """사양을 이루는 것들. **학습 데이터는 안 들어간다.**"""
    return {
        "params": C.PARAMS,
        "random_state": C.RANDOM_STATE,
        "features": list(features or FEATURES),
        "horizon": C.HORIZON,
    }


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]


def spec_version(features: list[str] | None = None) -> str:
    """사양 식별자. **재학습해도 안 바뀐다.**

    파라미터·피처·예측 지평만 보므로, 같은 사양으로 반년을 돌리면 예측
    수천 건이 하나의 `spec_version` 아래 모인다. **성적은 이걸로 묶는다** —
    "이 사양이 2,000건에서 몇 % 맞혔나"가 증명하려는 것이기 때문이다.

    피처 목록은 **순서까지** 넣는다. `categorical_features` 를 인덱스로
    넘기므로 순서가 바뀌면 다른 사양이다.
    """
    return _hash(_spec_payload(features))


def model_version(train_end, train_rows: int, features: list[str] | None = None) -> str:
    """이 모델을 재현할 수 있는 식별자. 재학습하면 바뀐다.

    사양에 **학습 마지막 날과 행 수**를 더한 해시다. 학습이 결정적이라
    같은 값이면 같은 모델이 나온다. 개별 예측을 추적할 때 쓴다.
    """
    return _hash({
        **_spec_payload(features),
        "train_end": str(pd.Timestamp(train_end).date()),
        "train_rows": int(train_rows),
    })


def save_predictions(signals: pd.DataFrame, repo: BigQueryRepository | None = None) -> int:
    """`predict_latest()` 결과를 적재한다. 적재 행 수를 반환한다.

    `signals.attrs` 에 `model_version`·`train_end`·`train_rows` 가 있어야
    한다(`predict_latest` 가 채워 넣는다). 없으면 어느 모델이 낸 신호인지
    모르는 행이 남으므로 **조용히 넘기지 않고 막는다.**
    """
    need = ("spec_version", "model_version", "train_end", "train_rows")
    missing = [k for k in need if k not in signals.attrs]
    if missing:
        raise ValueError(
            f"signals.attrs 에 {missing} 가 없습니다. "
            "predict_latest() 가 만든 표를 그대로 넘기세요."
        )
    if signals.empty:
        logging.info("[model] 적재할 신호가 없습니다")
        return 0

    df = pd.DataFrame({
        "predicted_at": pd.Timestamp.now(tz="UTC"),
        "price_date": pd.to_datetime(signals.기준일),
        "item": signals.품목.values,
        "price_kg_avg": signals["오늘 가격"].values,
        "prob_up": signals["오를 확률"].values,
        "confidence": signals.확신도.values,
        "signal": signals.신호.values,
        "threshold": C.THRESHOLD,
        "storage_days": signals.저장일수.values,
        "is_storable": signals["창고 후보"].values,
        "spec_version": signals.attrs["spec_version"],
        "model_version": signals.attrs["model_version"],
        "train_end": pd.Timestamp(signals.attrs["train_end"]),
        "train_rows": signals.attrs["train_rows"],
        "horizon": C.HORIZON,
    })
    return (repo or BigQueryRepository()).save(df, PREDICTION, mode="append")


def build_score_view(conn: BigQueryConnection | None = None) -> str:
    """예측에 실측을 붙여 채점하는 뷰를 만든다(멱등).

    HORIZON 거래일 뒤 가격은 마트에서 `LEAD` 로 가져온다. 달력일이 아니라
    **행 기준**이라 휴장일이 저절로 건너뛰어진다 — 학습 때 라벨을 만든
    방식과 같다.

    아직 7거래일이 안 지난 예측은 `future_price` 가 NULL 로 남는다.
    **버리지 않는다** — "아직 채점 전"과 "틀렸다"는 다르다.
    """
    conn = conn or BigQueryConnection()
    sql = f"""
    CREATE OR REPLACE VIEW `{conn.table_id(V_SCORE)}` AS
    WITH ranked AS (
        SELECT
            p.*,
            ROW_NUMBER() OVER (
                PARTITION BY p.price_date, p.item
                ORDER BY p.predicted_at DESC
            ) AS rn
        FROM `{conn.table_id(PREDICTION.name)}` AS p
    ),
    latest AS (
        -- 같은 날을 여러 번 돌렸으면 마지막 실행만 남긴다
        SELECT * FROM ranked WHERE rn = 1
    ),
    future AS (
        SELECT
            m.item,
            m.price_date,
            LEAD(m.price_kg_avg, {C.HORIZON}) OVER (
                PARTITION BY m.item ORDER BY m.price_date
            ) AS future_price
        FROM `{conn.table_id('mart_item_daily')}` AS m
    )
    SELECT
        l.price_date,
        l.item,
        l.spec_version,
        l.model_version,
        l.signal,
        l.prob_up,
        l.confidence,
        l.threshold,
        l.is_storable,
        l.price_kg_avg,
        f.future_price,
        -- 실제로 올랐나. 아직 미래가 안 왔으면 NULL
        CASE
            WHEN f.future_price IS NULL THEN NULL
            ELSE f.future_price > l.price_kg_avg
        END AS actual_up,
        -- 방향을 맞혔나
        CASE
            WHEN f.future_price IS NULL THEN NULL
            ELSE (l.prob_up > 0.5) = (f.future_price > l.price_kg_avg)
        END AS direction_correct,
        -- 신호대로 했을 때 실제로 받은 값. 미룸이면 미래가격, 아니면 오늘.
        CASE
            WHEN f.future_price IS NULL THEN NULL
            WHEN l.signal = '미룸' THEN f.future_price
            ELSE l.price_kg_avg
        END AS realized_price
    FROM latest AS l
    LEFT JOIN future AS f
        ON f.item = l.item
        AND f.price_date = l.price_date
    """
    conn.client.query(sql).result()
    logging.info(f"[model] 뷰 생성: {V_SCORE}")
    return conn.table_id(V_SCORE)
