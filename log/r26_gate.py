#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R26 machine gate: prevent score-only exclusions in low-experience 2-year-old races.

Run from anywhere in the repository. Standard-library only.

Policy:
- R26 applies when race_name contains "2歳" or races.notes has ``R26対象=1``.
- An x/✕ horse requires predictions.notes marker ``R26消し根拠=<category>:<detail>``.
- category must be one of キャリア / 馬体重 / ローテ / その他構造.
- score/rank-based wording is rejected even when the marker exists.
- For a race whose date is today/future, violations are ERROR and exit 1.
- For a past race, violations are WARN only so a newly added gate does not rewrite history.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import re
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
RACES = os.path.join(BASE, "races.csv")
PREDICTIONS = os.path.join(BASE, "predictions.csv")

KESHI_MARKS = {"x", "X", "✕", "×"}
R26_MARKER = "R26消し根拠="
R26_RACE_MARKER = "R26対象=1"
ALLOWED_CATEGORIES = {"キャリア", "馬体重", "ローテ", "その他構造"}
FORBIDDEN_SCORE_WORDS = (
    "final_score", "final score", "最終点", "点差", "スコア", "score", "順位", "ランキング"
)
REASON_RE = re.compile(r"R26消し根拠=([^:：/／\s]+)\s*[:：]\s*([^/／]+)")


def load_csv(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def is_r26_race(race: dict[str, str]) -> bool:
    name = (race.get("race_name") or "").strip()
    notes = race.get("notes") or ""
    return "2歳" in name or R26_RACE_MARKER in notes


def parse_reason(notes: str) -> tuple[str, str] | None:
    m = REASON_RE.search(notes or "")
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def is_past(date_text: str, today: dt.date) -> bool:
    try:
        return dt.date.fromisoformat((date_text or "").strip()) < today
    except ValueError:
        return False


def validate(today: dt.date | None = None) -> tuple[list[str], list[str]]:
    today = today or dt.date.today()
    races = load_csv(RACES)
    predictions = load_csv(PREDICTIONS)
    preds_by_race: dict[str, list[dict[str, str]]] = defaultdict(list)
    for p in predictions:
        preds_by_race[(p.get("race_id") or "").strip()].append(p)

    errors: list[str] = []
    warns: list[str] = []
    for race in races:
        if not is_r26_race(race):
            continue
        rid = (race.get("race_id") or "").strip()
        historical = is_past(race.get("date") or "", today)
        for p in preds_by_race.get(rid, []):
            mark = (p.get("mark") or "").strip()
            if mark not in KESHI_MARKS:
                continue
            horse = f"#{p.get('horse_no')} {p.get('horse_name')}"
            notes = p.get("notes") or ""
            parsed = parse_reason(notes)
            problems: list[str] = []
            if parsed is None:
                problems.append(f"{R26_MARKER}<カテゴリ>:<詳細> がない")
            else:
                category, detail = parsed
                if category not in ALLOWED_CATEGORIES:
                    problems.append(
                        f"カテゴリ '{category}' は不可（{', '.join(sorted(ALLOWED_CATEGORIES))} のいずれか）"
                    )
                if not detail:
                    problems.append("構造フィルタの詳細が空")
                lowered = detail.lower()
                bad = [w for w in FORBIDDEN_SCORE_WORDS if w.lower() in lowered]
                if bad:
                    problems.append(
                        "点差・最終点・順位を消し根拠にしている: " + ", ".join(bad)
                    )
            if not problems:
                continue
            msg = (
                f"R26 [{rid} {horse}]: 2歳戦の消し(x)に構造フィルタ根拠が不足 — "
                + " / ".join(problems)
                + "。スコア順位だけでは消さず3着紐に保全する。"
            )
            (warns if historical else errors).append(msg)
    return errors, warns


def main() -> int:
    errors, warns = validate()
    for msg in warns:
        print(f"[WARN] {msg}")
    for msg in errors:
        print(f"[ERROR] {msg}")
    print(f"R26 gate: ERROR {len(errors)} / WARN {len(warns)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
