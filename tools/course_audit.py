#!/usr/bin/env python3
"""
course_audit.py — コース補正表の各節が「実測と出典つき」かを機械判定する（`#準備` の収録ギャップ表・`#予想` 工程0の入力）

  一覧   : python3 tools/course_audit.py
  照会   : python3 tools/course_audit.py --course 京都芝2200      # 該当節だけ
  機械用 : python3 tools/course_audit.py --json

判定（節ごと・2026-11-20〜）:
  OK        : (a) 出典の明記（出典／調査期間／JRA公式／GET 200／HTTP 200／keiba-course／うまめし／netkeiba）と
              (b) 脚質の実測（逃げ・先行・差し・追込・4角 のいずれかを含む行に【n-n-n-n】か n% の数値）の両方がある
  要改訂    : 節はあるが (a) か (b) が無い。理由を併記する（出典なし／脚質の実測なし）
  なし      : 該当コースの節が無い（--course 指定時のみ）

起点：エリザベス女王杯2026。補正表④（京都芝2200外）は 2026-06-23 の初期作成のまま出典も実測も無く、
「後方一気は届きにくい」の×0.85 が根拠なしで使われていた。従来のギャップ表は「節がある」だけで「済」にしていた。
キーワードによる判定なので、OK は「実測と出典の記述がある」ことまでしか保証しない（数値の正しさは見ない）。
"""
import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["references/コース別_脚質枠_補正表.md", "references/コース別_脚質枠_補正表_夏開催追記.md"]
HEAD_RE = re.compile(r"^## ([①-⑳㉑-㊿])\s*(.+)$")
SRC_RE = re.compile(r"出典|調査期間|JRA公式|GET 200|HTTP 200|keiba-course|うまめし|netkeiba")
STYLE_RE = re.compile(r"逃げ|先行|差し|追込|4角")
NUM_RE = re.compile(r"【\d+-\d+-\d+-\d+】|\d+(\.\d+)?%")


def norm(s):
    return re.sub(r"[\s（）()・m]", "", s).replace("メートル", "")


def sections():
    out = []
    for rel in FILES:
        path = os.path.join(BASE, rel)
        cur = None
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if line.startswith("## "):
                m = HEAD_RE.match(line.rstrip("\n"))
                cur = {"file": rel, "line": i, "no": m.group(1), "title": m.group(2).strip(),
                       "src": False, "measure": False} if m else None
                if cur:
                    out.append(cur)
                continue
            if cur is None:
                continue
            if SRC_RE.search(line):
                cur["src"] = True
            if STYLE_RE.search(line) and NUM_RE.search(line):
                cur["measure"] = True
    for s in out:
        why = []
        if not s["src"]:
            why.append("出典なし")
        if not s["measure"]:
            why.append("脚質の実測なし")
        s["status"] = "OK" if not why else "要改訂"
        s["reason"] = "・".join(why)
    return out


def match(s, course):
    t, c = norm(s["title"]), norm(course)
    # 「京都芝2200」→ 場・距離の両方が見出しに含まれれば一致（外回り等の付記は問わない）
    m = re.match(r"(\D+?)芝(\d+)", c)
    if not m:
        return c in t
    place, dist = m.group(1), m.group(2)
    return place in t and dist in t


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--course", help="例: 京都芝2200 / 東京芝1800")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    secs = sections()
    if a.course:
        secs = [s for s in secs if match(s, a.course)]
        if not secs:
            secs = [{"file": "", "line": 0, "no": "", "title": a.course, "status": "なし", "reason": "該当節なし"}]
    if a.json:
        print(json.dumps(secs, ensure_ascii=False, indent=1))
        return 0
    for s in secs:
        loc = f"{os.path.basename(s['file'])} L{s['line']}" if s["line"] else "-"
        print(f"{s['status']:4}  {s['no']} {s['title']}  [{loc}]" + (f"  … {s['reason']}" if s["reason"] else ""))
    n = sum(1 for s in secs if s["status"] != "OK")
    print(f"\n要改訂・なし {n}件 / {len(secs)}節")
    return 0


if __name__ == "__main__":
    sys.exit(main())
