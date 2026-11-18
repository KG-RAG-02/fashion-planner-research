"""패키지와 함께 설치되는 설정·프롬프트 파일의 위치.

소스 체크아웃, 휠 설치, 컨테이너 이미지 모두 같은 파일을 읽도록 패키지 안의
resources/만 쓴다.
"""
from __future__ import annotations

from pathlib import Path

RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
CONFIGS_DIR = RESOURCES_DIR / "configs"
PROMPTS_DIR = RESOURCES_DIR / "prompts"
