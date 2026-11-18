# 다이어그램 근거

`docs/diagrams/src/`의 다이어그램에 있는 노드와 간선은 모두 코드 줄에 근거합니다. 각 행에는 그 줄의 위치와, 그 줄에 들어 있어야 하는 문자열이 있습니다.
`python scripts/check_diagram_claims.py`로 모든 행을 검사합니다. 코드를 고친 뒤에는 `--fix`를 붙여 줄 번호를 문자열의 새 위치로 옮깁니다.

## review-pipeline.dot

| 요소 | 근거 file:line | 확인 방법 |
|---|---|---|
| orchestrator가 리뷰 경로를 POST로 호출 | `src/fashion_planner_research/http_server.py:530` | 포함 `if unquote(urlsplit(self.path).path) != REVIEW_PATH:` |
| 1. Bearer 토큰 확인 | `src/fashion_planner_research/http_server.py:403` | 포함 `hmac.compare_digest(supplied.encode(), expected.encode())` |
| 1. JSON 본문 1 MiB 제한 | `src/fashion_planner_research/http_server.py:26` | 포함 `MAX_BODY_BYTES = 1_048_576` |
| 1. 계약 이름 확인 | `src/fashion_planner_research/http_server.py:561` | 포함 `if not isinstance(body, dict) or body.get("contract_version") != REVIEW_CONTRACT_VERSION:` |
| 1. 바깥과 input의 request_id 일치 | `src/fashion_planner_research/http_server.py:300` | 포함 `if payload.get("request_id") != request_id:` |
| 잘못된 요청 → 4xx 오류 응답 | `src/fashion_planner_research/http_server.py:278` | 포함 `def _review_error_payload(code: str, message: str, request_id: str | None = None)` |
| 2. 허용 상품 필드 | `src/fashion_planner_research/http_server.py:303` | 포함 `allowed_product_fields = {"category", "product_type", "gender", "season", "material"}` |
| 2. 보낸 필드마다 출처 하나 | `src/fashion_planner_research/http_server.py:317` | 포함 `set(field_evidence) != set(product)` |
| 분기: RESEARCH_REVIEW_SOURCE = synthetic | `src/fashion_planner_research/http_server.py:433` | 포함 `use_synthetic = review_source == synthetic.SYNTHETIC` |
| 합성 모드: 패키지 합성 표로 자료 묶음 | `src/fashion_planner_research/sources/synthetic_review_source.py:139` | 포함 `def synthetic_research_bundle(product: dict[str, Any]) -> dict[str, Any]:` |
| 합성 모드: 결과에 synthetic 표시 | `src/fashion_planner_research/sources/synthetic_review_source.py:29` | 포함 `SOURCE_TYPE = "synthetic_test_data"` |
| 4. 합성 모드는 고정 합성 환율 | `src/fashion_planner_research/http_server.py:448` | 포함 `fx = synthetic.synthetic_fx_reference() if use_synthetic else fetch_usd_krw_reference_rate()` |
| 분기: 합성 모드이거나 LLM 전송 허용 | `src/fashion_planner_research/http_server.py:466` | 포함 `elif target_audience_authorized:` |
| 5. 합성 모드는 고정 템플릿 | `src/fashion_planner_research/http_server.py:462` | 포함 `target_audience_proposal = synthetic.synthetic_target_audience(` |
| 분기: H&M 연구 허용 + 스냅샷 키 설정 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_combined_research_source.py:69` | 포함 `if research_enabled and aggregate_key:` |
| 예: 스냅샷 sha256 확인 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:559` | 포함 `if object_metadata.get("sha256") != actual_digest:` |
| 예: 스냅샷 스키마와 snapshot ID 확인 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:620` | 포함 `if _snapshot_identifier(value) != value["snapshot_id"]:` |
| 예: 매핑 지문 확인 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:565` | 포함 `if metadata.get("mapping_sha256") != mapping_digest:` |
| 예: 프로세스 단위 스냅샷 캐시 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:540` | 포함 `cache_key = (store.settings.bucket, self.object_key, mapping_digest)` |
| 아니오: 원단 자료원은 항상 실행 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_combined_research_source.py:85` | 포함 `tasks = {"fabric": read_fabric}` |
| 아니오: 연령·가격은 H&M 연구 허용 시에만 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_combined_research_source.py:87` | 포함 `tasks["age"] = read_age` |
| 원단: 크기 제한 GET | `src/fashion_planner_research/sources/market/fabric_price_r2_source.py:211` | 포함 `content, metadata = store.get_bytes_and_metadata(self.object_key, max_bytes=_MAX_BYTES)` |
| 원단: sha256 확인 | `src/fashion_planner_research/sources/market/fabric_price_r2_source.py:217` | 포함 `actual_hash = hashlib.sha256(content).hexdigest()` |
| 연령: DuckDB httpfs 연결 설정 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_customer_age_source.py:495` | 포함 `_configure_age_duckdb(conn)` |
| 연령: 10명 미만 칸 숨김 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_customer_age_source.py:40` | 포함 `_MIN_CELL_SIZE = 10` |
| 가격: DuckDB httpfs | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_price_source.py:177` | 포함 `conn.execute("LOAD httpfs")` |
| 가격: ×590 USD 연구 배율 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_price_source.py:290` | 포함 `"scale_factor": 590,` |
| 자료원 → Cloudflare R2, 읽기 전용 | `src/fashion_planner_research/sources/shared/r2_object_store.py:3` | 포함 `This module never loads dotenv files and never writes or deletes objects.` |
| 자료 묶음 | `src/fashion_planner_research/http_server.py:435` | 포함 `else HmCombinedResearchSource().analyze(product))` |
| 하나가 실패해도 나머지 자료원 유지 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_combined_research_source.py:100` | 포함 `for future in as_completed(futures):` |
| 4. H&M 연구 허용이고 가격 집계가 있을 때만 환율 조회 | `src/fashion_planner_research/http_server.py:445` | 포함 `if historical_price is not None:` |
| 4. Frankfurter 환율 API | `src/fashion_planner_research/domain/currency_conversion.py:17` | 포함 `FRANKFURTER_RATE_URL = "https://api.frankfurter.dev/v2/rate/usd/krw"` |
| 4. 연결 불가 → 날짜가 적힌 대체 환율 | `src/fashion_planner_research/domain/currency_conversion.py:89` | 포함 `def _configured_fallback_rate() -> dict[str, Any]:` |
| 5. 입력: 상품 필드, 연령 집계, 원단 목록 집계 | `src/fashion_planner_research/llm/target_audience.py:129` | 포함 `"historical_customer_aggregate": age_profile,` |
| 5. OpenAI Responses API | `src/fashion_planner_research/llm/responses_api.py:17` | 포함 `RESPONSES_URL = "https://api.openai.com/v1/responses"` |
| 아니오: not_authorized | `src/fashion_planner_research/http_server.py:496` | 포함 `"failed" if target_audience_error else "not_authorized"` |
| 치명적이지 않은 LLM 오류는 ok를 partial로 낮춤 | `src/fashion_planner_research/http_server.py:503` | 포함 `review["errors"].append({"code": "TARGET_AUDIENCE_LLM_FAILED", "message": target_audience_error})` |
| 6. 리뷰 조립 | `src/fashion_planner_research/http_server.py:479` | 포함 `review = build_sales_market_review(` |
| 6. 가격 참고값 계산 방법 | `src/fashion_planner_research/domain/sales_market_review.py:282` | 포함 `"method": "normalized source mean × 590 as USD; convert using dated USD/KRW reference rate; round suggested KRW price to nearest 1,000",` |
| 6. 업계 마진 시나리오 | `src/fashion_planner_research/domain/sales_market_review.py:551` | 포함 `margin = calculate_benchmark_margin_estimate(` |
| 6. 의사결정 지원 | `src/fashion_planner_research/domain/sales_market_review.py:246` | 포함 `"ready_for_human_decision": self.status in {"ok", "partial"},` |
| 7. 투영: 원천 행 제거, 문장 길이 제한 | `src/fashion_planner_research/http_server.py:223` | 포함 `for field, limit in (("proposal", 360), ("primary_segment", 360)):` |
| 7. 해석 한계 추가 | `src/fashion_planner_research/http_server.py:275` | 포함 `compact["limitations"] = list(dict.fromkeys(limitations))` |
| 7. 응답 검증 | `src/fashion_planner_research/http_server.py:509` | 포함 `_validate_review_result(review, request_id)` |
| 200 응답의 status: ok, partial, no_results | `src/fashion_planner_research/domain/sales_market_review.py:596` | 포함 `review_status = "no_results"` |

## snapshot-build.dot

| 요소 | 근거 file:line | 확인 방법 |
|---|---|---|
| H&M Kaggle 파일명이 기본 객체 키 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:66` | 포함 `("hm_transactions", "transactions_train.parquet"),` |
| 원단 CSV는 운영자가 준비 | `src/fashion_planner_research/sources/market/fabric_price_r2_source.py:1` | 포함 `"""Read and analyze the owner-provided public fabric listing snapshot in R2."""` |
| 빌더 진입점 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:835` | 포함 `parser = argparse.ArgumentParser(description="Build a privacy-safe H&M aggregate snapshot in R2.")` |
| 1. 비상업 연구 설정 필요 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:454` | 포함 `raise PermissionError("H&M snapshot generation is disabled by runtime settings.")` |
| 2. R2 자료원 매니페스트에서 객체 키 결정 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:62` | 포함 `_checked_key(r2_source_key(name, default=default), default)` |
| 3. 자료원마다 HEAD, 크기와 버전 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:467` | 포함 `if size <= 0 or size > _MAX_SOURCE_BYTES[logical_name] or not version:` |
| 4. 원단 CSV 최대 1 MB | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:43` | 포함 `_MAX_FABRIC_BYTES = 1_000_000` |
| 4. 원단 sha256을 R2 메타데이터와 대조 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:485` | 포함 `raise ValueError("The fabric listing source digest does not match its R2 metadata.")` |
| 4. 원단 CSV 열 구성 확인 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:498` | 포함 `fabric_bytes, "", version=fabric_version, sha256=fabric_digest, object_key=fabric_key,` |
| 5. 매핑 지문 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_customer_age_source.py:246` | 포함 `def _mapping_sha256(mapping: dict[str, Any]) -> str:` |
| 6. 연결을 나눈 쿼리 두 번 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:227` | 포함 `return {"age": age_statements, "price": (price_sql,)}` |
| 6. 연결마다 임시 디렉터리 지정 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_customer_age_source.py:70` | 포함 `conn.execute("SET temp_directory = " + _sql_string(temp_directory))` |
| 7. 다시 HEAD, 바뀌면 중단 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:509` | 포함 `raise ValueError("An H&M source changed while the snapshot was being prepared.")` |
| 8. 10명 미만 연령 칸 숨김 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_customer_age_source.py:278` | 포함 `small_cell_present = any(0 < count < _MIN_CELL_SIZE for count in counts.values())` |
| 8. 매핑·집계 SQL의 sha256 기록 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:514` | 포함 `aggregation_sql_sha256=aggregation_sql_sha256(),` |
| 8. snapshot_id는 내용 해시 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:387` | 포함 `snapshot["snapshot_id"] = _snapshot_identifier(snapshot)` |
| 9. 16 MB 제한 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:42` | 포함 `_MAX_SNAPSHOT_BYTES = 16_000_000` |
| 9. 키 = prefix/snapshot_id.json | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:403` | 포함 `return _checked_key(f"{prefix}/{snapshot_id}.json", "")` |
| --upload 없음 → 아무것도 쓰지 않음 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:843` | 포함 `print("prepared_only=true; no R2 object was written")` |
| 10. 업로드는 --upload와 업로드 설정이 모두 필요 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:845` | 포함 `if os.getenv("RESEARCH_AGGREGATE_UPLOAD_ENABLED", "").strip().casefold() not in {"1", "true", "yes"}:` |
| 10. If-None-Match로 put | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:429` | 포함 `IfNoneMatch="*",` |
| 같은 키에 다른 객체 → 거부 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:447` | 포함 `"A different object already exists at the immutable snapshot key.") from None` |
| 서비스가 설정된 키를 읽고 매핑 지문과 대조 | `src/fashion_planner_research/sources/sales/hm_kaggle/hm_research_aggregate.py:566` | 포함 `raise ValueError("Aggregate snapshot was built with a different category mapping.")` |
