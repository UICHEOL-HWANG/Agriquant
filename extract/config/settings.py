# config/settings.py
import os
from pathlib import Path

from dotenv import load_dotenv

# extract/.env 를 로드 (프로젝트 루트가 아니라 extract/ 기준)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# 각 서비스별 API Key / ID 세팅 (환경변수로 가져오거나 직접 입력)
KAMIS_CERT_KEY = os.getenv("KAMIS_CERT_KEY", "test")
KAMIS_CERT_ID = os.getenv("KAMIS_CERT_ID", "test")

DATAGO_SERVICE_KEY = os.getenv("DATAGO_SERVICE_KEY", "your_datago_service_key")
MAFRA_API_KEY = os.getenv("MAFRA_API_KEY", "sample") # 예시 키

# 기상청 ASOS 도 공공데이터포털(data.go.kr)이라 datago 와 같은 키를 쓴다.
# (키는 계정당 하나, 서비스별 '활용신청'만 따로) 다른 계정/키로 승인받았다면
# .env 에 KMA_SERVICE_KEY 를 넣어 덮어쓴다.
KMA_SERVICE_KEY = os.getenv("KMA_SERVICE_KEY", DATAGO_SERVICE_KEY)

# 한국천문연구원 특일정보(B090041)도 같은 포털이라 역시 같은 키다.
# 2026-08-04 활용신청 승인 확인(그 전까지는 403).
KASI_SERVICE_KEY = os.getenv("KASI_SERVICE_KEY", DATAGO_SERVICE_KEY)

# Base URLs
# http 는 https 로 301 되므로 처음부터 https 로 간다(리다이렉트 1회 절약).
# KAMIS 는 레거시 암호군만 제시해 base_client._LegacyTLSAdapter 가 필요하다.
URL_KAMIS = "https://www.kamis.or.kr/service/price/xml.do"
URL_KAMIS_SALES = "http://www.kamis.co.kr/service/price/xml.do"
URL_DATAGO_RISES = "https://apis.data.go.kr/B552845/risesAndFalls/info"
# shipmentSequel 은 /info operation 이 실제 엔드포인트다(무접미사는 500).
URL_DATAGO_SHIPMENT = "https://apis.data.go.kr/B552845/shipmentSequel/info"
URL_MAFRA_BASE = "http://211.237.50.150:7080/openapi"
# 기상청 지상(종관, ASOS) 일자료. numOfRows 상한 999(1200 요청 시 오류).
URL_KMA_ASOS = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
# 한국천문연구원 특일정보. 다른 소스와 달리 operation 이 여러 개라 base 만 둔다
# (getRestDeInfo / get24DivisionsInfo / getSundryDayInfo). 뒤에 /<op> 를 붙인다.
URL_KASI_SPCDE_BASE = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService"

# ── Google BigQuery ──────────────────────────────────────────
BQ_PROJECT = os.getenv("BQ_PROJECT", "agriquant")
BQ_DATASET = os.getenv("BQ_DATASET", "agriculture_data")
BQ_LOCATION = os.getenv("BQ_LOCATION", "asia-northeast3")  # 서울 리전