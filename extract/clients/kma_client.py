# clients/kma_client.py
from datetime import date
from urllib.parse import unquote

from extract.config import settings

from .base_client import BaseClient

# 한 요청당 최대 조회 행수. 실측상 999 가 상한이고 1200 은 오류다.
# 1년(365행)이 한 요청에 다 들어와 지점×연도 = 1요청이 된다.
_NUM_OF_ROWS = 999

# 정상 응답 코드 (response.header.resultCode)
_OK_CODE = "00"


class WeatherFetchError(RuntimeError):
    """ASOS 조회 실패 또는 전량 수집 미달.

    base_client._get() 이 오류를 삼키고 {} 를 반환하므로, 이를 걸러내지
    않으면 잘린 관측치가 정상처럼 흘러가 적재된다. MafraClient 의
    GridFetchError 와 같은 사상.
    """


class KmaClient(BaseClient):
    """기상청 지상(종관, ASOS) 일자료 수집 클라이언트.

    서비스키는 datago 와 같은 공공데이터포털 '인코딩키'라 unquote 로 한 번
    풀어야 requests 가 이중 인코딩하지 않는다(DatagoClient 와 동일).
    """

    def __init__(self, service_key: str = settings.KMA_SERVICE_KEY):
        super().__init__(timeout=30)
        self.service_key = unquote(service_key)   # 이중인코딩 회피

    def _get_page(self, stn_id: str, start: str, end: str, page: int) -> dict:
        """한 페이지 조회 후 body({totalCount, items:{item:[...]}})를 반환."""
        resp = self._get(
            settings.URL_KMA_ASOS,
            params={
                "serviceKey": self.service_key,
                "pageNo": page,
                "numOfRows": _NUM_OF_ROWS,
                "dataType": "JSON",
                "dataCd": "ASOS",
                "dateCd": "DAY",
                "startDt": start,
                "endDt": end,
                "stnIds": stn_id,
            },
        )
        node = (resp or {}).get("response", {})
        code = (node.get("header") or {}).get("resultCode")
        if code != _OK_CODE:
            msg = (node.get("header") or {}).get("resultMsg")
            raise WeatherFetchError(
                f"지점 {stn_id} {start}~{end} p{page} 조회 실패 "
                f"(code={code!r}, msg={msg!r})"
            )
        return node.get("body", {})

    def iter_daily(self, stn_id: str, start: date, end: date) -> list[dict]:
        """한 지점의 [start, end] 일자료 전량을 모아 반환.

        totalCount 를 첫 페이지에서 확정해 종료 조건 겸 검증에 쓴다.
        수집 행수가 totalCount 와 다르면 WeatherFetchError.
        """
        s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        rows: list[dict] = []
        total: int | None = None
        page = 1
        while True:
            body = self._get_page(stn_id, s, e, page)
            if total is None:
                total = body.get("totalCount", 0) or 0
            batch = (body.get("items") or {}).get("item", [])
            if isinstance(batch, dict):   # 원소 1개면 dict
                batch = [batch]
            rows.extend(batch)
            if not batch or len(rows) >= total:
                break
            page += 1

        if len(rows) != total:
            raise WeatherFetchError(
                f"지점 {stn_id} {s}~{e} 전량 수집 미달: {len(rows):,}/{total:,}행"
            )
        return rows
