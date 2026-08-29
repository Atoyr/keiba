#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import shutil
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        fail(f"missing required file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def find_race(races: list[dict[str, str]], race_id: str) -> dict[str, str]:
    for row in races:
        if row.get("race_id") == race_id:
            return row
    fail(f"race_id={race_id} is not present in log/races.csv")


def ensure_prediction_rows(predictions: list[dict[str, str]], race_id: str) -> None:
    if not any(r.get("race_id") == race_id for r in predictions):
        fail(f"race_id={race_id} has no rows in log/predictions.csv")


def validate_board(source: Path, race_id: str) -> None:
    if not source.exists() or not source.is_file():
        fail(f"board source does not exist: {source}")
    if source.suffix.lower() not in {".html", ".htm"}:
        fail("board source must be an HTML file")
    text = source.read_text(encoding="utf-8")
    if race_id not in text:
        fail("board HTML must contain the same race_id as log/*.csv")
    if "<html" not in text.lower():
        fail("board source does not look like a complete HTML document")


def published_race_ids(docs_dir: Path) -> list[str]:
    base = docs_dir / "predictions"
    if not base.exists():
        return []
    return sorted(
        child.name
        for child in base.iterdir()
        if child.is_dir() and (child / "index.html").exists()
    )


def render_index(repo_name: str, races: list[dict[str, str]], published_ids: list[str]) -> str:
    by_id = {r.get("race_id", ""): r for r in races}
    rows = [by_id[rid] for rid in published_ids if rid in by_id]
    rows.sort(key=lambda r: (r.get("date", ""), r.get("race_id", "")), reverse=True)

    cards = []
    for r in rows:
        rid = html.escape(r.get("race_id", ""))
        date = html.escape(r.get("date", ""))
        name = html.escape(r.get("race_name", rid))
        grade = html.escape(r.get("grade", ""))
        course = html.escape(r.get("course", ""))
        meta = " · ".join(x for x in (grade, course) if x)
        cards.append(
            '<a class="race" href="./predictions/{rid}/">'
            '<time>{date}</time><strong>{name}</strong><span>{meta}</span></a>'.format(
                rid=rid, date=date, name=name, meta=meta
            )
        )

    body = "\n".join(cards) if cards else '<p class="empty">公開済みの予想ボードはまだありません。</p>'

    return '''<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{repo_name} 競馬予想ボード">
  <title>競馬予想ボード</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #09110d; color: #edf7f0; }}
    main {{ width: min(900px, calc(100% - 32px)); margin: 0 auto; padding: 48px 0 72px; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(28px, 6vw, 48px); }}
    .lead {{ margin: 0 0 32px; color: #a9b9ae; }}
    .list {{ display: grid; gap: 12px; }}
    .race {{ display: grid; grid-template-columns: 110px 1fr auto; gap: 12px 20px; align-items: center; padding: 18px 20px; border: 1px solid #294034; border-radius: 14px; background: #101d16; color: inherit; text-decoration: none; }}
    .race:hover {{ border-color: #4b7c5d; transform: translateY(-1px); }}
    .race time, .race span {{ color: #9fb1a5; font-size: 14px; }}
    .race strong {{ font-size: 18px; }}
    .empty {{ padding: 24px; border: 1px dashed #294034; border-radius: 14px; color: #9fb1a5; }}
    footer {{ margin-top: 36px; color: #7f9185; font-size: 12px; }}
    @media (max-width: 640px) {{ .race {{ grid-template-columns: 1fr; gap: 5px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>競馬予想ボード</h1>
    <p class="lead">予想ログを正本として公開しているレース別ボードです。</p>
    <section class="list">{body}</section>
    <footer>馬場は発走直前確定／馬券は自己責任</footer>
  </main>
</body>
</html>
'''.format(repo_name=html.escape(repo_name), body=body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    race_id = args.race_id.strip()
    if not race_id or "/" in race_id or "\\" in race_id or ".." in race_id:
        fail("race_id contains invalid path characters")

    races = read_csv(repo_root / "log" / "races.csv")
    predictions = read_csv(repo_root / "log" / "predictions.csv")
    find_race(races, race_id)
    ensure_prediction_rows(predictions, race_id)
    validate_board(args.source, race_id)

    docs = repo_root / "docs"
    target_dir = docs / "predictions" / race_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "index.html"
    shutil.copyfile(args.source, target)

    (docs / "index.html").write_text(
        render_index(repo_root.name, races, published_race_ids(docs)),
        encoding="utf-8",
    )
    (docs / ".nojekyll").touch()

    print(f"published: {target.relative_to(repo_root)}")
    print("updated: docs/index.html")
    print("next: commit log CSV changes and docs/ changes together")


if __name__ == "__main__":
    main()
