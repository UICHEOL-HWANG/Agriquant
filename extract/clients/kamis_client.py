# clients/kamis_client.py
from .base_client import BaseClient
from extract.config import settings

class KamisClient(BaseClient):
    """KAMIS(농수산물유통정보) API 수집 클라이언트"""

    def __init__(self, cert_key: str = settings.KAMIS_CERT_KEY, cert_id: str = settings.KAMIS_CERT_ID):
        super().__init__()
        self.cert_key = cert_key
        self.cert_id = cert_id

    # 일별 품목별 도매 가격자료(기간설정가능, 최대1년)
    def get_period_wholesale_product_list(self,
                                    *,
                                    p_startday: str,
                                    p_endday: str,
                                    category_code: str = "200",
                                    p_itemcode: str = "211",
                                    product_cls_code: str = "01",
                                    ) -> dict:
        """일별 도매가. 날짜는 `YYYY-MM-DD` 이고 **기본값이 없다.**

        예전엔 `2025-01-01`~`2026-01-01` 이 박혀 있었다. 날짜를 안 넘기는
        호출(`run_all` 이 그랬다)이 조용히 2025년 1년치를 받아 append 로
        쌓는 사고로 이어진다. 기본값을 없애 그런 호출이 즉시 TypeError 로
        드러나게 한다.

        **이 API 는 요청 구간보다 넓게 돌려준다**(2026-08-11 실측: 하루를
        요청했는데 2026-04~08 이 왔다). 받은 쪽에서 잘라야 하며,
        `pipelines.run_wholesale` 이 그 일을 한다.
        """
        params = {
            "action" : "periodWholesaleProductList",
            "p_cert_key": self.cert_key,
            "p_cert_id": self.cert_id,
            "p_returntype": "json",
            "p_startday" : p_startday,
            "p_endday" : p_endday,
            "p_itemcategorycode": category_code,
            "p_itemcode" : p_itemcode,
            "p_product_cls_code": product_cls_code,
        }
        return self._get(settings.URL_KAMIS, params=params)

    # 2. 월별 도.소매가격정보
    def monthlySalesList(self,
                         p_yyyy,
                         p_period,
                         p_itemcategorycode,
                         p_itemcode
                                )-> dict:
        params = {
            "action": "monthlySalesList",
            "p_cert_id": self.cert_id,
            "p_cert_key": self.cert_key,
            "p_returntype" : "json",
            "p_yyyy" : p_yyyy,
            "p_period" : p_period,
            "p_itemcategorycode" : p_itemcategorycode,
            "p_itemcode" : p_itemcode
        }

        return self._get(settings.URL_KAMIS, params=params)

    # 3. 연도별 도.소매가격정보
    def yearlySalesList(self,
                        p_yyyy,
                        p_itemcategorycode,
                        p_itemcode,
                        ) -> dict:
        params = {
            "action": "yearlySalesList",
            "p_cert_id": self.cert_id,
            "p_cert_key": self.cert_key,
            "p_returntype" : "json",
            "p_yyyy" : p_yyyy,
            "p_itemcategorycode" : p_itemcategorycode,
            "p_itemcode" : p_itemcode
        }

        return self._get(settings.URL_KAMIS, params=params)
