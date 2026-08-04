# clients/datago_client.py
from urllib.parse import unquote

from extract.config import settings

from .base_client import BaseClient

# shipmentSequel 응답으로 받을 전체 컬럼(selectable). 스펙의 리턴 필드 전부.
SHIPMENT_COLS = (
    "spmt_ymd,whsl_mrkt_cd,whsl_mrkt_nm,corp_cd,corp_nm,"
    "gds_lclsf_cd,gds_lclsf_nm,gds_mclsf_cd,gds_mclsf_nm,"
    "gds_sclsf_cd,gds_sclsf_nm,unit_cd,unit_nm,unit_qty,"
    "avg_spmt_qty,avg_spmt_amt,"
    "ww1_bfr_avg_spmt_qty,ww1_bfr_avg_spmt_amt,"
    "ww2_bfr_avg_spmt_qty,ww2_bfr_avg_spmt_amt,"
    "ww3_bfr_avg_spmt_qty,ww3_bfr_avg_spmt_amt,"
    "ww4_bfr_avg_spmt_qty,ww4_bfr_avg_spmt_amt"
)


class DatagoClient(BaseClient):
    """공공데이터포털(B552845) API 수집 클라이언트.

    서비스키는 '인코딩키'(%-escaped)라 requests 에 그대로 넘기면 이중
    인코딩되어 인증이 깨진다. __init__ 에서 unquote 로 한 번 풀어,
    requests 가 클린 인코딩하도록 한다.
    """

    def __init__(self, service_key: str = settings.DATAGO_SERVICE_KEY):
        super().__init__()
        self.service_key = unquote(service_key)   # 이중인코딩 회피

    # 4. 가격 등락 정보 조회
    def get_rises_and_falls(self, page_no: int = 1, num_of_rows: int = 100) -> dict:
        params = {
            "serviceKey": self.service_key,
            "returnType": "json",
            "pageNo": page_no,
            "numOfRows": num_of_rows,
        }
        return self._get(settings.URL_DATAGO_RISES, params=params)

    # 5. 출하량 추이 정보 조회 (단일 페이지)
    def get_shipment_sequel(
        self,
        spmt_ymd: str,
        gds_lclsf_cd: str,
        gds_mclsf_cd: str,
        page_no: int = 1,
        num_of_rows: int = 1000,
    ) -> dict:
        """출하일자·대분류·중분류로 한 페이지 조회.

        spmt_ymd 는 필수(없으면 0건). 도매시장·법인 코드는 넣지 않아
        전체 법인·시장을 조회한다.
        """
        params = {
            "serviceKey": self.service_key,
            "returnType": "json",
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            "selectable": SHIPMENT_COLS,
            "cond[spmt_ymd::EQ]": spmt_ymd,
            "cond[gds_lclsf_cd::EQ]": gds_lclsf_cd,
            "cond[gds_mclsf_cd::EQ]": gds_mclsf_cd,
        }
        return self._get(settings.URL_DATAGO_SHIPMENT, params=params)

    def iter_shipment(
        self,
        spmt_ymd: str,
        gds_lclsf_cd: str,
        gds_mclsf_cd: str,
        num_of_rows: int = 1000,
    ) -> list[dict]:
        """출하일자·대분류·중분류 하루치 전량(페이지네이션) 반환.

        totalCount 까지 pageNo 를 넘겨가며 item 을 모은다.
        응답 이상(빈 body 등) 시 모은 만큼 반환한다.
        """
        rows: list[dict] = []
        page = 1
        while True:
            resp = self.get_shipment_sequel(
                spmt_ymd, gds_lclsf_cd, gds_mclsf_cd,
                page_no=page, num_of_rows=num_of_rows,
            )
            body = (resp or {}).get("response", {}).get("body", {})
            total = body.get("totalCount", 0) or 0
            item = body.get("items", {})
            item = item.get("item", []) if isinstance(item, dict) else []
            if isinstance(item, dict):   # 원소 1개면 dict 로 올 수 있음
                item = [item]
            rows.extend(item)
            if not item or len(rows) >= total:
                return rows
            page += 1
