#!/usr/bin/env bash
# claude_run.sh — 集計・バックテストのランナー（Claude.ai コンテナ／ローカル共通）
#
# GitHub の最新 main を取得して log/analyze.py と log/backtest.py を実行する。
# Claude.ai のコード実行コンテナは GitHub への通信が許可されているため、
# プロジェクトナレッジの Sync 状態と無関係に常に最新コミットで集計できる。
#
# 使い方:
#   bash tools/claude_run.sh              # analyze + backtest
#   bash tools/claude_run.sh --detail     # backtest にレース別内訳を渡す
#   KEIBA_LOCAL=1 bash tools/claude_run.sh  # 取得せず手元の作業コピーで実行
set -euo pipefail

REPO_TAR="https://codeload.github.com/atoyr/keiba/tar.gz/refs/heads/main"

if [ "${KEIBA_LOCAL:-0}" = "1" ]; then
  WORK="$(cd "$(dirname "$0")/.." && pwd)"
  echo "== ローカル作業コピーで実行: $WORK =="
else
  WORK="${TMPDIR:-/tmp}/keiba_latest"
  rm -rf "$WORK"; mkdir -p "$WORK"
  echo "== GitHub 最新 main を取得 =="
  curl -sL "$REPO_TAR" | tar xz -C "$WORK" --strip-components=1
  COMMIT_DATE=$(ls -ld "$WORK/log" | awk '{print $6, $7, $8}')
  echo "   取得完了 ($WORK)"
fi

echo
echo "================ analyze.py ================"
python3 "$WORK/log/analyze.py"

echo
echo "================ backtest.py ==============="
if [ -f "$WORK/log/backtest.py" ]; then
  python3 "$WORK/log/backtest.py" "$@"
else
  echo "[skip] log/backtest.py が main に未コミット。コミット後に再実行を。"
fi
