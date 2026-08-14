import logging
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent
COLLECTION_NAME = "claude_demo_docs"
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
VECTOR_SIZE = 384

INCLUDE_EXTENSIONS = {".py", ".jsx", ".md", ".css", ".html"}
EXCLUDE_DIRS = {"venv", "node_modules", "dist", ".git", "__pycache__"}
MAX_CHUNK_CHARS = 800


def iter_project_files():
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in INCLUDE_EXTENSIONS:
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        yield path


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Découpe le texte en paragraphes, regroupés jusqu'à ~max_chars."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def main():
    client = QdrantClient(url="http://localhost:6333")

    if client.collection_exists(COLLECTION_NAME):
        logger.info("Suppression de l'ancienne collection %s", COLLECTION_NAME)
        client.delete_collection(COLLECTION_NAME)

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
    )

    points = []
    for path in iter_project_files():
        rel_path = path.relative_to(PROJECT_ROOT)
        text = path.read_text(encoding="utf-8", errors="ignore")
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            points.append(
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=models.Document(text=chunk, model=EMBEDDING_MODEL),
                    payload={"source": str(rel_path), "chunk_index": i, "text": chunk},
                )
            )
        logger.info("Indexé %s (%d chunks)", rel_path, len(chunks))

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)

    logger.info("Terminé : %d chunks indexés dans %s", len(points), COLLECTION_NAME)


if __name__ == "__main__":
    main()
