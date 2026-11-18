"""docs/diagrams/CLAIMS.md의 다이어그램 근거가 지금 코드와 맞는지 검사합니다.

각 행은 파일, 줄 번호, 그 줄에 들어 있어야 하는 문자열을 적습니다. 코드를 고친 뒤
--fix를 주면, 문자열이 파일에 한 번만 나올 때 줄 번호를 새 위치로 고칩니다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "docs" / "diagrams" / "CLAIMS.md"
ROW = re.compile(r"`(?P<path>[^`:]+):(?P<line>\d+)` \| 포함 `(?P<text>[^`]+)`")


def main() -> int:
    parser = argparse.ArgumentParser(description="다이어그램 근거(CLAIMS.md)를 코드와 대조합니다.")
    parser.add_argument("--fix", action="store_true", help="옮겨진 줄 번호를 고쳐 씁니다")
    args = parser.parse_args()
    document = CLAIMS.read_text(encoding="utf-8")
    problems: list[str] = []
    checked = 0

    def check(match: re.Match[str]) -> str:
        nonlocal checked
        checked += 1
        path, line, text = match["path"], int(match["line"]), match["text"]
        source = ROOT / path
        if not source.is_file():
            problems.append(f"{path}: 파일이 없습니다")
            return match[0]
        lines = source.read_text(encoding="utf-8").splitlines()
        if 1 <= line <= len(lines) and text in lines[line - 1]:
            return match[0]
        found = [number for number, value in enumerate(lines, start=1) if text in value]
        if args.fix and len(found) == 1:
            return match[0].replace(f"`{path}:{line}`", f"`{path}:{found[0]}`")
        problems.append(f"{path}:{line}: {text!r}가 없습니다(발견한 줄: {found or '없음'})")
        return match[0]

    updated = ROW.sub(check, document)
    if args.fix and updated != document:
        CLAIMS.write_text(updated, encoding="utf-8")
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"근거 {checked}개 검사, 문제 {len(problems)}개")
    return 1 if problems or checked == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
