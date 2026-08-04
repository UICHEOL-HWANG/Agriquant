# extract/__init__.py
"""농산물 원천 데이터 수집 계층.

KAMIS·datago·기상청·MAFRA·특일정보를 가져와 BigQuery 원천 테이블로 쌓는다.
쌓인 원천을 '날짜 × 품목'으로 접는 일은 transform 패키지가 맡는다.

여기서 하위 모듈을 re-export 하지 않는다. extract.config.settings 가
import 시점에 .env 를 읽으므로, 패키지를 건드리기만 해도 인증정보 로딩이
일어나는 걸 피하려는 것이다. 필요한 모듈은 각자 명시적으로 import 한다.
"""
