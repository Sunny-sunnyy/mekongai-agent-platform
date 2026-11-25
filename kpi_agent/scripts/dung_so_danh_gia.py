"""Dựng số cho tab "Đánh giá" từ sổ nguồn `config/so_danh_gia.yaml` ⇒ `data/danh_gia.json` (Git bỏ qua). Chỉ đọc kết quả đã đo — 0 AI, 0 USD.

Chạy: .venv/bin/python kpi_agent/scripts/dung_so_danh_gia.py [--so <file sổ>] [--ra <file json>]
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from kpi_agent.backend import evaluations  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--so", default=str(REPO_ROOT / "kpi_agent/config/so_danh_gia.yaml"))
    parser.add_argument("--ra", default=str(REPO_ROOT / "kpi_agent/data/danh_gia.json"))
    args = parser.parse_args(argv)
    try:
        data = evaluations.build(REPO_ROOT, Path(args.so))
    except (OSError, ValueError) as problem:
        print(f"KHÔNG dựng được — {problem}", file=sys.stderr)   # sổ nguồn hỏng ⇒ dừng, không ghi file (bản cũ, nếu có, giữ nguyên)
        return 1
    data["built_at"] = datetime.now().strftime("%H:%M %d/%m/%Y")
    Path(args.ra).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in data["versions"]:
        print(f"{row['id']} ({row['commit']}): {row['label']} — {row['label_reason']}")
    print(f"đã ghi {args.ra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
