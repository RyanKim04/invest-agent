#!/usr/bin/env bash
# Re-render every diagram: edit a .mmd file, then run ./docs/diagrams/render.sh
set -euo pipefail
cd "$(dirname "$0")"
for f in *.mmd; do
  npx -y @mermaid-js/mermaid-cli@11.4.2 -i "$f" -o "${f%.mmd}.png" -w 1600 -s 2 -b white -q
  echo "rendered ${f%.mmd}.png"
done
