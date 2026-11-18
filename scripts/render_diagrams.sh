#!/usr/bin/env bash
# docs/diagrams/src/*.dot에서 다이어그램을 모두 다시 만듭니다.
# 원본 <name>.dot마다 <name>.svg, <name>-dark.svg, <name>.png, <name>-dark.png를 만듭니다.
# Graphviz(dot), librsvg(rsvg-convert), Pretendard 글꼴이 필요합니다.
set -euo pipefail
cd "$(dirname "$0")/.."

src_dir=docs/diagrams/src
out_dir=docs/diagrams

light=(BG='#ffffff' FG='#1f2328' SVC_FILL='#eef2ff' SVC_LINE='#4f46e5' EXT='#6b7280' ERR='#b91c1c')
dark=(BG='#0d1117' FG='#e6edf3' SVC_FILL='#1e1b4b' SVC_LINE='#818cf8' EXT='#6b7280' ERR='#f87171')

render() {  # render <file.dot> <suffix> <TOKEN=value>...
  local dot_file=$1 suffix=$2
  shift 2
  local name
  name=$(basename "$dot_file" .dot)
  local args=()
  for pair in "$@"; do
    args+=(-e "s|@${pair%%=*}@|${pair#*=}|g")
  done
  sed "${args[@]}" "$dot_file" | dot -Tsvg -o "$out_dir/$name$suffix.svg"
  if grep -q '@[A-Z_]*@' "$out_dir/$name$suffix.svg"; then
    echo "바뀌지 않은 색 토큰이 있습니다: $out_dir/$name$suffix.svg" >&2
    exit 1
  fi
  rsvg-convert -w 1600 "$out_dir/$name$suffix.svg" -o "$out_dir/$name$suffix.png"
}

for dot_file in "$src_dir"/*.dot; do
  render "$dot_file" "" "${light[@]}"
  render "$dot_file" "-dark" "${dark[@]}"
done
echo "SVG $(find "$out_dir" -maxdepth 1 -name '*.svg' | wc -l | tr -d ' ')개를 ${out_dir}에 만들었습니다"
