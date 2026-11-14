# VOGUE Sales Market Review Agent (Project_07)

Project_07(P07)은 상품 기획 이후, P06가 전달한 최소 상품 정보를 바탕으로 시장 참고자료를 모아 검토 결과를 반환합니다. 선택 자료원에 문제가 생겨도 얻은 결과와 오류·한계를 함께 보존합니다.

## 입력 → 분석 → 출력

### 1. 입력

- **요청 경로:** `POST /v1/agents/sales-market-agent/reviews`
- **인증:** `Authorization: Bearer <token>`
- **요청 계약:** `vogue-sales-market-review/0.3-proposal`

`input.product.category`는 필수입니다. `product_type`, `gender`, `season`, `material`은 선택 항목입니다. 바깥 요청과 `input`의 `request_id`는 같아야 합니다.

현재 요청 형식과 필드별 `product_evidence` 예시는 [요청 예시](contracts/examples/sales-market-review-0.3-proposal.request.json)와 [요청 schema](contracts/schemas/proposals/sales-market-review-0.3-proposal.schema.json)를 참고하세요. 제공하는 각 상품 필드마다 `kind`, `source_ref`, `evidence_ids`를 보냅니다. 사용자 요청에서 온 값은 `user_requested`이며, P06의 실제 근거 ID가 없으면 `evidence_ids`는 빈 배열입니다. P07은 전체 기획 초안, 제안가, 원가표, 이미지 참조를 받지 않습니다.

### 2. 분석

1. **입력 검증 및 전달 필드 제한** — 알 수 없는 필드나 서로 다른 `request_id`를 거부하고 허용된 상품 정보만 자료원에 전달합니다.
2. **시장 자료 조회** — 원단 listing의 category, composition, construction 항목에서 `material`과 일치하는 기록을 찾습니다. 요청 소재에서 섬유 혼용률이나 원가를 추정하지 않습니다. H&M 자료는 `VOGUE_HM_RESEARCH_NONCOMMERCIAL=true`일 때만 사용합니다.
   - `VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY`가 최신 통합 스냅샷을 가리키면, P07 프로세스가 처음 사용할 때 스냅샷 객체를 한 번 읽습니다. 스냅샷에는 H&M 연령·거래가격 집계와 크기가 제한된 원단 listing CSV가 있습니다. 스냅샷 생성 시 연령과 거래가격은 DuckDB의 한 집계 쿼리로 계산되며, 실행 중에는 검증한 객체를 프로세스 메모리에서 재사용합니다.
   - 스냅샷 생성기는 H&M 고객 CSV, 상품 Parquet, 거래 Parquet를 읽어 DuckDB 집계 쿼리 한 번을 실행하고, 원단 listing 객체도 검증한 뒤 변경 불가한 새 JSON 객체를 `derived/research-aggregate-snapshots/` 아래에 게시합니다. 고객 원본 행과 ID는 스냅샷에 저장하지 않습니다. 연령 구간 중 하나라도 10명 미만이면 해당 프로필의 연령별 수치·비율, 전체 분모, 타깃 연령대 수치를 모두 숨깁니다. 원단 listing은 한 번의 스냅샷 조회로 세 분석을 제공하기 위해 포함하며, 제공 데이터의 재사용 조건은 별도 제한사항입니다.
   - 통합 스냅샷 키가 설정되지 않으면 연령, 거래가격, 원단 자료원을 각각 조회합니다. 스냅샷을 읽지 못하면 H&M 분석은 실패로 표시하고 원단 자료원은 별도로 시도해 성공 결과를 보존합니다.
3. **가격 참고값 계산** — H&M 정규화 가격에 ×590 USD 해석을 적용하고, 값이 있으면 Frankfurter USD/KRW 기준환율로 원화 참고값을 계산합니다. 이는 제안가나 검증된 현재 시장 가격이 아닙니다. 업계 마진 기준은 시나리오 참고값이며 해당 상품의 실제 마진이 아닙니다.
4. **선택적 타깃 가설 생성** — `VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED=true`일 때만 허용된 상품 정보와 요약 근거를 텍스트 LLM에 전달합니다. 결과는 고객 사실이 아니라 가설입니다.
5. **응답 조립** — 성공한 분석, 출처 근거, 누락 사유, 오류, 해석 한계와 사람의 검토 필요 여부를 함께 반환합니다. 없는 값을 0이나 임의의 가격으로 채우지 않습니다.

### 3. 출력

응답은 동기 방식입니다. 완료된 검토의 바깥 `status`와 `result.status`는 같은 값(`ok`, `partial`, `no_results`)을 가집니다. `request_id`도 바깥 응답과 결과에 보존됩니다. 요청 검증·서비스 실패는 `error` 객체를 포함한 HTTP 오류 응답입니다.

| 응답 경로 | 내용 |
| --- | --- |
| `result.category_price_comparison` | H&M 과거 거래가격 참고 통계 |
| `result.material_cost_analysis` | 공개 원단 listing 참고값. 검증된 조달 원가가 아님 |
| `result.customer_segment_analysis` | 개인정보 행이 아닌 과거 연령대 집계 |
| `result.target_audience_analysis` | 연령 근거와 선택적 텍스트 가설 |
| `result.evidence` | 분석에 사용한 자료의 출처 |
| `result.missing_data` | 자료원 미설정, 일치 결과 없음, 자료 조회 불가 사유 |
| `result.errors` | 치명적이지 않은 자료원 또는 선택 LLM 오류 |
| `result.limitations` | 자료 범위와 해석상 제약 |
| `result.margin_estimate`, `result.margin_price_range_analysis` | 업계 기준 시나리오. 상품의 실제 마진이 아님 |
| `result.decision_support` | 근거 영역별 충족 여부와 담당자 검토 필요 표시 |

- `ok`: 적용되는 자료원을 조회했고 결과를 반환한 상태입니다. 연구 자료의 해석상 한계는 `limitations`에 별도로 남습니다.
- `partial`: 일부 검토 근거는 있지만 적용 자료원의 실패·미설정·일치 결과 없음이 남은 상태입니다. 해석상 한계는 `limitations`에 별도로 기록합니다. P08은 채워진 분석 필드를 표시하고 관련 오류도 함께 보존해야 합니다.
- `no_results`: 상품별 검토 근거가 없습니다. 원천 오류는 `errors`, 조회 사유는 `missing_data`에 남길 수 있습니다. 일반 업계 기준만으로 시장 결과가 있다고 표시하지 않습니다.

원단 가격이 없는 경우 `result.missing_data[]`에서 `field=fabric_costs`인 항목을 확인하세요. `reason`은 `source_not_configured`, `no_match`, `source_error` 등을 구분합니다. 조회 오류는 `result.errors[]`에 `R2_FABRIC_PRICE_READ_FAILED`로도 나타납니다. 전체 형식은 [응답 schema](contracts/schemas/proposals/sales-market-review-0.3-proposal-response.schema.json)를, 예시는 [`no_results` 응답](contracts/examples/sales-market-review-0.3-proposal.response.json)을 참고하세요.

## 로컬 실행

Python 3.11 이상이 필요합니다. R2 자료 조회나 스냅샷 생성을 사용한다면 선택 의존성도 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[r2]'
export VOGUE_AGENT_SERVICE_BEARER_TOKEN='<token>'
python -m market_research_agent.http_server --host 127.0.0.1 --port 8766
```

다른 터미널에서 요청 예시를 보냅니다.

```bash
curl http://127.0.0.1:8766/v1/agents/sales-market-agent/reviews \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  --data-binary @contracts/examples/sales-market-review-0.3-proposal.request.json
```

`GET /api/health`는 서버 응답 여부만 확인하며 R2, DuckDB, 환율, LLM 연결은 확인하지 않습니다. 로컬 서버는 `.env`의 허용된 설정을 읽습니다. 로더를 끄려면 `VOGUE_PROJECT07_DISABLE_DOTENV=1`을 설정하세요.

## R2 통합 스냅샷

스냅샷 생성에는 H&M 원본 객체와 원단 listing 객체의 읽기 권한, 파생 경로에 새 변경 불가 객체를 추가할 권한이 필요합니다. 원본 객체는 덮어쓰지 않습니다.

Compose는 `configs/r2-source-manifest.v1.json`을 컨테이너에 읽기 전용으로 전달합니다. 버킷에서 객체가 namespace 아래에 있으면 호스트의 manifest 파일을 수정하거나 별도 파일을 준비한 뒤 `PROJECT07_R2_SOURCE_MANIFEST_FILE=/path/to/manifest.json`으로 지정하세요. 컨테이너 안에서는 고정 경로 `/app/configs/r2-source-manifest.runtime.json`으로 읽습니다. 설정하지 않으면 저장소 기본 manifest의 객체 키(`customers.csv`, `articles.parquet`, `transactions_train.parquet`)를 사용합니다.

```bash
VOGUE_HM_RESEARCH_NONCOMMERCIAL=true \
VOGUE_HM_AGGREGATE_UPLOAD_ENABLED=true \
python -m market_research_agent.sources.sales.hm_kaggle.hm_research_aggregate --upload
```

출력된 경로를 `VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY`에 설정하고 P07을 재시작하세요. 원본 버전이나 category mapping이 바뀌면 다른 객체 경로가 만들어집니다. `--upload`를 생략하면 로컬 집계까지만 수행하고 R2에는 쓰지 않습니다. API 요청 경로는 읽기 전용입니다.

## 저장소 구조

- `src/market_research_agent/http_server.py`: 요청 검증, 자료원 조정, 응답 반환
- `src/market_research_agent/domain/`: 검토 결과, 근거·상태 규칙, 원가 시나리오, 환산
- `src/market_research_agent/sources/`: R2, 원단 listing, H&M 연구 자료원
- `src/market_research_agent/llm/`: 선택적 타깃 가설 호출과 결과 검증
- `prompts/target_audience_hypothesis/`: 버전 관리되는 타깃 가설 prompt
- `configs/`: 자료원 객체 manifest와 카테고리 mapping
- `contracts/`: 현재 요청·응답 schema 및 예시
- `tests/`: API, 자료원, 계약 및 합성 fixture 검증 코드

요청·응답 구현의 소유자는 P07입니다. 소비 프로젝트와 공유하는 계약 변경은 합의가 필요합니다. [저장소 경계](docs/repository-boundaries.md)와 [P08 → P07 현재 검토 흐름](docs/design/post-planning-sales-market-review.md)을 참고하세요.
