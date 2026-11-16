# VOGUE Sales Market Review Agent (Project_07)

Project_07(P07)은 상품 기획 이후 시장 참고자료를 모아 검토 결과를 반환하는 HTTP 서비스입니다. P06는 Draft를 검증하고 반환하며, P08은 P06 bridge가 만든 허용 필드 projection을 검증한 뒤 P07 `/reviews`를 직접 호출합니다. P06와 P07 사이의 직접 호출은 없습니다. P08은 Draft 전체를 P07에 보내지 않으며, P07 응답은 Draft ID나 hash를 증명하지 않습니다. 선택 자료원에 문제가 생겨도 얻은 결과와 오류·한계를 함께 보존합니다.

## 입력 → 분석 → 출력

### 1. 입력

- **요청 경로:** `POST /v1/agents/sales-market-agent/reviews`
- **인증:** `Authorization: Bearer <token>`
- **요청 계약:** `vogue-sales-market-review/0.3-proposal`

`input.product.category`는 필수입니다. `product_type`, `gender`, `season`, `material`은 선택 항목입니다. 바깥 요청과 `input`의 `request_id`는 같아야 합니다.

현재 요청 형식과 필드별 `product_evidence` 예시는 [요청 예시](contracts/examples/sales-market-review-0.3-proposal.request.json)와 [요청 schema](contracts/schemas/proposals/sales-market-review-0.3-proposal.schema.json)를 참고하세요. 제공하는 각 상품 필드마다 `kind`, `source_ref`, `evidence_ids`를 보냅니다. 사용자 요청에서 온 값은 `user_requested`이며, P06의 실제 근거 ID가 없으면 `evidence_ids`는 빈 배열입니다. P07은 전체 기획 초안, 제안가, 원가표, 이미지 참조를 받지 않습니다.

### 2. 분석

1. **입력 검증 및 전달 필드 제한** — 알 수 없는 필드나 서로 다른 `request_id`를 거부하고 허용된 상품 정보만 자료원에 전달합니다.
2. **시장 자료 조회** — 원단 listing의 category, composition, construction 항목에서 `material`과 일치하는 기록을 찾습니다. 요청 소재에서 섬유 혼용률이나 원가를 추정하지 않습니다. H&M 자료 사용 설정은 P07 코드 기본값에서 비상업 연구로 켜져 있습니다.
   - `VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY`가 최신 통합 스냅샷을 가리키면, P07 프로세스가 처음 사용할 때 스냅샷 객체를 한 번 읽습니다. 스냅샷에는 H&M 연령·거래가격 집계와 크기가 제한된 원단 listing CSV가 있습니다. 스냅샷 생성은 연령과 가격 집계를 별도 DuckDB 쿼리 및 연결로 실행해 집계 메모리를 분리합니다. 가격의 p25·중앙값·p75는 T-Digest 근사 통계이며, 최소·최대·평균·행 수는 정확한 집계입니다. 실행 중에는 검증한 객체를 프로세스 메모리에서 재사용합니다.
   - 스냅샷 생성기는 H&M 고객 CSV, 상품 Parquet, 거래 Parquet를 읽어 DuckDB 집계 쿼리 한 번을 실행하고, 원단 listing 객체도 검증한 뒤 변경 불가한 새 JSON 객체를 `derived/research-aggregate-snapshots/` 아래에 게시합니다. 고객 원본 행과 ID는 스냅샷에 저장하지 않습니다. 연령 구간 중 하나라도 10명 미만이면 해당 프로필의 연령별 수치·비율, 전체 분모, 타깃 연령대 수치를 모두 숨깁니다. 원단 listing은 한 번의 스냅샷 조회로 세 분석을 제공하기 위해 포함하며, 제공 데이터의 재사용 조건은 별도 제한사항입니다.
   - 통합 스냅샷 키가 설정되면 성공·실패와 관계없이 요청은 그 객체만 읽습니다. 스냅샷을 읽지 못하면 포함된 세 자료원을 모두 `error`로 표시하며, 원단이나 H&M 원본 객체를 따로 조회하지 않습니다. 키가 없을 때만 독립 자료원 모드를 사용합니다.
3. **가격 참고값 계산** — H&M 정규화 가격에 ×590 USD 해석을 적용하고, 값이 있으면 Frankfurter USD/KRW 기준환율로 원화 참고값을 계산합니다. 이는 제안가나 검증된 현재 시장 가격이 아닙니다. 업계 마진 기준은 시나리오 참고값이며 해당 상품의 실제 마진이 아닙니다.
4. **선택적 타깃 가설 생성** — 코드 기본 설정은 LLM 전송을 허용합니다. 이 설정이 켜지면 허용된 상품 정보와 요약 근거로 LLM 호출을 시도합니다. `LLM_API_KEY`가 없거나 호출에 실패하면 `TARGET_AUDIENCE_LLM_FAILED`를 기록하고 가능한 다른 검토 결과는 보존합니다. 생성 결과는 고객 관측 사실이 아니라 가설입니다. 설정 위치는 [`runtime_settings.py`](src/market_research_agent/runtime_settings.py)입니다.
5. **응답 조립** — 성공한 분석, 출처 근거, 누락 사유, 오류, 해석 한계와 사람의 검토 필요 여부를 함께 반환합니다. 없는 값을 0이나 임의의 가격으로 채우지 않습니다.

### 3. 출력

응답은 동기 방식입니다. 완료된 검토의 바깥 `status`와 `result.status`는 같은 값(`ok`, `partial`, `no_results`)을 가집니다. `request_id`도 바깥 응답과 결과에 보존됩니다. 요청 검증·서비스 실패는 `error` 객체를 포함한 HTTP 오류 응답입니다.

P07은 P08 화면·기획·SWOT이 사용하는 집계 통계, 가격/소재/마진 요약, 타깃 가설, 근거 참조 ID, 누락·오류·제한만 `/reviews` 응답에 포함합니다. 거래·고객 원본 파일 목록과 원단 개별 listing 행은 전달하지 않습니다. 응답 버전과 필수 최상위 필드 이름은 유지하며, `target_audience`가 상세 가설의 소비 필드이고 `target_audience_analysis`는 상태·연령 요약 alias입니다. 근거 레코드는 출처·기간·권한·지원 필드와 해석만 전달하고, 집계값은 해당 분석 요약에 한 번만 둡니다.

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
python -m market_research_agent.http_server
```

로컬 실행은 기본적으로 `127.0.0.1:8766`에만 bind합니다. `--host 127.0.0.1 --port 8766`으로 명시할 수도 있습니다. `/reviews` 요청에는 Bearer token이 필요합니다.

다른 터미널에서 요청 예시를 보냅니다.

```bash
curl http://127.0.0.1:8766/v1/agents/sales-market-agent/reviews \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  --data-binary @contracts/examples/sales-market-review-0.3-proposal.request.json
```

`GET /api/health`는 서버 응답 여부만 확인하며 R2, DuckDB, 환율, LLM 연결은 확인하지 않습니다. 로컬 서버는 `.env`의 허용된 설정을 읽습니다. 로더를 끄려면 `VOGUE_PROJECT07_DISABLE_DOTENV=1`을 설정하세요.

### Docker 서비스 실행

Docker 기본 명령은 HTTP 서버를 `0.0.0.0:8766`에 bind합니다. 비 loopback 서비스 모드는 `VOGUE_AGENT_SERVICE_BEARER_TOKEN`이 없으면 시작하지 않습니다. 저장소 루트의 `.env`에 해당 값을 설정한 뒤 실행하세요.

```bash
docker compose up --build -d sales-market-review-agent
curl http://127.0.0.1:8766/api/health
```

Compose는 호스트의 `8766` 포트를 컨테이너의 `8766`으로 전달하고 같은 health 경로를 주기적으로 확인합니다. 응답의 `mode`는 Docker에서 `container-service`, 로컬 실행에서 `local-development`입니다. health 응답은 liveness만 나타냅니다. P08이 별도 Compose 프로젝트라면 P07 서비스 URL과 공유 네트워크 연결은 P08 배포 설정에서 지정해야 합니다.

P08 호출 흐름은 **P06 Draft → P08의 P06 bridge projection 검증 → P07 `POST /v1/agents/sales-market-agent/reviews`**입니다. 전송되는 상품 필드는 `category`(필수), `product_type`, `gender`, `season`, `material`(선택)와 현재 계약에서 요구하는 해당 필드의 provenance입니다. P07은 P06를 호출하지 않고 전체 Draft도 받지 않습니다.

### 환경 설정 경계

`.env`는 향후 P06과 통합하기 쉽도록 공용 provider 자격 증명과 프로젝트별 설정을 구분합니다. `LLM_API_KEY`, `LLM_MODEL`, `R2_ACCOUNT_ID`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`는 공용 설정 후보입니다. P07 서비스 인증과 집계 스냅샷 키는 `.env`에 둡니다. H&M 비상업 연구 모드, 타깃 정보 LLM 전송 승인, 날짜가 있는 환율 fallback 기본값은 P07의 [`runtime_settings.py`](src/market_research_agent/runtime_settings.py)에 둡니다. H&M 연구와 타깃 정보 전송 승인은 코드 기본값이 켜져 있으며 프로세스 환경변수로 덮어쓸 수 있습니다. P06과 합칠 때 공용 값은 한 번만 정의하고 agent별 동작 설정은 프로젝트별로 유지하면 충돌을 줄일 수 있습니다.

운영 요청 경로는 `VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY`가 가리키는 **단일 R2 객체**에서 H&M 연령·가격 집계와 원단 listing을 함께 읽습니다. cold process에서 객체 GET은 한 번이며, 검증한 snapshot을 프로세스 메모리에서 재사용합니다. 이 키가 설정됐지만 객체를 읽지 못하면 개별 원천 객체로 fallback하지 않고 포함 자료원 전체를 오류로 반환합니다. 원천 manifest는 스냅샷 작성·개발용 독립 자료원 모드에서만 사용합니다. 기본 Cloudflare R2 endpoint는 `R2_ACCOUNT_ID`로 유도되므로 `R2_ENDPOINT`는 생략할 수 있습니다. 필요한 변수 목록과 P06 공용 설정 경계는 [`.env.example`](.env.example)에 정리했습니다.

`VOGUE_DUCKDB_EXTENSION_DIRECTORY`는 R2 접속 설정이 아니라 로컬 DuckDB 확장 파일 위치입니다. 통합 스냅샷을 읽는 서버 요청에는 필요하지 않고, Docker 이미지가 호환되는 확장 경로를 자체 설정합니다. 로컬에서 스냅샷을 직접 생성할 때만 환경에 따라 일회성 지정이 필요할 수 있습니다.

## R2 통합 스냅샷

스냅샷 생성에는 H&M 원본 객체와 원단 listing 객체의 읽기 권한, 파생 경로에 새 변경 불가 객체를 추가할 권한이 필요합니다. 원본 객체는 덮어쓰지 않습니다.

스냅샷 작성기는 저장소의 `configs/r2-source-manifest.v1.json`에서 원천 객체 키를 읽습니다. 원천 객체가 다른 prefix에 있으면 manifest를 수정한 뒤 이미지를 다시 빌드하거나, 로컬 작성 명령에 `PROJECT07_R2_SOURCE_MANIFEST`를 직접 설정하세요. Compose의 리뷰 서비스는 manifest를 mount하지 않으며, 구성된 통합 스냅샷 객체만 읽습니다.

```bash
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

## P08 연동 계약 및 제공 경로

최신 `KG-RAG-02/fashion-planner-orchestrator` main (`da129ec`, 2026-11-16 기준)은 P07에 `POST /v1/agents/sales-market-agent/reviews`와 `vogue-sales-market-review/0.3-proposal`만 호출합니다. P08은 P06 Draft에서 허용된 상품 필드와 필드별 출처를 구성해 전달합니다. 이 저장소의 HTTP 서버도 해당 0.3 리뷰 경로만 제공합니다. P07은 구형 `/runs`, `/structured-runs`, Full-Draft artifact replay 계약을 패키징하거나 제공하지 않습니다. 이 저장소의 예시·테스트도 현재 0.3 요청 및 `sales-market-review/0.1-internal` 응답 계약을 기준으로 합니다.

요청·응답 계약, 예시, 상태 해석은 [`contracts/README.md`](contracts/README.md)와 [현재 P08 → P07 흐름](docs/design/post-planning-sales-market-review.md)을 기준으로 확인하세요. P06/P08 공유 계약을 변경하지 않습니다.

로컬 실행은 `python -m market_research_agent.http_server` 또는 `vogue-sales-market-review-http` 명령을 사용합니다. Docker/Compose 배포는 위 기본 실행 절차를 따릅니다. API는 상태 저장 replay artifact를 만들지 않으므로 리뷰 artifact 디렉터리나 upstream artifact mount가 필요하지 않습니다.

`python scripts/run_tests.py`는 저장소의 전체 단위 검증을 실행합니다. Dockerfile의 test profile도 이 명령을 사용합니다.
