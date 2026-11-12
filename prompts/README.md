# Project_07 prompt registry

이 폴더는 판매 분석가와 시장 분석가 프롬프트 원본을 각자 버전별로 관리합니다. 프롬프트 문장은 Python 코드에 넣지 않습니다.

| Prompt | Version | Status | Runtime | Purpose |
| --- | --- | --- | --- | --- |
| `sales_analysis` | [v1](sales_analysis/v1/) | proposal | 미연결 | 판매 관측 분석과 판매 source 경계 |
| `market_analysis` | [v1](market_analysis/v1/) | proposal | 미연결 | 승인된 시장 관측·시장 Evidence 분석 |
| `sales_market_analysis` | [v1](sales_market_analysis/v1/) | superseded proposal | 미연결 | 분리 전 통합 초안, 추적성 보존용 |

현재 Project_07에는 생성 모델과 프롬프트 실행 경로가 없습니다. `manifest.json`의 `runtime_active: false`는 이 사실을 기록합니다. 렌더링 파일은 검토용 산출물이며 활성 시스템 프롬프트가 아닙니다.

## 버전 폴더

- `system_template.md`: 시스템 프롬프트 원본
- `user_template.md`: 추후 모델 호출을 위해 제안하는 사용자 메시지 템플릿
- `examples.json`: 합성 입력과 기대 동작의 설명 예시. 자동으로 모델에 주입하지 않음
- `rendered_system.md`: system template을 복사해 생성한 검토용 렌더링 산출물

## 새 버전 추가

1. 해당 역할 폴더의 `v1/`을 새 버전 폴더로 복사합니다. 공개·활성화된 버전은 덮어쓰지 않고 새 버전을 만듭니다.
2. 새 버전의 system/user 템플릿, 예시, 검토 상태를 수정하고 `manifest.json`에 등록합니다.
3. `python3 scripts/render_market_analysis_prompt.py --prompt sales_analysis --version v2`처럼 prompt ID와 버전을 지정해 검토용 `rendered_system.md`를 생성합니다.
4. 공격·정상 혼합 fixture와 output 계약 호환성을 담당자가 검토합니다.
5. 실제 런타임 사용은 모델·보안·계약 담당자 승인 및 별도 구현 뒤에만 설정합니다. 이 저장소의 문서나 manifest만으로 런타임이 활성화되지는 않습니다.
