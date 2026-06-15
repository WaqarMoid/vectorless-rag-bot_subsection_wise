from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi import File as FastAPIFile
from fastapi import UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.ingest.pipeline import ingest_two_books
from app.qa.engine import ChatEngine


class HistoryTurn(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str = Field(min_length=1)
    history: list[HistoryTurn] = Field(default_factory=list)


class CitationResponse(BaseModel):
    citation_id: str
    node_id: str
    book_id: str
    book_title: str
    page_start: int
    page_end: int


class ChatResponse(BaseModel):
    answer: str
    out_of_bounds: bool
    citations: list[CitationResponse]


class UploadBooksResponse(BaseModel):
    message: str
    books_indexed: int
    pages_extracted: int
    chunks_stored: int
    index_location: str


app = FastAPI(title="TOC Bounded Book RAG Chatbot", version="1.0.0")


def _initialize_engine() -> None:
    settings = get_settings()
    settings.ensure_directories()
    app.state.engine = None
    app.state.startup_error = None
    
    # Check if index files exist. If not, check if we can auto-ingest the PDFs from the root folder
    if not settings.tree_path.exists() or not settings.pages_path.exists():
        print("[server] Index files missing. Searching for book PDFs in project root...")
        root_dir = settings.project_root
        pdf_files = list(root_dir.glob("*.pdf"))
        # Exclude architecture document from ingestion
        pdf_files = [p for p in pdf_files if "architecture" not in p.name.lower()]
        
        if len(pdf_files) == 2:
            print(f"[server] Found 2 book PDFs: {[p.name for p in pdf_files]}. Starting auto-ingestion...")
            try:
                ingest_two_books(
                    settings=settings,
                    pdf_paths=pdf_files,
                    book_ids=["book_1", "book_2"],
                )
                print("[server] Auto-ingestion succeeded.")
            except Exception as exc:
                app.state.startup_error = f"Auto-ingestion failed: {exc}"
                print(f"[server] Auto-ingestion failed: {exc}")
                return
        else:
            print(f"[server] Auto-ingestion skipped: expected 2 PDFs, found {len(pdf_files)}: {[p.name for p in pdf_files]}")
            app.state.startup_error = "Index files missing and exactly two book PDFs were not found in the workspace root."
            return

    try:
        app.state.engine = ChatEngine.from_settings(settings)
        print("[server] ChatEngine initialized successfully.")
    except Exception as exc:
        app.state.startup_error = str(exc)
        print(f"[server] ChatEngine initialization failed: {exc}")


@app.on_event("startup")
def startup_event() -> None:
    _initialize_engine()


@app.get("/")
def home() -> FileResponse:
    page = Path(__file__).resolve().parent / "static" / "index.html"
    return FileResponse(page)


@app.get("/health")
def health() -> dict[str, str | bool | None]:
    ready = app.state.engine is not None
    return {
        "status": "ok" if ready else "not_ready",
        "ready": ready,
        "error": app.state.startup_error,
    }


@app.post("/upload-books", response_model=UploadBooksResponse)
def upload_books(files: list[UploadFile] = FastAPIFile(...)) -> UploadBooksResponse:
    if len(files) != 2:
        raise HTTPException(status_code=400, detail="Please upload exactly two PDF files.")

    settings = get_settings()
    settings.ensure_directories()
    raw_dir = settings.data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    for i, upload in enumerate(files, start=1):
        name = upload.filename or f"book{i}.pdf"
        if not name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{name} is not a PDF file.")

        target_path = raw_dir / f"book{i}.pdf"
        with target_path.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        saved_paths.append(target_path)
        upload.file.close()

    try:
        stats = ingest_two_books(
            settings=settings,
            pdf_paths=saved_paths,
            book_ids=["book_1", "book_2"],
        )
        _initialize_engine()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to ingest uploaded books: {exc}") from exc

    return UploadBooksResponse(
        message="Books uploaded and indexed successfully.",
        books_indexed=int(stats["books_indexed"]),
        pages_extracted=int(stats["pages_extracted"]),
        chunks_stored=int(stats["chunks_stored"]),
        index_location=str(stats["index_location"]),
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    if app.state.engine is None:
        detail = app.state.startup_error or "Engine is not initialized."
        raise HTTPException(status_code=503, detail=detail)

    result = app.state.engine.ask(request.query, request.history)
    return ChatResponse(
        answer=result.answer,
        out_of_bounds=result.out_of_bounds,
        citations=[CitationResponse(**citation.__dict__) for citation in result.citations],
    )
