# VOGUE Market Research Agent

VOGUE의 시장성 조사 단계에서 사용할 수 있는 계약 중립적 Python 도메인 코어입니다. 현재 구현은 승인된 시장 자료원이 정해지지 않은 상태를 반영해 외부 API, 크롤러, 모델, 그래프 저장소를 포함하지 않습니다.

## 현재 동작

- `MarketResearchResult` 공통 응답 envelope와 Evidence 참조 모델을 제공합니다.
- 조사 출처는 `MarketResearchSource` 인터페이스로 연결하도록 분리했습니다.
- 출처가 설정되지 않으면 `no_results`와 사유를 반환합니다. 이를 시장값 0으로 표현하지 않습니다.
- 권한이 `authorized`로 확인되지 않은 Evidence와 이를 근거로 삼은 finding은 출력하지 않습니다.
- `fact` 유형 finding은 Evidence ID를 반드시 가져야 합니다. 참조가 누락되거나 유효하지 않으면 해당 finding을 제거하고 오류를 남깁니다.
- provider 결과가 부분적이어도 유효한 finding과 Evidence를 보존하고 `partial` 상태를 반환합니다.
- `product_concept`는 후속 교차 연결을 위한 선택 입력입니다. 시장 조사는 이 결과가 없어도 실행할 수 있습니다.

이 패키지는 요청·트렌드·재고 입력을 opaque dictionary로 전달합니다. 실제 공통 입력 스키마가 다른 저장소와 합의되면 해당 계약 어댑터를 추가해야 합니다. 이 저장소의 JSON Schema는 구현 초안이며, 다른 에이전트 저장소와 동기화된 wire contract로 간주하지 않습니다.

## 빠른 실행

Python 3.11 이상, 외부 패키지 없이 실행할 수 있습니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

## 사용 예시

```python
from market_research_agent import MarketResearchAgent, MarketResearchContext

context = MarketResearchContext(
    request_id="req-123",
    planning_request={"query": "2027SS 한국 여성 니트"},
    trend_result={"status": "ok", "findings": []},
    inventory_product_result={"status": "ok", "findings": []},
)

# 승인된 source adapter를 넘기기 전에는 안전하게 no_results를 반환합니다.
result = MarketResearchAgent().run(context)
print(result.to_dict())
```

새상품 조사 에이전트와의 병렬 실행은 상위 workflow가 각 에이전트를 독립 호출해 구현해야 합니다. 이 저장소는 transport나 orchestrator를 선택하지 않습니다. 초안 생성 단계는 `ProductConcept`와 `MarketResearchResult`를 함께 받은 뒤 결과별 상태와 Evidence를 보존해야 합니다.

## 미결정 사항

- 승인된 시장 조사 자료원, 라이선스·접근 권한, 조사 지표와 시간 범위
- 다섯 저장소에 공통으로 반영할 최종 `MarketResearchResult` 스키마·버전
- API/메시지 전달 방식, 인증, 저장·보존, retry/timeout과 배포 형태
- 시장 조사 품질 평가용 승인 데이터와 팀이 정할 합격 기준

현재 MVP 지침에 따라 판매량 분석은 구현하지 않으며 재고 스냅샷을 수요나 판매량의 대리 지표로 사용하지 않습니다. 출처·구현 경계의 검토 기록은 [`docs/notion-review.md`](docs/notion-review.md)에 있습니다.
