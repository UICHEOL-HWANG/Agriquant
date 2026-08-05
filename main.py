# main.py — 농산물 데이터 수집 → 파싱 → BigQuery 적재 진입점
#
# KAMIS(가격) 사용법:
#   python main.py                       # 전체 파이프라인, append
#   python main.py yearly                # 특정 파이프라인만
#   python main.py monthly yearly        # 여러 개
#   python main.py --replace             # 테이블 교체(멱등 재적재)
#   python main.py yearly --items 배추 무 # 특정 파이프라인·품목만
#
# datago(출하량) 사용법:
#   python main.py --source datago               # 최근 1년 일별
#   python main.py --source datago --days 30     # 최근 30일
#   python main.py --source datago --start 2025-01-01 --end 2025-12-31
#   python main.py --source datago --replace     # 테이블 교체
#
# 구간 재수집(--replace-range) — datago / weather / backfill 전용:
#   python main.py --source datago --start 2025-07-24 --end 2025-07-30 \
#       --replace-range --dry-run   # 먼저 지워질 행 수만 확인
#   python main.py --source datago --start 2025-07-24 --end 2025-07-30 \
#       --replace-range             # 그 구간만 지우고 다시 적재
#   같은 구간을 몇 번을 다시 돌려도 중복이 안 쌓인다. 부분 적재로 남은
#   날짜를 콘솔에서 손으로 DELETE 할 필요가 없어진다.
#   구간 밖은 건드리지 않는다(--replace 와 다른 점). --start/--end 필수.
#
# mafra(농협계약재배) 사용법:
#   python main.py --source mafra                # 전량 스냅샷 재적재(replace)
#
# backfill(KAMIS 일별 도매가 과거 이력) 사용법:
#   python main.py --source backfill                    # 최근 5년(2021-01-01~오늘)
#   python main.py --source backfill --years 3          # 최근 3년
#   python main.py --source backfill --start 2021-01-01 --end 2021-12-31
#   python main.py --source backfill --items 배추       # 특정 품목만
#   KAMIS는 1회 요청 최대 1년이라 연 단위로 끊어 적재한다(해마다 flush).
#
# weather(기상청 ASOS 일자료) 사용법:
#   python main.py --source weather                     # 최근 5년, 주산지 13개 지점
#   python main.py --source weather --years 3           # 최근 3년
#   python main.py --source weather --start 2021-01-01 --end 2021-12-31
#   ASOS는 요청당 999행 상한이라 지점×연도가 딱 1요청이다.
#
# holiday(한국천문연구원 특일정보) 사용법:
#   python main.py --source holiday                     # 2021 ~ 올해+2년
#   python main.py --source holiday --years 3           # 최근 3년 + 미래 2년
#   python main.py --source holiday --start 2021-01-01 --end 2028-12-31
#   공휴일·24절기·잡절을 연 단위로 받는다(solYear 조회라 월·일은 무시).
#   전량 400여 행이라 항상 replace 로 통째 재적재한다.
#   미래분까지 받는 이유: 예측 시점에 다가올 명절을 알아야 피처가 성립한다.
#
# transform(적재된 원천 → 분석용 뷰) 사용법:
#   python main.py --source transform
#   수집이 아니라 BigQuery 안에서 뷰만 만든다(멱등).
#   v_price_daily / v_weather_daily / v_shipment_daily / mart_item_daily.
#   datago 적재가 끝난 뒤 다시 돌리면 마트에 출하량 컬럼이 붙는다.
#
# 사전 조건: GCP 인증(ADC)
#   gcloud auth application-default login
#   gcloud config set project agriquant
import argparse
from datetime import date

from extract.database import REPLACE_RANGE
from extract.pipelines import (
    RANGE_SPECS,
    preview_range,
    run_all,
    run_datago,
    run_holiday,
    run_kamis_backfill,
    run_mafra,
    run_weather,
)
from transform import build_all

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="농산물 데이터 수집 → 파싱 → BigQuery 적재",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="KAMIS 파이프라인 (monthly/yearly/wholesale). 생략 시 전체",
    )
    parser.add_argument(
        "--source",
        choices=["kamis", "datago", "mafra", "backfill", "weather", "holiday",
                 "transform"],
        default="kamis",
        help="수집 소스. 기본 kamis. datago=출하량 추이, mafra=농협계약재배, "
             "backfill=KAMIS 일별 도매가 과거 이력, weather=기상청 ASOS 일자료, "
             "holiday=한국천문연구원 특일정보(공휴일·24절기·잡절), "
             "transform=적재된 원천으로 분석용 뷰 생성(수집 아님).",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="[backfill/weather/holiday] 최근 몇 개 연도를 받을지. "
             "기본 5(= 2021-01-01~오늘). --start/--end 지정 시 무시",
    )
    # --replace 와 --replace-range 는 동시에 못 준다. 둘 다 '기존 행을 지운다'
    # 인데 지우는 범위가 정반대(테이블 전체 vs 요청 구간)라 조합이 무의미하다.
    write_mode = parser.add_mutually_exclusive_group()
    write_mode.add_argument(
        "--replace",
        action="store_true",
        help="테이블을 통째로 교체(WRITE_TRUNCATE). 생략 시 이어 쌓기(append)",
    )
    write_mode.add_argument(
        "--replace-range",
        action="store_true",
        help="[datago/weather/backfill] --start~--end 구간만 지우고 다시 적재. "
             "같은 구간을 다시 돌려도 중복이 안 쌓인다. --start/--end 필수",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="[--replace-range] 지우지 않고 '지워질 행 수'만 세어 본다",
    )
    parser.add_argument(
        "--items",
        nargs="*",
        metavar="품목명",
        help="[kamis] 수집할 품목명 (예: 배추 무 당근). 생략 시 기본 채소 목록",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="[datago] 최근 며칠 수집. 기본 365(최근 1년). start/end 지정 시 무시",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="[datago] 수집 시작일. 지정 시 --days 무시",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="[datago] 수집 종료일. 생략 시 오늘",
    )
    args = parser.parse_args()

    if args.replace_range:
        # 구간 삭제는 되돌릴 수 없다. --years/--days 로 계산된 기본 구간이
        # 조용히 잡혀 엉뚱한 데이터가 날아가는 일이 없도록, 양 끝 날짜를
        # 반드시 손으로 명시하게 한다.
        if args.source not in RANGE_SPECS:
            parser.error(
                f"--replace-range 는 {sorted(RANGE_SPECS)} 에서만 쓸 수 있다. "
                f"--source {args.source} 는 날짜 구간으로 지울 대상이 아니다"
            )
        if args.start is None or args.end is None:
            parser.error("--replace-range 에는 --start 와 --end 를 모두 지정해야 한다")
        if args.start > args.end:
            parser.error(f"구간이 뒤집혔다: --start {args.start} > --end {args.end}")
        mode = REPLACE_RANGE
    elif args.replace:
        mode = "replace"
    else:
        mode = "append"

    if args.dry_run:
        if mode != REPLACE_RANGE:
            parser.error("--dry-run 은 --replace-range 와 함께 쓴다")
        preview_range(args.source, args.start, args.end)
        raise SystemExit(0)

    if args.source == "transform":
        # 수집이 아니라 이미 적재된 원천을 뷰로 잇는 단계. mode/기간 인자는 안 쓴다.
        build_all()
    elif args.source == "datago":
        run_datago(start=args.start, end=args.end, days=args.days, mode=mode)
    elif args.source == "mafra":
        # 전량 스냅샷이라 append 는 중복 적재 위험 → 항상 replace.
        run_mafra(mode="replace")
    elif args.source == "holiday":
        # 전 기간 받아도 400여 행뿐인 전량 스냅샷 → mafra 와 같이 항상 replace.
        # solYear 단위 조회라 --start/--end 는 연도만 쓴다.
        run_holiday(
            start_year=args.start.year if args.start else None,
            end_year=args.end.year if args.end else None,
            years=args.years,
            mode="replace",
        )
    elif args.source == "weather":
        run_weather(
            start=args.start,
            end=args.end,
            years=args.years,
            mode=mode,
        )
    elif args.source == "backfill":
        run_kamis_backfill(
            item_names=args.items or None,
            start=args.start,
            end=args.end,
            years=args.years,
            mode=mode,
        )
    else:
        run_all(
            args.targets or None,
            item_names=args.items or None,
            mode=mode,
        )
