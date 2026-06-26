from __future__ import annotations

from config import VECTOR_DIR
from tools import list_notes


def build_vector_index() -> dict[str, int | str]:
    notes = list_notes()
    try:
        import chromadb
    except ModuleNotFoundError:
        return {
            "mode": "keyword_fallback",
            "note_count": len(notes),
            "chunk_count": len(notes),
            "message": "chromadb is not installed. The app will use local keyword retrieval fallback.",
        }

    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(VECTOR_DIR))
    collection = client.get_or_create_collection("study_notes")
    existing = collection.count()
    if existing:
        ids = collection.get().get("ids", [])
        if ids:
            collection.delete(ids=ids)

    documents = [note["content"] for note in notes]
    ids = [f"note-{note['id']}-0" for note in notes]
    metadatas = [{"note_id": note["id"], "course": note["course"], "title": note["title"]} for note in notes]
    if documents:
        collection.add(documents=documents, ids=ids, metadatas=metadatas)
    return {"mode": "chroma", "note_count": len(notes), "chunk_count": len(documents), "message": "Vector index built."}


if __name__ == "__main__":
    print(build_vector_index())

