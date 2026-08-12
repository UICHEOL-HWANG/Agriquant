# model/__init__.py
"""7거래일 뒤 가격 방향 예측.

`extract` 가 '가져와 쌓는' 곳, `transform` 이 '날짜 x 품목으로 접는' 곳이라면
여기는 **'접은 것으로 방향을 맞히고 그 성적을 재는' 곳**이다.

노트북 01~30 의 결론을 코드로 옮긴 것이라, 상수를 바꾸려면 근거 노트북을
먼저 읽는다(`model/config.py` 의 각 값에 번호가 달려 있다). 바꾼 뒤에는
반드시 재현 검사를 돌린다:

    python -m model.evaluate --check     # 15·19번 수치 재현
    python -m model.predict              # 오늘 신호

**예측하는 것은 방향이지 값이 아니다.** 변화율(몇 % 오를까)은 08·10번에서
못 맞힌다고 결론났다. 여기서 나오는 확률을 가격 예측처럼 쓰면 안 된다.

이름을 처음 쓸 때 해당 모듈을 불러온다(PEP 562). 이렇게 하지 않으면
`python -m model.evaluate` 가 모듈을 두 번 적재해 RuntimeWarning 을 내고,
그 경고가 운영 로그에 매일 쌓인다.
"""
from __future__ import annotations

_EXPORTS = {
    "load_mart": "model.data",
    "refresh_cache": "model.data",
    "build_features": "model.features",
    "FEATURES": "model.features",
    "walk_forward": "model.evaluate",
    "noise_floor": "model.evaluate",
    "item_groups": "model.evaluate",
    "run": "model.evaluate",
    "report": "model.evaluate",
    "score": "model.metrics",
    "score_at_coverage": "model.metrics",
    "by_item": "model.metrics",
    "predict_latest": "model.predict",
    "train_final": "model.predict",
    "save_predictions": "model.store",
    "build_score_view": "model.store",
    "model_version": "model.store",
    "run_checks": "model.monitor",
    "score_so_far": "model.monitor",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module 'model' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(_EXPORTS[name]), name)


def __dir__() -> list[str]:
    return sorted(__all__)
