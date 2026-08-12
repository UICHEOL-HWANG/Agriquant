# model/monitor.py
"""매일 돌려서 조용한 고장을 잡는 곳. 읽기만 한다.

    python -m model.monitor          # 검사만
    python -m model.monitor --score  # 누적 성적도 함께

**이게 필요한 이유는 실제로 당했기 때문이다.** 2026-08-07 에 마트
커버리지가 16품목에서 11품목으로 떨어졌는데 아무것도 알려주지 않았다.
배추가 빠졌고 배추는 중간지대 4품목이다. 그대로 돌렸으면 그날 배추
판단을 통째로 건너뛰고도 몰랐을 것이다.

검사에 실패하면 **종료코드 1** 을 낸다. 스케줄러가 그걸 보고 알리게 한다.
경고문만 찍고 0 으로 끝나면 아무도 안 본다.

## 무엇을 검사하나

| 검사 | 무엇이 고장 났다는 뜻인가 |
|---|---|
| 신선도 | 수집이 멈췄다 |
| 커버리지 | 수집이 일부만 됐다 (그날 전체가 아니라) |
| 품목별 정체 | 그 품목 원천만 갱신이 멈췄다 (생강이 그랬다) |

**성적 하락은 검사 항목이 아니다.** 30번에서 폴드별 적중률이
57.2~68.1% 로 11%p 벌어졌다. 한 달 성적으로는 판단이 안 서고, 여기서
경보를 울리면 거짓 경보만 쌓인다. 성적은 `--score` 로 **보기만** 한다.
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from model.data import load_mart
from model.evaluate import item_groups

MAX_LAG_DAYS = 4
"""마트 마지막 날이 오늘로부터 이만큼 넘게 뒤처지면 실패.

주말+공휴일이면 3일까지 정상이라 4로 둔다. KAMIS 는 전날 자료가
다음 날 들어오므로 평일 기준 하루 지연이 기본이다.
"""

STALE_DAYS = 10
"""한 품목의 가격이 이 일수 넘게 전혀 안 변하면 원천 갱신 중단을 의심한다.

정체 품목(피마늘·깐마늘·생강)은 원래 안 변하니 검사 대상에서 빠진다
(운영 대상만 본다).
"""


def check_freshness(df: pd.DataFrame, today: pd.Timestamp | None = None) -> dict:
    """마트가 얼마나 뒤처졌나."""
    today = pd.Timestamp(today) if today is not None else pd.Timestamp.now().normalize()
    last = df.price_date.max()
    lag = (today - last).days
    return {"검사": "신선도", "통과": lag <= MAX_LAG_DAYS,
            "값": f"{lag}일 지연 (마지막 {last:%Y-%m-%d})",
            "기준": f"{MAX_LAG_DAYS}일 이하"}


def check_coverage(df: pd.DataFrame, items: list[str], lookback: int = 10) -> dict:
    """마지막 날 품목 수가 최근 중앙값에 못 미치나.

    휴장이면 그날 전체가 없어 이 검사에 안 걸린다. 걸리는 건 **일부만
    들어온 날**이고, 그게 정확히 수집 미완이다.
    """
    d = df[df.item.isin(items)]
    daily = d.groupby("price_date").item.nunique().tail(lookback)
    마지막 = int(daily.iloc[-1])
    직전 = daily.iloc[:-1]
    기준 = float(직전.median()) if len(직전) else float(마지막)
    return {"검사": "커버리지", "통과": 마지막 >= 기준,
            "값": f"{마지막}품목 (직전 {len(직전)}일 중앙값 {기준:.0f})",
            "기준": "중앙값 이상"}


def check_stale_items(df: pd.DataFrame, items: list[str]) -> dict:
    """가격이 오래 멈춘 품목이 있나. 그 품목 원천만 죽은 경우를 잡는다."""
    멈춤 = []
    for item, g in df[df.item.isin(items)].groupby("item"):
        최근 = g.sort_values("price_date").price_kg_avg.tail(STALE_DAYS + 1)
        if len(최근) > STALE_DAYS and bool((최근.diff().dropna() == 0).all()):
            멈춤.append(item)
    return {"검사": "품목별 정체", "통과": not 멈춤,
            "값": ", ".join(멈춤) if 멈춤 else "없음",
            "기준": f"{STALE_DAYS}일 연속 동일가 없음"}


def run_checks(source: str = "auto") -> tuple[pd.DataFrame, bool]:
    """검사를 전부 돌리고 (표, 전체통과) 를 준다."""
    df = load_mart(source)
    운영 = item_groups(df)["운영 대상"]
    t = pd.DataFrame([check_freshness(df), check_coverage(df, 운영),
                      check_stale_items(df, 운영)])
    t["판정"] = t.통과.map({True: "통과", False: "실패"})
    return t[["검사", "판정", "값", "기준"]], bool(t.통과.all())


def score_so_far(days: int = 180) -> pd.DataFrame:
    """`v_model_score` 에서 누적 성적을 읽는다.

    **경보용이 아니라 참고용이다.** 반년 단위로 봐야 판단이 선다(30번).

    `spec_version` 으로 묶는다. 매 실행 재학습이라 `model_version` 으로
    묶으면 "120개 모델이 각 16건씩" 이 되어 정작 사양의 성적을 못 본다.
    `refits` 가 그 사양으로 몇 번 재학습했는지를 알려준다.

    **사양을 바꾸면 행이 하나 더 생긴다.** 품목을 늘리거나 피처를 더하면
    `spec_version` 이 갈리므로, 옛 사양과 새 사양의 성적이 나란히 쌓여
    개선 여부를 그대로 비교할 수 있다.
    """
    from extract.database.connection import BigQueryConnection
    from model.store import V_SCORE

    conn = BigQueryConnection()
    sql = f"""
    SELECT
        spec_version,
        COUNT(DISTINCT model_version)              AS refits,
        COUNT(*)                                   AS predictions,
        COUNTIF(direction_correct IS NOT NULL)     AS scored,
        ROUND(AVG(IF(direction_correct IS NULL, NULL,
                     IF(direction_correct, 1.0, 0.0))) * 100, 2)
                                                   AS accuracy_pct,
        ROUND(AVG(IF(confidence >= threshold AND direction_correct IS NOT NULL,
                     IF(direction_correct, 1.0, 0.0), NULL)) * 100, 2)
                                                   AS confident_accuracy_pct,
        MIN(price_date)                            AS first_date,
        MAX(price_date)                            AS last_date
    FROM `{conn.table_id(V_SCORE)}`
    WHERE price_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {days} DAY)
    GROUP BY spec_version
    ORDER BY last_date DESC
    """
    return conn.client.query(sql).to_dataframe()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="데이터·모델 상태 검사")
    ap.add_argument("--source", default="auto", choices=["auto", "cache", "bigquery"])
    ap.add_argument("--score", action="store_true",
                    help="v_model_score 에서 누적 성적도 읽는다 (BigQuery 조회)")
    ap.add_argument("--days", type=int, default=180, help="성적을 볼 기간")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    t, ok = run_checks(a.source)
    print("\n=== 상태 검사 ===")
    print(t.to_string(index=False))

    if a.score:
        print(f"\n=== 최근 {a.days}일 성적 (참고) ===")
        try:
            s = score_so_far(a.days)
            print(s.to_string(index=False) if len(s)
                  else "  아직 채점된 예측이 없습니다.")
            print("\n  반년 단위로 보세요. 한 달 성적으로는 판단이 안 섭니다(30번).")
        except Exception as e:                       # noqa: BLE001
            print(f"  성적 조회 실패: {type(e).__name__}: {e}")
            print("  model_prediction 테이블과 v_model_score 뷰가 있는지 보세요.")

    print(f"\n판정: {'전부 통과' if ok else '실패 항목 있음'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
