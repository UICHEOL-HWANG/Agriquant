# clients/mafra_client.py
from extract.config import settings

from .base_client import BaseClient

# Grid OpenAPI ID
GRID_CONTRACT_CROP = "Grid_20260128000000000685_1"   # 농협계약재배 물량·도매가격
# 거래단량 매핑은 어떤 파이프라인에도 배선하지 않는다. PUM_CODE(6자리)가 KAMIS
# item_code(3자리)와 체계가 달라 조인 키가 없고, (STEP_CODE,PUM_CODE)가 유일키도
# 아니며(1766조합 중 879개 중복), 단위 대부분이 이미 kg 라 parse_unit() 대비
# 얻는 게 없다. 탐색용으로 get_unit_mapping() 만 남겨둔다.
GRID_UNIT_MAPPING = "Grid_20220826000000000645_1"    # 농수축산물 거래단량 매핑

# 한 요청당 최대 조회 행수 (start~end 인덱스 범위 폭)
_CHUNK = 1000

# 정상 응답 코드 (result.code)
_OK_CODE = "INFO-000"


class GridFetchError(RuntimeError):
    """그리드 조회 실패 또는 전량 수집 미달.

    base_client._get() 이 HTTP/JSON 오류를 삼키고 {} 를 반환하므로, 이를
    걸러내지 않으면 잘린 rows 가 정상처럼 흘러가 replace(WRITE_TRUNCATE)
    적재로 기존 데이터를 덮어쓴다. 적재 전에 터뜨려 테이블을 보호한다.
    """


class MafraClient(BaseClient):
    """농림축산식품부 공공데이터포털 Grid OpenAPI 수집 클라이언트.

    파라미터를 쿼리스트링이 아니라 경로(path)에 박는다:
        {base}/{API_KEY}/{TYPE}/{GRID_ID}/{START_INDEX}/{END_INDEX}
    응답은 {GRID_ID: {totalCnt, startRow, endRow, result, row:[...]}} 형태.
    """

    def __init__(self, api_key: str = settings.MAFRA_API_KEY):
        super().__init__()
        self.api_key = api_key

    def _build_grid_url(self, grid_id: str, start_idx: int, end_idx: int) -> str:
        return f"{settings.URL_MAFRA_BASE}/{self.api_key}/json/{grid_id}/{start_idx}/{end_idx}"

    def _get_grid(self, grid_id: str, start_idx: int, end_idx: int) -> dict:
        """한 페이지 조회 후 grid 노드({totalCnt,row,...})를 반환.

        result.code 가 INFO-000 이 아니면(= 통신 실패로 {} 를 받았거나 API가
        오류코드를 준 경우) GridFetchError.
        """
        resp = self._get(self._build_grid_url(grid_id, start_idx, end_idx))
        node = (resp or {}).get(grid_id, {})
        code = (node.get("result") or {}).get("code")
        if code != _OK_CODE:
            raise GridFetchError(
                f"{grid_id} {start_idx}~{end_idx} 조회 실패 (code={code!r})"
            )
        return node

    def iter_grid(self, grid_id: str, chunk: int = _CHUNK) -> list[dict]:
        """그리드 전량(row)을 페이지네이션으로 모아 반환.

        totalCnt 까지 start/end 인덱스를 옮겨가며 row 를 이어붙인다.
        row 가 단일 dict 로 올 경우도 리스트로 정규화한다.

        totalCnt 는 첫 페이지 값을 기준으로 삼고, 최종 수집 행수가 이와
        다르면 GridFetchError. 중간 페이지가 조용히 비는 경우를 잡는다.
        """
        rows: list[dict] = []
        start = 1
        total: int | None = None
        while True:
            node = self._get_grid(grid_id, start, start + chunk - 1)
            if total is None:
                total = node.get("totalCnt", 0) or 0
            batch = node.get("row", [])
            if isinstance(batch, dict):   # 원소 1개면 dict
                batch = [batch]
            rows.extend(batch)
            if not batch or len(rows) >= total:
                break
            start += chunk

        if len(rows) != total:
            raise GridFetchError(
                f"{grid_id} 전량 수집 미달: {len(rows):,}/{total:,}행"
            )
        return rows

    # 6. 농협계약재배 물량과 도매가격정보 (전량)
    def iter_contract_crop_price(self) -> list[dict]:
        return self.iter_grid(GRID_CONTRACT_CROP)

    # 6. 농협계약재배 물량과 도매가격정보 (단일 페이지)
    def get_contract_crop_price(self, start_idx: int = 1, end_idx: int = 100) -> dict:
        url = self._build_grid_url(GRID_CONTRACT_CROP, start_idx, end_idx)
        return self._get(url)

    # 7. 농수축산물 거래단량 매핑 정보조회 (단일 페이지)
    def get_unit_mapping(self, start_idx: int = 1, end_idx: int = 5) -> dict:
        url = self._build_grid_url(GRID_UNIT_MAPPING, start_idx, end_idx)
        return self._get(url)
