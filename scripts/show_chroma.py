#!/usr/bin/env python
"""Print every record in the ChromaDB collection (id, metadata, start of text)."""

import sys
from pathlib import Path

# Allow `import app` when run as a plain script from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))

import chromadb  # noqa: E402

from app.config import config  # noqa: E402


def main() -> int:
    client = chromadb.HttpClient(host=config.chroma.host, port=config.chroma.port)
    collection = client.get_collection(config.chroma.collection)
    records = collection.get()

    print(f"{config.chroma.collection}: {collection.count()} record(s)")
    for record_id, doc, meta in zip(
        records["ids"], records["documents"], records["metadatas"]
    ):
        print(f"\n{record_id}")
        print(f"  metadata: {meta}")
        print(f"  text:     {doc[:100]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
