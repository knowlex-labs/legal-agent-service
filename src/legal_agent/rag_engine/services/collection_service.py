
import requests
import tempfile
from typing import List, Optional
import logging

from legal_agent.rag_engine.repositories.qdrant_repository import QdrantRepository
from legal_agent.rag_engine.services.storage.storage_factory import get_storage_service
from legal_agent.rag_engine.services.hierarchical_chunking_service import chunking_service
from legal_agent.rag_engine.utils.embedding_client import embedding_client
from legal_agent.rag_engine.models.api_models import BatchLinkRequest, LinkItem, IndexingStatus
from legal_agent.rag_engine.parsers.parser_factory import ParserFactory

logger = logging.getLogger(__name__)

class CollectionService:
    def __init__(self):
        self.qdrant_repo = QdrantRepository()
        self.embedding_client = embedding_client
        self.storage_service = get_storage_service()

    def _get_user_collection(self, user_id: str) -> str:
        return f"user_{user_id}"

    def _format_chunks_for_qdrant(self, chunks, embeddings, file_id: str, collection_id: str, source_type: str, content_type: str):
        """Format chunks for Qdrant indexing with proper metadata structure."""
        documents = []
        for i, chunk in enumerate(chunks):
            documents.append({
                "text": chunk.text,
                "vector": embeddings[i],
                "document_id": file_id,
                "chunk_id": chunk.chunk_id,
                "source": source_type,
                "metadata": {
                    "collection_id": collection_id or "default",
                    "file_id": file_id,
                    "source_type": source_type,
                    "content_type": content_type,
                    "chunk_type": chunk.chunk_metadata.chunk_type.value,
                    "chapter_title": chunk.topic_metadata.chapter_title,
                    "section_title": chunk.topic_metadata.section_title,
                    "page_start": chunk.topic_metadata.page_start,
                    "key_terms": chunk.chunk_metadata.key_terms,
                }
            })
        return documents

    async def process_batch(self, request: BatchLinkRequest, tenant_id: str):
        logger.info(f"=== INDEXING: process_batch STARTED ===")
        logger.info(f"tenant_id={tenant_id}, item_count={len(request.items)}")

        collection_id = request.items[0].collection_id if request.items else "default"
        content_type = request.items[0].content_type.value if request.items and request.items[0].content_type else "legal"
        logger.info(f"Starting batch process for tenant {tenant_id}, collection {collection_id}, content_type {content_type}")

        qdrant_collection_name = collection_id
        if not self.qdrant_repo.collection_exists(collection_id):
            raise ValueError(f"Collection '{collection_id}' does not exist")

        results = []
        for idx, item in enumerate(request.items):
            logger.info(f"=== INDEXING: Item {idx + 1}/{len(request.items)} ===")
            logger.info(f"file_id={item.file_id}, type={item.type}")
            try:
                logger.info(f"Resolving source for {item.file_id}")
                source = self._resolve_source(item)
                logger.info(f"Source resolved: {source[:80] if source else 'None'}")

                logger.info(f"Parsing content for {item.file_id}")
                parsed = self._parse_content(source, item.type)
                logger.info(f"Parsed: title={getattr(parsed.metadata, 'title', 'N/A')}")

                logger.info(f"Chunking content for {item.file_id}")
                chunks = self._chunk_content(parsed, parsed.source_type)
                logger.info(f"Created {len(chunks)} chunks")

                logger.info(f"Generating embeddings for {len(chunks)} chunks")
                embeddings = self._generate_embeddings(chunks, parsed)
                logger.info(f"Embeddings generated: {len(embeddings)}")

                item_content_type = item.content_type.value if item.content_type else "legal"

                # Index to Qdrant (default)
                logger.info(f"Indexing chunks to Qdrant for {item.file_id}")
                qdrant_documents = self._format_chunks_for_qdrant(
                    chunks=chunks,
                    embeddings=embeddings,
                    file_id=item.file_id,
                    collection_id=item.collection_id or "default",
                    source_type=item.type,
                    content_type=item_content_type
                )
                self.qdrant_repo.link_content(qdrant_collection_name, qdrant_documents)

                logger.info(f"Successfully processed {item.file_id}")

                results.append({
                    "file_id": item.file_id,
                    "status": "INDEXING_SUCCESS",
                    "error": None
                })

            except Exception as e:
                logger.error(f"=== INDEXING FAILED: {item.file_id} ===")
                logger.error(f"Error: {e}", exc_info=True)
                results.append({
                    "file_id": item.file_id,
                    "status": "INDEXING_FAILED",
                    "error": str(e)
                })

        logger.info(f"=== INDEXING: process_batch COMPLETED ===")
        return results

    def _resolve_source(self, item: LinkItem) -> str:
        logger.info(f"_resolve_source: item type={item.type}")
        from urllib.parse import unquote
        if item.type in ('file', 'image'):
            if not item.storage_url:
                logger.error("_resolve_source: Missing storage_url!")
                raise ValueError("Missing storage_url")

            # Unquote in case of %20 etc
            storage_url = unquote(item.storage_url)
            logger.info(f"_resolve_source: storage_url={storage_url}")

            # Handle local:// URLs (local storage)
            if storage_url.startswith('local://'):
                logger.info(f"_resolve_source: Downloading from local storage")
                local_path = self.storage_service.download_for_processing(storage_url)
                if not local_path:
                    logger.error(f"_resolve_source: Failed to get file from local storage: {storage_url}")
                    raise ValueError(f"Failed to get file from local storage: {storage_url}")
                logger.info(f"_resolve_source: local_path={local_path}")
                return local_path

            # Handle HTTP/HTTPS URLs
            if storage_url.startswith(('http://', 'https://')):
                logger.info(f"_resolve_source: Downloading from HTTP URL")
                result = self._download_from_url(storage_url)
                logger.info(f"_resolve_source: downloaded to {result}")
                return result

            logger.error(f"_resolve_source: Unsupported storage URL format: {storage_url}")
            raise ValueError(f"Unsupported storage URL format: {storage_url}")
        logger.info(f"_resolve_source: returning item.url")
        return item.url

    def _download_from_url(self, url: str) -> str:
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()

            # Detect suffix from URL, fallback to .pdf
            from pathlib import Path as _Path
            url_path = _Path(url.split('?')[0])
            suffix = url_path.suffix.lower() if url_path.suffix else ".pdf"

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                return tmp_file.name
        except Exception as e:
            logger.error(f"Failed to download file from URL {url}: {e}")
            raise ValueError(f"Failed to download file: {str(e)}")


    def _parse_content(self, source: str, item_type: str):
        logger.info(f"_parse_content: source={source[:50]}, item_type={item_type}")
        # For generic "file" type, detect actual format from extension
        if item_type == "file":
            from pathlib import Path as _Path
            ext = _Path(source).suffix.lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp"}:
                item_type = "image"
                logger.info(f"_parse_content: detected image from extension {ext}, routing to ImageParser")
        parser = ParserFactory.get_parser(item_type)
        result = parser.parse(source)
        logger.info(f"_parse_content: done, pages={getattr(result.metadata, 'page_count', 'N/A')}")
        return result

    def _chunk_content(self, parsed_content, item_type: str):
        logger.info(f"_chunk_content: item_type={item_type}")
        result = chunking_service.chunk_parsed_content(parsed_content, item_type)
        logger.info(f"_chunk_content: created {len(result)} chunks")
        return result

    def _generate_embeddings(self, chunks, parsed_content=None):
        logger.info(f"_generate_embeddings: {len(chunks)} chunks")

        # For images, embed the raw image bytes directly (multimodal embedding)
        if parsed_content and parsed_content.source_type == "image" and parsed_content.image_data:
            from legal_agent.rag_engine.parsers.image_parser import MIME_TYPES
            from pathlib import Path
            ext = Path(parsed_content.image_path).suffix.lower() if parsed_content.image_path else ".png"
            mime_type = MIME_TYPES.get(ext, "image/png")
            embedding = self.embedding_client.generate_image_embedding(parsed_content.image_data, mime_type)
            logger.info(f"_generate_embeddings: generated 1 image embedding")
            return [embedding]

        texts = [chunk.text for chunk in chunks]
        result = self.embedding_client.generate_embeddings(texts)
        logger.info(f"_generate_embeddings: done, {len(result)} embeddings")
        return result

    def _set_status(self, tenant_id: str, file_id: str, status: IndexingStatus, error: Optional[str] = None, item_name: Optional[str] = None, source_type: Optional[str] = None):
        logger.debug(f"Status update (no-op): file_id={file_id}, status={status.value}")

    def get_status(self, tenant_id: str, file_id: str) -> dict:
        """Check if a document has chunks indexed in Qdrant for this user."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        qdrant_collection_name = self._get_user_collection(tenant_id)
        try:
            if not self.qdrant_repo.collection_exists(qdrant_collection_name):
                return {
                    "status": "PROCESSING",
                    "file_id": file_id,
                    "message": "User collection not found",
                }

            scroll_result = self.qdrant_repo.client.scroll(
                collection_name=qdrant_collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(key="metadata.file_id", match=MatchValue(value=file_id))]
                ),
                limit=1,
                with_payload=True,
                with_vectors=False,
            )
            points = scroll_result[0] if scroll_result else []
            if points:
                indexed_at = points[0].payload.get("metadata", {}).get("indexed_at")
                return {
                    "status": "READY",
                    "file_id": file_id,
                    "message": "Document is indexed and ready for AI",
                    "indexed_at": indexed_at,
                }
            return {
                "status": "PROCESSING",
                "file_id": file_id,
                "message": "Document is being processed or not found",
            }
        except Exception as e:
            logger.error(f"Error checking status: {e}")
            return {"status": "ERROR", "file_id": file_id, "message": str(e)}



    def unlink_content(self, collection_name: Optional[str], file_ids: List[str], user_id: str) -> int:
        deleted_count = 0
        qdrant_collection_name = self._get_user_collection(user_id)

        for file_id in file_ids:
            try:
                # Delete from Qdrant first (default backend)
                qdrant_success = self.qdrant_repo.unlink_content(
                    collection_name=qdrant_collection_name,
                    file_id=file_id
                )

                if qdrant_success:
                    deleted_count += 1
            except Exception:
                logger.error(f"Error unlinking file {file_id}", exc_info=True)
        return deleted_count

    def delete_collection(self, user_id: str, collection_id: str) -> bool:
        return self.qdrant_repo.delete_logical_collection(user_id, collection_id)

    def purge_user_data(self, user_id: str) -> bool:
        """Delete the user's entire Qdrant collection."""
        name = self._get_user_collection(user_id)
        return self.qdrant_repo.delete_collection(name)

    def _check_file_in_collection(self, collection_name: str, collection_id: str, file_id: str) -> dict:
        """Check if a specific file exists in a collection and return its status."""
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        try:
            scroll_result = self.qdrant_repo.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="metadata.collection_id", match=MatchValue(value=collection_id)),
                        FieldCondition(key="metadata.file_id", match=MatchValue(value=file_id))
                    ]
                ),
                limit=100,
                with_payload=True,
                with_vectors=False
            )

            points = scroll_result[0] if scroll_result else []

            if points:
                indexed_at = points[0].payload.get("metadata", {}).get("indexed_at")
                return {
                    "file_id": file_id,
                    "status": IndexingStatus.SUCCESS,
                    "chunk_count": len(points),
                    "indexed_at": indexed_at
                }

            # Not found - return FAILED with error message
            return {
                "file_id": file_id,
                "status": IndexingStatus.FAILED,
                "error": "not found"
            }

        except Exception as e:
            logger.error(f"Error checking file {file_id}: {e}")
            return {
                "file_id": file_id,
                "status": IndexingStatus.FAILED,
                "error": str(e)
            }

    def check_collection_status(self, user_id: str, collection_id: str, file_ids: List[str]) -> List[dict]:
        """Check the indexing status of multiple files within a specific collection."""
        qdrant_collection_name = self._get_user_collection(user_id)

        if not self.qdrant_repo.collection_exists(qdrant_collection_name):
            return [{"file_id": fid, "status": IndexingStatus.FAILED, "error": "not found"} for fid in file_ids]

        return [self._check_file_in_collection(qdrant_collection_name, collection_id, fid) for fid in file_ids]
