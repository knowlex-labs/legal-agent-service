from fastapi import APIRouter, Header, HTTPException
from typing import Optional, Tuple
import logging

from legal_agent.rag_engine.repositories.qdrant_repository import QdrantRepository
from legal_agent.rag_engine.services.collection_service import CollectionService
from legal_agent.rag_engine.services.query_service import QueryService
from legal_agent.rag_engine.models.api_models import (
    BatchLinkRequest, IngestionResponse, QueryAnswerRequest, QueryResponse,
    RetrieveRequest, RetrieveResponse, DeleteFileRequest,
    CreateCollectionRequest, GetChunksRequest, FileStatusRequest,
)

router = APIRouter()
logger = logging.getLogger(__name__)

qdrant_repo = QdrantRepository()
collection_service = CollectionService()
query_service = QueryService()


def _get_collection_name(user_id: str) -> str:
    return f"user_{user_id}"


def _extract_filters(request) -> Tuple[Optional[list], Optional[str], Optional[str]]:
    if not request.filters:
        return None, None, None
    f = request.filters
    return (
        f.file_ids,
        f.content_type.value if f.content_type else None,
        f.news_subcategory,
    )


@router.post("/create")
async def create_collection(request: CreateCollectionRequest):
    """Create a new collection in Qdrant."""
    try:
        logger.info(f"Creating collection: {request.collection_name}")

        if qdrant_repo.collection_exists(request.collection_name):
            return {
                "success": True,
                "message": f"Collection '{request.collection_name}' already exists",
                "collection_name": request.collection_name
            }

        success = qdrant_repo.create_collection(
            collection_name=request.collection_name,
            use_new_schema=request.use_new_schema
        )

        if success:
            return {
                "success": True,
                "message": f"Created collection '{request.collection_name}'",
                "collection_name": request.collection_name
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create collection")

    except Exception as e:
        logger.error(f"Error creating collection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_collections():
    """List all collections in Qdrant."""
    try:
        collections = qdrant_repo.client.get_collections()
        collection_names = [col.name for col in collections.collections]

        return {
            "success": True,
            "collections": collection_names,
            "count": len(collection_names)
        }
    except Exception as e:
        logger.error(f"Error listing collections: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{collection_id}/files/status")
async def get_files_status(
    collection_id: str,
    request: FileStatusRequest,
    x_user_id: str = Header(...)
):
    """Check indexing status of specific files within a collection."""
    try:
        logger.info(f"Checking status for {len(request.file_ids)} files in collection {collection_id}")

        collection_name = collection_id
        file_statuses = []

        for file_id in request.file_ids:
            # Use request.collection_id if provided, otherwise fall back to URL path param
            filter_collection_id = request.collection_id if request.collection_id else collection_id
            chunks = qdrant_repo.scroll_by_filter(
                collection_name=collection_name,
                filters={
                    "metadata.collection_id": filter_collection_id,
                    "metadata.file_id": file_id
                },
                limit=10000
            )

            chunk_count = len(chunks)

            if chunk_count > 0:
                indexed_at = None
                if chunks and chunks[0].get("metadata", {}).get("indexed_at"):
                    indexed_at = chunks[0]["metadata"]["indexed_at"]

                file_statuses.append({
                    "file_id": file_id,
                    "status": "INDEXED",
                    "chunk_count": chunk_count,
                    "indexed_at": indexed_at
                })
            else:
                file_statuses.append({
                    "file_id": file_id,
                    "status": "NOT_FOUND",
                    "chunk_count": 0,
                    "indexed_at": None
                })

        return {
            "success": True,
            "collection_id": collection_id,
            "files": file_statuses,
            "total_files": len(file_statuses)
        }

    except Exception as e:
        logger.error(f"Error checking file status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{collection_id}/link-content", response_model=IngestionResponse, status_code=207)
async def link_content(
    collection_id: str,
    request: BatchLinkRequest,
    x_user_id: str = Header(...)
):
    """Index documents, web pages, or YouTube videos to a collection."""
    try:
        logger.info(f"=== API: link_content STARTED ===")
        logger.info(f"collection_id={collection_id}, user_id={x_user_id}, item_count={len(request.items)}")

        for item in request.items:
            item.collection_id = collection_id

        logger.info(f"Calling collection_service.process_batch...")
        results = await collection_service.process_batch(request, x_user_id)
        logger.info(f"collection_service.process_batch returned {len(results)} results")

        # Surface quota errors as HTTP 429 so callers stop retrying
        for result in results:
            if result.get("status") == "INDEXING_FAILED":
                error = result.get("error", "")
                if "429" in error or "RESOURCE_EXHAUSTED" in error or "quota" in error.lower():
                    raise HTTPException(status_code=429, detail="Gemini API quota exceeded. Please retry later.")

        return IngestionResponse(
            message=f"Processed {len(results)} items",
            batch_id="sync",
            results=results
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== API: link_content FAILED ===")
        logger.error(f"Error linking content: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{collection_id}/query", response_model=QueryResponse)
async def query_collection(
    collection_id: str,
    request: QueryAnswerRequest,
    x_user_id: str = Header(...)
):
    """Query documents in a collection."""
    try:
        file_ids, content_type, _news_subcategory = _extract_filters(request)

        return query_service.search(
            collection_name=collection_id,
            query_text=request.query,
            limit=request.top_k,
            collection_ids=None,
            file_ids=file_ids,
            content_type=content_type,
            answer_style=request.answer_style or "detailed"
        )
    except Exception as e:
        logger.error(f"Error querying collection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{collection_id}/retrieve", response_model=RetrieveResponse)
async def retrieve_chunks(
    collection_id: str,
    request: RetrieveRequest,
    x_user_id: str = Header(...)
):
    """Retrieve relevant chunks for a query without generating an answer."""
    try:
        file_ids, content_type, _news_subcategory = _extract_filters(request)

        results = await query_service.retrieve_context(
            query=request.query,
            user_id=collection_id,
            collection_ids=None,
            top_k=request.top_k,
            file_ids=file_ids,
            content_type=content_type,
        )

        enriched_chunks = []
        for r in results:
            enriched_chunks.append({
                "chunk_id": r.get("id", ""),
                "chunk_text": r.get("text", ""),
                "relevance_score": r.get("score", 0.0),
                "file_id": r.get("metadata", {}).get("file_id", ""),
                "page_number": r.get("metadata", {}).get("page_number"),
                "timestamp": r.get("metadata", {}).get("timestamp"),
                "concepts": r.get("metadata", {}).get("concepts", [])
            })

        return RetrieveResponse(success=True, results=enriched_chunks)
    except Exception as e:
        logger.error(f"Error retrieving chunks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{collection_id}/chunks")
async def get_chunks(
    collection_id: str,
    request: GetChunksRequest,
    x_user_id: str = Header(...)
):
    """Get all chunks for a specific file in a collection."""
    try:
        logger.info(f"Getting chunks for file {request.file_id} in {collection_id}")

        collection_name = collection_id

        chunks = qdrant_repo.scroll_by_filter(
            collection_name=collection_name,
            filters={
                "metadata.collection_id": collection_id,
                "metadata.file_id": request.file_id
            },
            limit=request.limit
        )

        return {
            "success": True,
            "file_id": request.file_id,
            "collection_id": collection_id,
            "chunks": chunks,
            "count": len(chunks)
        }

    except Exception as e:
        logger.error(f"Error getting chunks: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{collection_id}/status")
async def get_collection_status(
    collection_id: str,
    x_user_id: str = Header(...)
):
    """Get all files in a collection and their indexing status."""
    try:
        logger.info(f"Getting status for collection {collection_id}")

        collection_name = collection_id

        chunks = qdrant_repo.scroll_by_filter(
            collection_name=collection_name,
            filters={"metadata.collection_id": collection_id},
            limit=10000
        )

        file_chunks = {}
        for chunk in chunks:
            file_id = chunk.get("metadata", {}).get("file_id")
            if file_id:
                if file_id not in file_chunks:
                    file_chunks[file_id] = {
                        "chunks": [],
                        "indexed_at": chunk.get("metadata", {}).get("indexed_at")
                    }
                file_chunks[file_id]["chunks"].append(chunk)

        file_statuses = []
        for file_id, data in file_chunks.items():
            file_statuses.append({
                "file_id": file_id,
                "status": "INDEXED",
                "chunk_count": len(data["chunks"]),
                "indexed_at": data["indexed_at"]
            })

        return {
            "success": True,
            "collection_id": collection_id,
            "files": file_statuses,
            "total_files": len(file_statuses),
            "total_chunks": len(chunks)
        }

    except Exception as e:
        logger.error(f"Error getting collection status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{collection_id}/files")
async def delete_files(
    collection_id: str,
    request: DeleteFileRequest,
    x_user_id: str = Header(...)
):
    """Delete specific files from a collection."""
    try:
        logger.info(f"Deleting {len(request.file_ids)} files from {collection_id}")

        count = collection_service.unlink_content(
            collection_name=collection_id,
            file_ids=request.file_ids,
            user_id=x_user_id
        )

        return {
            "success": True,
            "message": f"Deleted {count} file(s)",
            "deleted_count": count
        }
    except Exception as e:
        logger.error(f"Error deleting files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{collection_id}")
async def delete_collection(collection_id: str):
    """Delete an entire collection from Qdrant. This permanently deletes all data!"""
    try:
        logger.warning(f"Deleting collection: {collection_id}")

        if not qdrant_repo.collection_exists(collection_id):
            raise HTTPException(status_code=404, detail=f"Collection '{collection_id}' not found")

        qdrant_repo.client.delete_collection(collection_name=collection_id)

        return {
            "success": True,
            "message": f"Deleted collection '{collection_id}'"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting collection: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
