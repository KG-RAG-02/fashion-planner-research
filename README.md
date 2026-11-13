# VOGUE Sales Market Review Agent (Project_07)

Project_07은 P06가 전달한 최소 상품 정보를 자료원과 대조해 시장 참고자료를 만들고, 검토 제안(`SalesMarketReview`)으로 반환합니다. 아래는 이 프로젝트의 핵심 흐름인 **입력 → 분석 → 출력**입니다.

## 1. 입력

호출자는 `POST /v1/agents/sales-market-agent/reviews`에 `vogue-sales-market-review/0.2-proposal` 요청을 보냅니다. Bearer 인증이 필요합니다.

필수 상품 필드는 `category` 하나입니다. `product_type`, `gender`, `season`, `material`은 선택 필드입니다. `gender`는 `women`, `men`, `unisex`, `unknown` 중 하나를 사용합니다. envelope와 `input`의 `request_id` 값은 같아야 합니다.

```json
{
  "contract_version": "vogue-sales-market-review/0.2-proposal",
  "request_id": "review-001",
  "input": {
    "request_id": "review-001",
    "product": {
      "category": "Outerwear",
      "product_type": "Jacket",
      "gender": "women",
      "season": "winter",
      "material": "wool blend"
    }
  }
}
```

P06 bridge는 초안에서 이 상품 필드만 추려 전달합니다. P07은 `PlanningDraft` 전체, 상품 원가표, 제안 가격·통화, 이미지, P06 evidence metadata를 받지 않습니다. 알 수 없는 필드는 요청 오류로 처리합니다.

전체 예시는 [요청 JSON](contracts/examples/sales-market-review-0.2-proposal.request.json), 필드 제약은 [요청 schema](contracts/schemas/proposals/sales-market-review-0.2-proposal.schema.json)에 있습니다.

## 2. 분석 흐름

요청이 검증되면 P07은 전달된 상품 정보를 각 설정 자료원에 조회합니다. 자료원은 독립적으로 실행되므로 한 자료원에 결과가 없거나 오류가 있어도 다른 자료원의 결과는 검토에 포함될 수 있습니다.

1. **원단 listing 참고값** — 상품의 `material`과 매칭되는 R2 공개 listing snapshot을 찾습니다. 결과는 가격 참고값이며 검증된 조달 원가가 아닙니다.
2. **H&M 과거 가격 통계** — `VOGUE_HM_RESEARCH_NONCOMMERCIAL=true`일 때 category, product type, gender를 사용해 Kaggle 거래 자료를 집계합니다. 원천에 시즌 정보가 없으므로 `season`은 이 집계에 적용되지 않습니다.
3. **고객 연령대 집계** — 같은 비상업 연구 설정에서 카테고리·gender 기준의 연령대 통계를 조회합니다. 개별 고객 행을 반환하지 않습니다.
4. **환산 및 시나리오 계산** — H&M 가격 자료가 있으면 구현상의 ×590 USD 해석과 USD/KRW 기준환율로 참고 원화값을 계산합니다. 그 가격과 업계 총마진 benchmark를 사용해 마진 참고 시나리오를 만듭니다. 원가 입력이 없으므로 상품별 실제 마진은 계산하지 않습니다.
5. **선택적 타겟 가설** — `VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED=true`일 때에만 허용된 상품 정보와 요약 근거를 LLM에 보내 텍스트 가설을 생성합니다.
6. **결과 조립** — 근거, 누락 자료, 오류, 제한사항과 검토 필요 영역을 `SalesMarketReview`로 묶습니다. 자료가 없으면 0으로 채우지 않고 누락 상태로 기록합니다.

H&M 자료는 과거 비상업 연구용이며 현재 시장 가격이나 VOGUE 판매 실적이 아닙니다. 원천 통화도 공식 확인된 값이 아니므로 ×590 해석과 환산값은 가정에 기반한 참고치입니다. 원단 공개 listing은 실제 조달 원가로 간주하지 않습니다.

## 3. 출력

검토가 완료되면 동기 방식으로 HTTP `200`과 JSON을 반환합니다. 응답의 `request_id`로 요청을 식별하고, `status`는 `ok`, `partial`, `no_results`, `error` 중 하나입니다. 자료원이 비활성화됐거나 일부 근거가 없으면 보통 `partial` 또는 `no_results`와 함께 `missing_data` 및 `limitations`가 채워집니다.

주요 결과 필드는 다음과 같습니다.

| 필드 | 내용 |
| --- | --- |
| `category_price_comparison` | H&M 과거 가격 집계와 기준 정보 |
| `material_cost_analysis` | 원단 listing 관측값 |
| `customer_segment_analysis` | 사용 가능한 경우 고객 연령대 집계 |
| `target_audience_analysis` | 연령 근거 및 선택적 LLM 가설 |
| `margin_estimate`, `margin_price_range_analysis` | benchmark 기반 참고 시나리오 |
| `evidence` | 결과에 사용된 자료의 출처 정보 |
| `missing_data`, `errors`, `limitations` | 조회하지 못한 자료, 오류 및 해석 제약 |
| `decision_support` | 근거 영역별 충족 여부와 사람 검토 필요 표시 |

P07은 상품 초안을 수정하거나 채택 여부를 결정하지 않습니다. 응답은 의사결정 보조 자료이며 최종 판단은 담당자가 합니다. 성공 응답 형식은 [응답 schema](contracts/schemas/proposals/sales-market-review-0.2-proposal-response.schema.json), 오류 응답 형식은 [오류 schema](contracts/schemas/proposals/sales-market-review-0.2-error.schema.json)를 참고하세요.

## 로컬 실행 및 호출

Python 3.11 이상에서 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
export VOGUE_AGENT_SERVICE_BEARER_TOKEN='<token>'
PYTHONPATH=src python3 -m market_research_agent.http_server --host 127.0.0.1 --port 8766
```

다른 터미널에서 예시 요청을 전송합니다.

```bash
curl http://127.0.0.1:8766/v1/agents/sales-market-agent/reviews \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  --data-binary @contracts/examples/sales-market-review-0.2-proposal.request.json
```

로컬 API는 `127.0.0.1` 또는 `localhost`에서만 실행됩니다. `GET /api/health`는 인증 없이 서버 응답 여부를 확인합니다. 자료원 연결을 확인하려면 실제 리뷰 요청 결과를 확인하세요. `.env`의 허용된 설정을 읽지 않으려면 `VOGUE_PROJECT07_DISABLE_DOTENV=1`을 지정합니다.

P06/P07 통합 상태와 재현 절차는 [통합 확인 문서](docs/integration/p06-p07-review-loopback.md), 자료원 설계 및 추가 제약은 [시장 검토 설계](docs/design/post-planning-sales-market-review.md)와 [고객 연령대·타겟 분석 설계](docs/design/customer-age-target-analysis.md)를 참고하세요.

## 저장소 구조

| 경로 | 역할 |
| --- | --- |
| `src/market_research_agent/http_server.py` | HTTP 입력 검증, 자료원 호출, 응답 반환 |
| `src/market_research_agent/sources/` | 판매·시장 자료원 adapter |
| `src/market_research_agent/domain/` | 결과 모델과 review 조립 |
| `contracts/` | 요청·응답 schema와 JSON 예시 |
| `docs/` | 설계 및 통합 참고 문서 |
| `tests/` | 자동화 테스트 |
