# transform/__init__.py
"""원천 테이블 → 분석용 뷰 계층.

extract 가 '가져와 쌓는' 곳이라면 여기는 '날짜 × 품목으로 접어 잇는' 곳이다.
"""
from .views import build_all

__all__ = ["build_all"]
