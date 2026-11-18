# 리뷰 계약(research-handoff)

research는 orchestrator가 보낸 상품 필드 몇 개를 받아, 설정된 과거 판매·고객 집계와 원단 판매 목록을 분석하고 리뷰를 바로 돌려줍니다. 응답은 근거, 빠진 자료, 오류, 해석 한계를 함께 담아, 받는 쪽이 관측값과 추정값과 가설을 구분할 수 있게 합니다.

## 경로

| 경로 | 설명 |
|---|---|
| `POST /v1/agents/research/reviews` | 리뷰 한 건. `Authorization: Bearer <토큰>`이 필요합니다. 요청·응답·오류 봉투의 `contract_version`은 모두 `research-handoff`입니다 |
| `GET /api/health` | 서버가 응답하는지만 알려 줍니다. 자료원이나 외부 API 접근은 확인하지 않습니다 |

## 스키마와 예시

| 대상 | 스키마 | 예시 |
|---|---|---|
| 요청 | [`research-review-request.schema.json`](schemas/research-review-request.schema.json) | [`research-review.request.json`](examples/research-review.request.json) |
| 성공 응답 | [`research-review-response.schema.json`](schemas/research-review-response.schema.json) | [`research-review.response.json`](examples/research-review.response.json) |
| 요청·서비스 오류 | [`research-review-error.schema.json`](schemas/research-review-error.schema.json) | |

스키마의 `$id`는 `https://contracts.fashion-planner.invalid/research/<파일명>`입니다. `.invalid`는 해석되지 않는 예약 도메인이라 식별자로만 씁니다. 응답·오류 스키마는 orchestrator 레포의 계약 사본과 바이트가 같습니다. 요청 스키마는 research가 받는 범위를 적은 것이라 `category`만 필수이고, orchestrator 사본은 보내는 쪽의 더 엄격한 규칙(다섯 필드 모두 보냄)을 적습니다.

예시 응답은 기본 설정(H&M 연구·LLM 전송 꺼짐, 저장소 없음)에서 예시 요청에 서버가 실제로 돌려준 `no_results` 응답입니다.

## 요청

- 바깥 봉투의 `request_id`와 `input.request_id`는 같아야 합니다.
- `input.product`: `category`(필수), `product_type`, `gender`(`women`, `men`, `unisex`, `unknown`), `season`, `material`(선택). 문자열은 120자 이하입니다. fashion-planner 흐름에서는 orchestrator가 다섯 필드를 모두 보내고, novelty 초안에 값이 없으면 대체값(`product_type`=카테고리, `gender`=`unknown`, `season`=초안 시즌 또는 `미정`, `material`=`혼방 소재`)을 `kind: proposal`로 넣습니다. `proposal`인 값은 확인되지 않은 제안입니다.
- `input.product_evidence`: 보낸 상품 필드마다 출처 하나. `kind`(`user_requested`, `evidence_observation`, `proposal`, `hypothesis`, `to_validate`, `unknown`), `source_ref`(예: `novelty.draft.product_profile.category`), `evidence_ids`(목록, 실제 근거 ID가 없으면 빈 목록)를 담습니다. 스키마는 `kind` 6가지를 허용하지만, fashion-planner 흐름에서 orchestrator가 넘기는 것은 `user_requested`, `evidence_observation`, `proposal` 세 가지입니다.
- 모르는 필드, 다른 계약 이름, 1 MiB를 넘는 본문은 거부합니다.

research는 기획 초안 전체, 제안가, 원가표, 이미지를 받지 않습니다. 그래서 응답은 초안 ID나 해시를 증명하지 않습니다.

## 성공 응답

- 봉투: `contract_version`, `request_id`(요청과 같음), `producer`(`agent_id: research`, `schema_version: 1.0.0`), `status`, `result`
- `result`에도 `request_id`, `producer_id`, `schema_version`, `status`가 다시 들어 있습니다. 봉투와 값이 다르면 받는 쪽은 응답을 거부해야 합니다.
- `status`
  - `ok`: 해당하는 자료원이 모두 근거를 냈습니다.
  - `partial`: 근거가 있지만 어떤 자료원이 비었거나 실패했습니다.
  - `no_results`: 상품에 맞는 근거가 없습니다. 업계 마진 기준만으로는 결과로 치지 않습니다.
- 빠진 자료는 `result.missing_data`에 이유(`source_not_configured`, `source_not_configured_or_disabled`, `no_match`, `source_error`)와 함께, 실패는 `result.errors`에 코드(예: `R2_FABRIC_PRICE_READ_FAILED`, `TARGET_AUDIENCE_LLM_FAILED`)와 함께 적습니다.
- `result.evidence`의 항목마다 `supports_fields`가 어떤 결과 필드를 뒷받침하는지 적고, 분석 행은 `evidence_ids`로 근거를 가리킵니다. 이 연결은 어떤 입력으로 계산했는지를 알려 줄 뿐, 업계 기준을 상품 관측값으로 바꾸지 않습니다.
- 응답은 받는 쪽이 쓰는 집계·요약만 담습니다. 원천 객체 목록과 원단 판매 목록의 개별 행은 빼고, 해석 한계를 `result.limitations`에 덧붙입니다.
- `RESEARCH_REVIEW_SOURCE=synthetic`이면 모든 자료원과 근거에 `synthetic_test_data`가 표시되고, `result.limitations`의 첫 항목이 합성 자료임을 알립니다.

필드별 설명은 [docs/architecture.md](../docs/architecture.md#응답)에 있습니다.

## 오류 응답

요청 검증 실패와 서비스 오류는 HTTP 상태 코드와 함께 `{"contract_version": "research-handoff", "request_id": …, "error": {"code": …, "message": …}}`를 돌려줍니다. 주요 코드는 `UNAUTHORIZED`(401), `UNSUPPORTED_MEDIA_TYPE`(415), `REQUEST_TOO_LARGE`(413), `INVALID_JSON`(400), `UNSUPPORTED_CONTRACT_VERSION`·`INVALID_PRODUCT`·`REQUEST_ID_MISMATCH`(422), `METHOD_NOT_ALLOWED`(405), `SERVICE_UNAVAILABLE`·`REVIEW_SOURCE_UNSUPPORTED`(503), `REVIEW_FAILED`(500)입니다.

## 계약을 바꿀 때

계약은 orchestrator와 함께 씁니다. 요청·응답 모양을 바꾸면 orchestrator 레포의 계약 사본과 테스트도 함께 고쳐야 합니다.
