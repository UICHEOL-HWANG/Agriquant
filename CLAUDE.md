# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

주석·문서는 한국어로 쓴다(기존 코드 전체가 그렇다).

## 데이터 분석 작업

**분석(노트북·쿼리·해석)을 할 때는 [docs/analysis-guide.md](docs/analysis-guide.md)
를 먼저 읽고 그 규칙을 따른다.** 이 CLAUDE.md 는 '저장소가 어떻게 동작하는가'이고,
그쪽은 '분석을 어떻게 진행하는가'다.

**분석을 이어서 할 때는 [docs/analysis-status.md](docs/analysis-status.md) 도
같이 읽는다.** 지금까지 무엇을 알아냈고 무엇이 이미 반증됐는지, 다음에 뭘 할지가
거기 있다. 특히 '반복하지 말 것' 절을 먼저 보면 같은 실험을 되풀이하지 않는다.

특히 지켜야 할 것:

- **쿼리는 항상 보여주고 설명한 뒤 실행한다.** 조용히 돌리고 결과만 던지지 않는다.
  SQL 스타일 규칙도 그 문서에 있다(예약어 대문자·snake_case·명시적 JOIN·CTE).
- **판단이 걸린 쿼리는 실행 전에 예측을 물어본다.** 스키마 확인 같은 조회는 그냥 실행한다.
- **결과 해석은 사용자가 먼저 한다.** 결과가 나오자마자 해석까지 말해버리지 않는다.
- **질문 세우기와 해석은 대신하지 않는다.** 쿼리·계산·차트·파일 정리는 빠르게 처리한다.
- 노트북·차트·쿼리 저장 규칙은 그 문서 12절에 있다.

## 무엇을 하는 저장소인가

농산물 도매가격 예측을 위한 ELT 파이프라인 + 예측 모델. 5개 공공 API를 수집해
BigQuery(`agriquant.agriculture_data`)에 원천 테이블로 쌓고, 그 위에 '날짜 ×
표준품목' 그레인의 분석용 뷰를 만든 뒤, **7거래일 뒤 가격의 방향**을 맞힌다.

**맞히는 것은 방향이지 값이 아니다.** 변화율(몇 % 오를까)은 못 맞힌다고
결론났다(노트북 08·10). 방향 적중률 62.8%(찍기 51.1%)이고, 모델이 확신하는
날만 고르면 72.1% 다. 쓸 자리는 **출하 시점 결정**이다 — "오른다면 미루고
내린다면 오늘 판다".

분석은 노트북 01~30 으로 끝났고 그 결론이 `model/` 패키지에 들어 있다.
무엇을 왜 그렇게 정했는지는 [docs/analysis-status.md](docs/analysis-status.md).

## 실행

전제: GCP 인증(ADC)과 `extract/.env`(커밋 금지, `.gitignore`에 걸려 있음).

```bash
gcloud auth application-default login && gcloud config set project agriquant
```

의존성은 uv 로 관리한다(`uv.lock`). 프로젝트는 editable 설치되므로 하위
디렉터리에서도 `from extract...` 가 된다.

```bash
uv sync
```

수집 실행(전체 옵션은 `main.py` 상단 주석에 소스별로 정리돼 있다):

```bash
uv run python main.py --source datago --start 2026-07-24 --end 2026-07-30 --replace-range --dry-run
```

`--source` 값: `kamis`(기본, monthly/yearly/wholesale) · `datago`(출하량) ·
`backfill`(KAMIS 일별 도매가 과거 이력) · `weather`(기상청 ASOS) ·
`holiday`(KASI 특일정보) · `mafra`(농협계약재배) · `transform`(수집 아님, 뷰 생성).

모델 실행:

```bash
uv run python -m model.evaluate --check   # 노트북 15·19 수치를 재현하는지
uv run python -m model.monitor            # 데이터 상태 (실패 시 종료코드 1)
uv run python -m model.predict --save     # 오늘 신호 → model_prediction
```

테스트·린터·CI 설정이 없다. 검증은 실행 로그의 행 수와 `bq query`,
그리고 **`model.evaluate --check`** 로 한다. 없는 테스트 명령을 만들어내지 말 것.

## 적재 모드 — 여기가 이 저장소의 핵심 계약

세 가지고, 셋 다 의미가 다르다.

- `append` (기본): 이어 쌓기. 같은 구간을 다시 돌리면 **중복이 쌓인다**.
- `--replace`: 테이블 통째 교체(`WRITE_TRUNCATE`). 요청 구간 밖까지 날아간다.
- `--replace-range`: `--start~--end` 구간만 DELETE 후 append. `--start/--end` 필수.

`replace_range` 는 BigQuery write_disposition 이 아니라 'DELETE → LOAD' 2단계라
`_WRITE_MODE` 에 없다. 파이프라인이 `pipelines._chunk_mode()` 를 통해
`repo.delete_range()` 를 부른 뒤 `"append"` 로 바꿔 넘긴다. 이 단계를 빠뜨리고
`repo.save()` 에 `replace_range` 를 그대로 넘기면 `ValueError` 로 막힌다
(조용히 중복이 쌓이는 걸 막으려는 장치다).

지켜야 할 세 규칙:

1. **삭제는 청크 단위로, 적재 직전에.** 요청 구간 전체를 미리 지우면 수집이
   중간에 끊길 때(datago 는 일일한도 429 로 자주 끊긴다) 지운 구간이 빈 채로
   남는다 — 부분 적재보다 나쁜 상태다. flush 단위는 datago=월, weather/backfill=연.
2. **0행이면 지우지도 적재하지도 않는다.** 0행이 '휴장'인지 '수집 실패'인지
   구분할 수 없어서다. `replace` 모드에서는 `first` 플래그도 소모하지 않는다
   (빈 첫 청크가 테이블 교체를 건너뛰면 그 뒤가 전부 append 라 옛 데이터가 남는다).
3. **수집 품목 목록은 테이블에 있는 품목 전체와 같아야 한다.**
   `delete_range` 는 **날짜만 보고 품목을 안 가린다.** 구간을 통째로 지운 뒤
   목록에 있는 품목만 다시 넣으므로, 목록에서 빠진 품목은 그 구간이 증발한다.

   2026-08-11 실측: `DEFAULT_VEGETABLES` 가 18품목인데 마트는 19품목이라,
   일일 스크립트를 한 번 돌리자 피마늘의 8/4~8/7 이 사라졌다(마지막 날짜가
   8/7 → 8/3 으로 후퇴). 목록을 19품목으로 맞춰 고쳤다.

   **수집 대상과 분석 대상은 다른 문제다.** 이력이 짧거나 정체된 품목이라도
   테이블에 있으면 받아야 한다. 분석에서 빼는 건 모델 쪽 일이다
   (`model/config.py` 의 `STAGNANT_LINE`).

`--replace-range` 대상은 `pipelines.RANGE_SPECS` 에 등록된 소스뿐이다.
`mafra`/`holiday` 는 날짜 구간 개념이 없는 전량 스냅샷이라 항상 `replace` 로
강제된다(`main.py` 에서 mode 인자를 무시하고 넘긴다).

## 계층 구조

```
extract/clients/   HTTP 만. 응답 dict/list 를 그대로 돌려준다
extract/parsers/   순수 변환. 클라이언트를 import 하지 않아 인증키 없이 테스트 가능
extract/database/  DataFrame → BigQuery. TableSpec 이 rename·schema·partition 을 쥔다
extract/pipelines.py  수집 → 파싱 → 적재 오케스트레이션 + 청크·모드 결정
transform/         적재된 원천 → 분석용 뷰 (BigQuery 안에서만 동작, 멱등)
model/             접은 것으로 방향을 맞히고 그 성적을 잰다
```

`model/` 안은 이렇다. **노트북 01~30 의 결론을 옮긴 것이라 상수를 바꾸려면
근거 노트북을 먼저 읽는다**(`model/config.py` 의 각 값에 번호가 달려 있다).

```
model/config.py    확정 상수 (하이퍼파라미터·폴드·임계값·품목 규칙)
model/data.py      마트 로드 (BigQuery / 파케이 캐시)
model/features.py  채택 피처 21개. 학습·추론이 같은 함수를 쓴다
model/metrics.py   지표 8개 + 커버리지 맞춤 비교
model/evaluate.py  워크포워드 평가 + 재현 검사
model/predict.py   오늘 시점 신호
model/store.py     예측 적재(model_prediction) + 채점 뷰(v_model_score)
model/monitor.py   신선도·커버리지·정체 검사
```

**파서는 한글 컬럼을 만들고, `TableSpec.rename` 이 영문 snake_case 로 바꾼다.**
`rename` 에 없는 컬럼(`caption_원문`, `가격_원문` 등 원문·디버그 컬럼)은 적재
시점에 자연스럽게 버려진다. 컬럼을 추가하려면 파서와 `models.py` 양쪽을 고쳐야
한다. `datago_parser` 만 예외로 이미 영문이라 rename 이 '원문 영문 → 정돈된 영문'이다.

**수집 대상 품목코드는 코드가 아니라 BigQuery `item_code` 테이블에 있다.**
`config/items.py` 는 품목'명'만 정하고(`DEFAULT_VEGETABLES`),
`repo.load_item_targets()` 가 `(category_code, item_code)` 를 조회해 채운다.
카테고리 필터가 아니라 이름 화이트리스트인 이유는 감자가 부류 200(채소)이 아니라
100(식량작물)이기 때문이다.

**소스별 품목 표기 매핑은 SQL 이 아니라 `transform/dim.py` 에 있다.** 뷰 SQL 을
문자열로 조립할 때 CTE(`station_map`, `item_map`)로 주입한다. 수집 대상
(`config/items.py`)이 바뀌면 조인이 따라가야 해서다. 표준 품목명은 KAMIS 표기를
따른다(datago '대파' → '파').

## 오류 처리 계약 — 소스마다 다르다

`BaseClient._get()` 은 실패 시 **예외가 아니라 `{}` 를 반환**한다(재시도 3회,
지수 백오프 1s→2s, 재시도 대상은 429·5xx·타임아웃·연결오류·JSON 파싱 실패).
4xx 는 재시도하지 않는다.

그 위에서 소스별로 갈린다:

- `mafra`/`kma`/`kasi`: totalCnt 미달·오류코드를 `GridFetchError` /
  `WeatherFetchError` / `SpecialDayFetchError` 로 올린다. **잡지 않는다** —
  잘린 데이터로 테이블을 덮어쓰지 않게 하려는 것이 목적이라 `run_mafra` 등에서
  그대로 위로 통과시킨다.
- `datago.iter_shipment`: 응답 이상 시 **모은 만큼 조용히 반환**한다. 그래서
  '0행 = 휴장'과 '0행 = 수집 실패'가 구분되지 않고, 위의 0행 보존 규칙이 필요해진다.

로그에는 인증키가 남으면 안 된다. `base_client._mask()` 가 쿼리 파라미터명과
값 자체를 모두 치환한다(MAFRA 는 키를 URL 경로에 박아서 값 치환이 필요하다).
새 클라이언트를 붙이면 `_SECRET_PARAMS` / `_secret_values()` 도 같이 갱신할 것.

## 외부 API 제약 (재발견하지 말 것)

- **KAMIS**: 서버가 레거시 암호군만 제시해 `base_client._LegacyTLSAdapter`
  (SECLEVEL=1)가 없으면 handshake 실패. curl 로는 되는데 파이썬만 실패하면 이것이다.
  `periodWholesaleProductList` 는 1회 요청 **최대 1년** → `_year_ranges()` 로 끊는다.
  그리고 **요청 구간보다 넓게 돌려준다**: 하루(`--start`=`--end`)를 달라 해도
  몇 달치가 온다(2026-08-11 실측 — 8/7 하루를 요청했는데 2026-04~08 이 왔다).
  좁은 구간을 `append` 로 받으면 그 넓은 구간에 **중복이 쌓인다**(그때
  `kamis_wholesale_obs` 2,908행 · `kamis_wholesale_agg` 806행이 이렇게 생겼다).
  **좁은 구간 재수집은 `--replace-range` 를 쓰고**, 굳이 `append` 로 받았다면
  적재 직후 배수를 확인할 것: `COUNT(*) / COUNT(DISTINCT ...)` 가 1.0 이어야
  한다(2021~2026-03 전 구간이 1.0 이라, 이 원천에 동일 행이 정상적으로 두 번
  오는 일은 없다).
- **data.go.kr (datago·기상청·KASI 공용 키)**: `403` = 활용신청 미승인/만료,
  `429` = 일일한도 초과. **원인이 다르니 같이 취급하지 말 것.** 서비스키는
  인코딩키라 `unquote()` 로 한 번 풀어야 이중 인코딩이 안 된다.
- **ASOS**: 요청당 `numOfRows` 상한 999(지점×연도 = 딱 1요청). **전날 자료까지만**
  제공하므로 종료일이 오늘/미래면 code=99 로 거부된다 → `run_weather` 가 어제로 낮춘다.
- **KASI 특일정보**: `solYear` 단위 조회라 `--start/--end` 는 연도만 쓴다.
  전 기간 400여 행이라 항상 `replace`. 미래 2년까지 받는 이유는 예측 시점에
  '다가올 명절'을 알아야 `days_to_major_holiday` 가 성립하기 때문이다.
  2029년부터는 미고시라 0행이 정상이다.

## 뷰 계층에서 주의할 것

- `mart_item_daily` 는 **가격 행을 기준(FROM)** 으로 나머지를 LEFT JOIN 한다.
  거래가 없던 날은 행 자체가 없다 — 달력으로 채우거나 0/보간으로 만들어내면
  학습이 그 가짜를 배운다.
- `build_all()` 은 원천 테이블이 없으면 해당 뷰를 건너뛰고 마트를 그 컬럼 없이
  만든다. `CREATE OR REPLACE` 라 나중에 적재하고 다시 돌리면 컬럼이 붙는다.
- `kasi_special_day.is_holiday_raw` 를 직접 쓰지 말 것. 제헌절·노동절까지
  `true` 로 온다. 휴일 판정은 반드시 `v_calendar_daily` 를 거친다. 실측상 실제
  휴장은 명절 연휴·신정·일요일뿐이고, 광복절·한글날·노동절·제헌절은 정상 개장이었다.
- `v_price_daily` 는 `no_trade` 행을 제외한다(배추 73%·감자 45%가 미거래). 넣으면
  가격이 아니라 '거래 여부'를 재게 된다. `unit_base='kg'` 조건도 단위가 섞이는
  순간 조용히 틀리지 않게 하려는 방어다.
- `kst`(절기 진입 시각)는 쓰지 않는다. 2024년 잡절 7행에만 시각이 아니라 MMDD 가 들어있다.

## 모델 계층에서 지킬 것

- **상수를 바꾸면 `model.evaluate --check` 를 돌린다.** 노트북 15·19 수치를
  재현하는지 보는 검사다. 재현이 안 되는 상태에서 성능을 비교하면 이식 실수인지
  모델 때문인지 영영 못 가린다.
- **`--check` 는 행 수를 같이 본다.** 마트가 매일 커져 숫자가 조금씩 움직이므로,
  `EXPECTED_ROWS` 와 다르면 '회귀'가 아니라 **'판정 보류'** 로 찍는다. 행 수가
  같은데 숫자가 다를 때만 코드를 의심한다.
- **학습과 추론은 같은 `build_features()` 를 쓴다.** 추론은 라벨이 없는 행이
  필요해서 `drop_unlabeled=False` 플래그만 다르다. 추론용 피처 함수를 따로
  만들면 한쪽만 고쳐지는 순간 조용히 틀린 예측이 나온다.
- **노이즈 플로어는 시드가 아니라 피처 값을 흔들어 잰다.** `early_stopping=False`
  에 기본 `max_features` 면 학습이 결정적이라 `random_state` 만 바꾸면 표준편차가
  정확히 0 이 나온다.
- **모델을 비교할 때는 커버리지를 맞춘다**(`score_at_coverage`). 확신도 분포가
  모델마다 달라 고정 임계값 0.20 으로 재면 표본 크기가 달라져 비교가 깨진다.
- **`model_prediction` 은 append 전용이다.** 같은 날을 여러 번 돌리면 행이 쌓이고
  `v_model_score` 가 `(price_date, item)` 별 최신 `predicted_at` 만 고른다.
  되돌릴 수 없는 삭제를 아예 만들지 않으려는 것이다.
- **아티팩트(pickle)를 저장하지 않는다.** `model_version` 이
  `sha256(파라미터 + 피처목록 + 학습마지막날 + 학습행수)[:12]` 이고 학습이
  결정적이라, 같은 식별자면 같은 모델이 재현된다.
- **성적으로 경보를 울리지 않는다.** 폴드별 적중률이 57.2~68.1% 로 11%p 벌어져서
  (노트북 30) 짧게 보면 거짓 경보만 쌓인다. `model.monitor` 는 데이터만 검사하고
  성적은 `--score` 로 보기만 한다.

## 파괴적 작업

`--replace-range` 는 되돌릴 수 없다. 날짜를 잘못 넣으면 100만 행이 날아간다.
그래서 `--start/--end` 를 손으로 명시하게 강제하고 `--dry-run`(COUNT 만 셈)을
둔다. **구간 삭제 전에는 항상 `--dry-run` 을 먼저 돌린다.**
