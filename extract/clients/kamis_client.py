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
                                    p_startday: str = "2025-01-01",
                                    p_endday: str =  "2026-01-01",
                                    category_code: str = "200",
                                    p_itemcode: str = "211",
                                    product_cls_code: str = "01",
                                    ) -> dict:
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
