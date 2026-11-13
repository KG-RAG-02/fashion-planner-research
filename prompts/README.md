# Project_07 prompt registry

이 폴더는 판매·시장 분석용 검토 프롬프트와 P07 리뷰에서 사용하는 타겟 가설 프롬프트를 버전별로 관리합니다. 프롬프트 본문은 Python 코드에 넣지 않습니다.

| Prompt | Version | Status | Runtime | Purpose |
| --- | --- | --- | --- | --- |
| `sales_analysis` | [v1](sales_analysis/v1/) | proposal | 미연결 | 판매 관측 분석과 판매 source 경계 |
| `market_analysis` | [v1](market_analysis/v1/) | proposal | 미연결 | 승인된 시장 관측·시장 Evidence 분석 |
| `sales_market_analysis` | [v1](sales_market_analysis/v1/) | superseded proposal | 미연결 | 분리 전 통합 초안, 추적성 보존용 |
| `target_audience_hypothesis` | [v1](target_audience_hypothesis/v1/) | runtime active | 상품 텍스트 LLM 전송 허가 시 opt-in | 패션 상품기획용 타깃 프로필, 가격 포지셔닝 가설, 머천다이징·검증안 |

현재 `/reviews` 경로에서 실행되는 프롬프트는 `target_audience_hypothesis` v1뿐입니다. P06 bridge가 보낸 허용 상품 필드와 집계 근거를 사용하며 `VOGUE_TARGET_LLM_TRANSFER_AUTHORIZED=true`일 때에만 실행됩니다. 이미지는 입력 계약에 없고, 이미지 스타일 프롬프트는 제거했습니다. 나머지 판매/시장 분석 프롬프트는 제안 상태이며 HTTP 경로와 연결되어 있지 않습니다.

## 버전 폴더

- `system_template.md`: 시스템 프롬프트 원본
- `user_template.md`: 추후 모델 호출을 위해 제안하는 사용자 메시지 템플릿
- `examples.json`: 합성 입력과 기대 동작의 설명 예시. 자동으로 모델에 주입하지 않음
- `rendered_system.md`: system template을 복사해 생성한 검토용 렌더링 산출물
- `target_audience_hypothesis/v1/rendered_system.md`: 패션 머천다이징 타깃 분석 system prompt. 관측된 집계, 상품 적합성 가설, 가격 위치, 실행안과 검증 계획을 구분합니다.

## 새 버전 추가

1. 해당 역할 폴더의 `v1/`을 새 버전 폴더로 복사합니다. 공개·활성화된 버전은 덮어쓰지 않고 새 버전을 만듭니다.
2. 새 버전의 system/user 템플릿, 예시, 검토 상태를 수정하고 `manifest.json`에 등록합니다.
3. `python3 scripts/render_market_analysis_prompt.py --prompt sales_analysis --version v2`처럼 prompt ID와 버전을 지정해 검토용 `rendered_system.md`를 생성합니다.
4. 공격·정상 혼합 fixture와 output 계약 호환성을 담당자가 검토합니다.
5. 실제 런타임 사용은 모델·보안·계약 담당자 승인 및 별도 구현 뒤에만 설정합니다. 이 저장소의 문서나 manifest만으로 런타임이 활성화되지는 않습니다.
