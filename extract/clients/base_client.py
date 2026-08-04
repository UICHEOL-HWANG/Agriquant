# clients/base_client.py
import logging
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

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

        오류 로그에는 인증키를 가린 URL·메시지만 남긴다(_mask).
        """
        safe_url = _mask(url)
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()  # 4xx, 5xx 에러 발생 시 예외 발생
            return response.json()
        except requests.exceptions.Timeout:
            logging.error(f"[Timeout Error] 요청 시간 초과: {safe_url}")
            return {}
        except requests.exceptions.HTTPError as e:
            logging.error(
                f"[HTTP Error] 상태 코드 {response.status_code}: {safe_url} - {_mask(str(e))}"
            )
            return {}
        except requests.exceptions.JSONDecodeError:
            logging.error(f"[JSON Error] 응답 결과를 JSON으로 파싱할 수 없음: {safe_url}")
            return {}
        except Exception as e:
            logging.error(f"[Unknown Error] 알 수 없는 오류 발생 ({safe_url}): {_mask(str(e))}")
            return {}
