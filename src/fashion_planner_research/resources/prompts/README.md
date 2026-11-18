# 타깃 고객 가설 프롬프트

`target_audience_hypothesis/`는 리뷰의 선택 기능인 타깃 고객 가설에만 쓰입니다. 시장 수치 계산에는 쓰지 않습니다.

- `system.md`: `llm/target_audience.py`가 읽는 시스템 프롬프트
- `user_template.md`: 사람이 읽기 위한 입력 모양. 실제 입력 JSON은 코드가 허용된 상품 필드, 연령 집계, 원단 목록 집계만으로 만듭니다.
- `examples.json`: 동작을 설명하는 합성 예시. 모델 호출에는 넣지 않습니다.

LLM 전송이 허용되고 API 키가 있을 때만 호출합니다. 출력은 `llm/target_audience.py`의 `_OUTPUT_FIELDS`와 필드별 검사로 확인하므로, 프롬프트의 JSON 지시를 바꿀 때는 그쪽도 함께 맞춥니다. 결과는 관측 사실이 아니라 가설입니다.
