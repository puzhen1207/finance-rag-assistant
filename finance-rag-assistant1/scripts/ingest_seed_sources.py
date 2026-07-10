from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.document_loader import load_url
from app.rag import RagStore


def main() -> None:
    seed_path = ROOT / "data" / "seed_sources.json"
    seeds = json.loads(seed_path.read_text(encoding="utf-8"))["sources"]
    store = RagStore(settings.data_dir)
    for item in seeds:
        try:
            print(f"Ingesting: {item['title']}")
            doc = load_url(item["url"], title=item["title"])
            summary, _, metrics = store.add_document(doc)
            print(
                "  OK: "
                f"{summary['chunks']} chunks, "
                f"{summary['vectors_inserted']} vectors, "
                f"{summary['dimension']} dimensions, "
                f"{metrics['total_seconds']}s"
            )
        except Exception as exc:
            print(f"  FAILED: {exc}")


if __name__ == "__main__":
    main()
