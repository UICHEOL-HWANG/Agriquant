# clients/kasi_client.py
from urllib.parse import unquote

from extract.config import settings

from .base_client import BaseClient

# 한 요청당 최대 조회 행수. 특일은 연간 최대 24행(24절기)이라 100 이면
# 한 해가 한 요청에 다 들어온다. 그래도 페이징은 돌려 totalCount 로 검증한다.
_NUM_OF_ROWS = 100

# 정상 응답 코드 (response.header.resultCode)
_OK_CODE = "00"

# 수집 대상 operation → dateKind 코드.
# getHoliDeInfo(국경일)는 getRestDeInfo 의 부분집합이라 뺐다(2026년 실측상
# 두 응답이 완전히 동일). getAnniversaryInfo(기념일)는 스승의날·소방의날 등
# 농산물 가격과 무관한 100여 건이라 노이즈여서 뺐다.
OPERATIONS: dict[str, str] = {
    "getRestDeInfo": "01",        # 공휴일(대체공휴일 포함)
    "get24DivisionsInfo": "03",   # 24절기
    "getSundryDayInfo": "04",     # 잡절(초복·말복·정월대보름 등)
}


class SpecialDayFetchError(RuntimeError):
    """특일정보 조회 실패 또는 전량 수집 미달.

    base_client._get() 이 오류를 삼키고 {} 를 반환하는 데다, 이 API 는
    실패 시 JSON 이 아니라 XML 을 뱉어 JSONDecodeError 로도 {} 가 된다.
    걸러내지 않으면 '그 해에 공휴일이 없다'로 조용히 둔갑한다.
    KmaClient 의 WeatherFetchError 와 같은 사상.
    """


class KasiClient(BaseClient):
    """한국천문연구원 특일정보(공휴일·24절기·잡절) 수집 클라이언트.

    서비스키는 datago 와 같은 공공데이터포털 '인코딩키'라 unquote 로 한 번
    풀어야 requests 가 이중 인코딩하지 않는다(DatagoClient 와 동일).

    `_type=json` 을 반드시 넘겨야 한다. 이 API 의 기본 응답은 XML 이라
    빼먹으면 BaseClient._get() 이 JSONDecodeError 로 {} 를 돌려준다.
    """

    def __init__(self, service_key: str = settings.KASI_SERVICE_KEY):
        super().__init__(timeout=30)
        self.service_key = unquote(service_key)   # 이중인코딩 회피

    def _get_page(self, operation: str, year: int, page: int) -> dict:
        """한 페이지 조회 후 body({totalCount, items:{item:[...]}})를 반환."""
        resp = self._get(
            f"{settings.URL_KASI_SPCDE_BASE}/{operation}",
            params={
                "serviceKey": self.service_key,
                "solYear": year,
                "numOfRows": _NUM_OF_ROWS,
                "pageNo": page,
                "_type": "json",
            },
        )
        node = (resp or {}).get("response", {})
        code = (node.get("header") or {}).get("resultCode")
        if code != _OK_CODE:
            msg = (node.get("header") or {}).get("resultMsg")
            raise SpecialDayFetchError(
                f"{operation} {year} p{page} 조회 실패 "
                f"(code={code!r}, msg={msg!r})"
            )
        return node.get("body", {})

    def fetch_year(self, operation: str, year: int) -> list[dict]:
        """한 operation 의 한 해 전량을 모아 반환.

        totalCount 를 첫 페이지에서 확정해 종료 조건 겸 검증에 쓴다.
        수집 행수가 totalCount 와 다르면 SpecialDayFetchError.

        totalCount 가 0 이면 빈 리스트를 돌려준다. 아직 고시되지 않은
        미래 연도(2029~)가 정상적으로 0 이라 오류로 볼 수 없다.
        """
        rows: list[dict] = []
        total: int | None = None
        page = 1
        while True:
            body = self._get_page(operation, year, page)
            if total is None:
                total = body.get("totalCount", 0) or 0
                if total == 0:
                    return []
            batch = (body.get("items") or {}).get("item", [])
            if isinstance(batch, dict):   # 원소 1개면 dict
                batch = [batch]
            rows.extend(batch)
            if not batch or len(rows) >= total:
                break
            page += 1

        if len(rows) != total:
            raise SpecialDayFetchError(
                f"{operation} {year} 전량 수집 미달: {len(rows):,}/{total:,}행"
            )
        return rows

    def iter_special_days(self, year: int) -> list[dict]:
        """한 해의 공휴일·24절기·잡절을 전부 모아 반환.

        응답에 dateKind 가 들어있어 operation 을 따로 붙일 필요는 없다.
        """
        rows: list[dict] = []
        for op in OPERATIONS:
            rows.extend(self.fetch_year(op, year))
        return rows
