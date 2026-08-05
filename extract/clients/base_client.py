# clients/base_client.py
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ── 재시도 정책 ──────────────────────────────────────────────
# 일시적 오류는 재시도한다. 백필은 요청이 1만 건 단위라 그중 몇 건이 그냥
# 실패하면 그날치가 통째로 빈 채 남는데, 상위 로직은 빈 응답을 '0행'으로
# 읽어 넘어가므로(iter_shipment 의 totalCount=0) 로그만 봐서는 진짜 휴장인지
# 수집 실패인지 구분할 수 없다. 조용한 구멍을 만들지 않으려는 장치다.
_MAX_ATTEMPTS = 3           # 최초 시도 포함
_BACKOFF_SEC = 1.0          # 1s → 2s (지수 백오프)

# 재시도할 상태코드. 4xx 는 요청 자체가 틀린 것이라 다시 보내도 같으므로
# 제외하고, 429(호출 제한)와 5xx(서버 일시 장애)만 다시 시도한다.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# 로그에 값을 남기면 안 되는 쿼리 파라미터 (소문자로 비교)
_SECRET_PARAMS = {"p_cert_key", "p_cert_id", "servicekey", "apikey", "api_key"}

# 값 자체를 치환할 때의 최소 길이. 'test'/'sample' 같은 짧은 기본값이
# 무관한 문자열까지 뭉개는 걸 막는다.
_MIN_SECRET_LEN = 8


class _LegacyTLSAdapter(HTTPAdapter):
    """레거시 TLS 서버 접속용 어댑터.

    KAMIS 서버는 TLSv1.2 / ECDHE-RSA-AES256-SHA(CBC+SHA1)만 제시하는데,
    urllib3 2.x + OpenSSL 3.x 의 기본 보안수준(SECLEVEL=2)이 이 암호군을
    거부해 'sslv3 alert handshake failure' 로 끊긴다. curl·openssl 로는
    되는데 파이썬에서만 실패하는 이유가 이것이다.

    SECLEVEL 을 1로 낮춰 암호군만 허용하고, 인증서 검증은 그대로 켜 둔다.
    """

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = create_urllib3_context(ciphers="DEFAULT:@SECLEVEL=1")
        return super().init_poolmanager(*args, **kwargs)


def _secret_values() -> tuple[str, ...]:
    """설정에 들어있는 실제 인증키 값들.

    MAFRA 는 키를 쿼리가 아니라 경로에 박기 때문에 파라미터명만으로는
    못 가린다. 그래서 값 자체로도 한 번 더 치환한다.
    """
    from extract.config import settings

    candidates = (
        settings.KAMIS_CERT_KEY,
        settings.KAMIS_CERT_ID,
        settings.DATAGO_SERVICE_KEY,
        settings.MAFRA_API_KEY,
    )
    return tuple(v for v in candidates if v and len(v) >= _MIN_SECRET_LEN)


def _mask(text: str) -> str:
    """로그용 문자열. 인증키를 *** 로 가린다.

    쿼리 파라미터명(KAMIS·datago)과 값 자체(MAFRA 경로) 양쪽을 처리한다.
    예외 메시지에도 URL 전문이 실려 오므로 그쪽에도 같이 적용한다.
    """
    parts = urlsplit(text)
    if parts.query:
        pairs = [
            (k, "***" if k.lower() in _SECRET_PARAMS else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        # safe='*' 를 줘야 마스크가 %2A%2A%2A 로 인코딩되지 않는다(로그 가독성).
        text = urlunsplit(parts._replace(query=urlencode(pairs, safe="*")))

    for secret in _secret_values():
        text = text.replace(secret, "***")
    return text


class BaseClient:
    """공통 HTTP 통신 및 JSON 변환 클라이언트"""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.mount("https://", _LegacyTLSAdapter())

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """안전한 HTTP GET 요청 실행 메서드.

        일시적 오류(타임아웃·연결 끊김·429·5xx·JSON 파싱 실패)는 백오프를
        두고 _MAX_ATTEMPTS 까지 다시 시도한다. JSON 파싱 실패까지 재시도에
        넣은 이유는 data.go.kr 이 과부하일 때 JSON 대신 XML 오류 페이지를
        내려주기 때문이다 — 형식은 깨졌지만 원인은 일시적 서버 상태다.

        모두 소진하면 종전대로 {} 를 반환한다. 상위 클라이언트들
        (mafra/kma/kasi)이 '빈 dict = 실패'로 보고 걸러내는 계약이라
        여기서 예외를 올리면 그쪽 처리가 어긋난다.

        오류 로그에는 인증키를 가린 URL·메시지만 남긴다(_mask).
        """
        safe_url = _mask(url)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            # 마지막 시도면 더 기다릴 것 없이 실패로 확정한다.
            last = attempt == _MAX_ATTEMPTS

            def retry_or_fail(label: str, detail: str) -> Optional[Dict[str, Any]]:
                """재시도 여지가 있으면 대기 후 None, 없으면 오류 로그 후 {}."""
                if last:
                    logging.error(f"[{label}] {detail} (시도 {attempt}/{_MAX_ATTEMPTS}, 포기)")
                    return {}
                wait = _BACKOFF_SEC * 2 ** (attempt - 1)
                logging.warning(
                    f"[{label}] {detail} (시도 {attempt}/{_MAX_ATTEMPTS}, {wait:.0f}s 후 재시도)"
                )
                time.sleep(wait)
                return None

            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()  # 4xx, 5xx 에러 발생 시 예외 발생
                return response.json()
            except requests.exceptions.Timeout:
                result = retry_or_fail("Timeout Error", f"요청 시간 초과: {safe_url}")
            except requests.exceptions.ConnectionError as e:
                result = retry_or_fail(
                    "Connection Error", f"연결 실패: {safe_url} - {_mask(str(e))}"
                )
            except requests.exceptions.HTTPError as e:
                status = response.status_code
                detail = f"상태 코드 {status}: {safe_url} - {_mask(str(e))}"
                if status in _RETRY_STATUS:
                    result = retry_or_fail("HTTP Error", detail)
                else:
                    logging.error(f"[HTTP Error] {detail}")   # 4xx 는 재시도해도 같다
                    return {}
            except requests.exceptions.JSONDecodeError:
                result = retry_or_fail(
                    "JSON Error", f"응답 결과를 JSON으로 파싱할 수 없음: {safe_url}"
                )
            except Exception as e:
                logging.error(f"[Unknown Error] 알 수 없는 오류 발생 ({safe_url}): {_mask(str(e))}")
                return {}

            if result is not None:   # 재시도 소진 → {}
                return result

        return {}   # 도달하지 않지만 반환 타입을 명확히 한다
