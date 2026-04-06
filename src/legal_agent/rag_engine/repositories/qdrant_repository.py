from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance, PointStruct, Filter, FieldCondition,
    MatchValue, MatchAny, PayloadSchemaType
)
from typing import List, Dict, Any, Optional
import uuid
import logging
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

class QdrantRepository:
    def __init__(self):
        host = get_settings().qdrant_host

        # Determine if using cloud instance (has domain with dots) or local
        is_cloud = '.' in host and 'localhost' not in host

        if is_cloud:
            # For cloud instances, use HTTPS and the full host URL without port
            self.client = QdrantClient(
                url=f"https://{host}",
                api_key=get_settings().qdrant_api_key,
                timeout=get_settings().qdrant_timeout
            )
        else:
            # For local instances, use HTTP with port
            self.client = QdrantClient(
                url=f"http://{host}:{get_settings().qdrant_port}",
                api_key=get_settings().qdrant_api_key,
                timeout=get_settings().qdrant_timeout
            )
            


    def collection_exists(self, collection_name: str) -> bool:
        try:
            logger.debug(f"Checking if collection '{collection_name}' exists")
            collections = self.client.get_collections()
            collection_names = [col.name for col in collections.collections]
            exists = collection_name in collection_names
            logger.debug(f"Collection '{collection_name}' exists: {exists}")
            return exists
        except Exception as e:
            logger.error(f"Error checking if collection '{collection_name}' exists: {str(e)}")
            raise

    def ensure_indexes(self, collection_name: str, use_new_schema: bool = False) -> bool:
        """
        Ensure payload indexes exist on collection.

        Args:
            collection_name: Name of the collection
            use_new_schema: If True, use new per-user collection schema with collection_id

        Returns:
            True if indexes were created/verified successfully
        """
        try:
            logger.info(f"Ensuring payload indexes exist on collection '{collection_name}'")

            if use_new_schema:
                # New schema for per-user collections
                indexes_to_create = [
                    ("metadata.collection_id", PayloadSchemaType.KEYWORD),
                    ("metadata.file_id", PayloadSchemaType.KEYWORD),
                    ("metadata.source_type", PayloadSchemaType.KEYWORD),
                    ("metadata.chunk_type", PayloadSchemaType.KEYWORD),
                    ("metadata.hierarchy_level", PayloadSchemaType.INTEGER),
                    ("metadata.page_number", PayloadSchemaType.INTEGER),
                    ("document_id", PayloadSchemaType.KEYWORD),  # For backward compatibility
                ]
            else:
                # Legacy schema
                indexes_to_create = [
                    ("document_id", PayloadSchemaType.KEYWORD),
                    ("metadata.chunk_type", PayloadSchemaType.KEYWORD),
                    ("metadata.chapter_num", PayloadSchemaType.INTEGER),
                    ("metadata.content_type", PayloadSchemaType.KEYWORD),
                    ("metadata.book_metadata.book_id", PayloadSchemaType.KEYWORD),
                    ("metadata.book_metadata.book_title", PayloadSchemaType.KEYWORD),
                ]

            for field_name, field_schema in indexes_to_create:
                self._create_single_index(collection_name, field_name, field_schema)

            logger.info(f"Payload indexes ensured on collection '{collection_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to ensure indexes on collection '{collection_name}': {str(e)}")
            return False

    def _create_single_index(self, collection_name: str, field_name: str, field_schema: str) -> None:
        try:
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema
            )
            logger.debug(f"Created index for {field_name} on collection '{collection_name}'")
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.debug(f"Index for {field_name} already exists on collection '{collection_name}'")
            else:
                logger.warning(f"Could not create index for {field_name}: {str(e)}")

    def create_collection(self, collection_name: str, use_new_schema: bool = False) -> bool:
        """
        Create a Qdrant collection.

        Args:
            collection_name: Name of the collection
            use_new_schema: If True, use new per-user collection schema

        Returns:
            True if collection was created, False if it already exists
        """
        try:
            if self.collection_exists(collection_name):
                logger.info(f"Collection '{collection_name}' already exists")
                return False

            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=get_settings().vector_size,
                    distance=Distance.COSINE
                )
            )

            self.ensure_indexes(collection_name, use_new_schema=use_new_schema)
            logger.info(f"Created collection '{collection_name}' with payload indexes")
            return True
        except Exception as e:
            logger.error(f"Failed to create collection '{collection_name}': {str(e)}")
            return False

    def create_user_collection(self, user_id: str) -> bool:
        """
        Create Qdrant collection for a specific user with new schema.

        Args:
            user_id: User identifier (Firebase UID)

        Returns:
            True if collection was created, False if it already exists
        """
        collection_name = f"user_{user_id}"
        logger.info(f"Creating user collection: {collection_name}")

        try:
            if self.collection_exists(collection_name):
                logger.info(f"User collection '{collection_name}' already exists")
                return False

            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=get_settings().vector_size,
                    distance=Distance.COSINE,
                    on_disk=False  # Keep in memory for better performance
                ),
                hnsw_config={
                    "m": 16,
                    "ef_construct": 100,
                    "full_scan_threshold": 10000
                }
            )

            # Create indexes for new schema
            self.ensure_indexes(collection_name, use_new_schema=True)
            logger.info(f"Created user collection '{collection_name}' with new schema and indexes")
            return True
        except Exception as e:
            logger.error(f"Failed to create user collection '{collection_name}': {str(e)}")
            return False

    def delete_collection(self, collection_name: str) -> bool:
        """
        Delete an entire Qdrant collection.

        Args:
            collection_name: Name of the collection to delete

        Returns:
            True if deletion was successful
        """
        try:
            if not self.collection_exists(collection_name):
                logger.warning(f"Collection '{collection_name}' does not exist")
                return False

            self.client.delete_collection(collection_name)
            logger.info(f"Deleted collection '{collection_name}'")
            return True
        except Exception as e:
            logger.error(f"Error deleting collection '{collection_name}': {str(e)}")
            return False

    def delete_logical_collection(self, user_id: str, collection_id: str) -> bool:
        """
        Delete a logical collection (folder) within a user's Qdrant collection.
        This deletes all points where metadata.collection_id matches the given value.

        Args:
            user_id: User identifier
            collection_id: Logical collection identifier to delete

        Returns:
            True if deletion was successful
        """
        collection_name = f"user_{user_id}"
        try:
            logger.info(f"Deleting logical collection '{collection_id}' from user collection '{collection_name}'")

            result = self.client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="metadata.collection_id",
                            match=MatchValue(value=collection_id)
                        )
                    ]
                )
            )

            logger.info(f"Deleted logical collection '{collection_id}' from '{collection_name}'. Result: {result}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete logical collection '{collection_id}' from '{collection_name}': {str(e)}")
            return False

    def list_collections(self) -> List[str]:
        try:
            logger.info("Fetching list of collections from Qdrant")
            collections = self.client.get_collections()
            collection_names = [col.name for col in collections.collections]
            logger.debug(f"Found {len(collection_names)} collections: {collection_names}")
            return collection_names
        except Exception as e:
            logger.error(f"Failed to list collections: {str(e)}")
            logger.exception("Full exception details:")
            return []


    def link_content(self, collection_name: str, documents: List[Dict[str, Any]], batch_size: int = 500) -> bool:
        try:
            logger.info(f"Linking {len(documents)} documents to collection '{collection_name}'")

            # Build and upload in batches to avoid holding all PointStructs in memory
            total_batches = (len(documents) + batch_size - 1) // batch_size
            for i in range(0, len(documents), batch_size):
                batch_docs = documents[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                points = [self._create_point_from_document(doc) for doc in batch_docs]
                logger.info(f"Uploading batch {batch_num}/{total_batches} ({len(points)} points)...")
                result = self.client.upsert(collection_name=collection_name, points=points)
                logger.debug(f"Batch {batch_num} upload result: {result}")
                del points  # free memory

            logger.info(f"Successfully linked all {len(documents)} documents to collection '{collection_name}'")
            return True
        except Exception as e:
            logger.error(f"Failed to link content to collection '{collection_name}': {str(e)}")
            logger.exception("Full exception details:")
            return False

    def _create_point_from_document(self, doc: Dict[str, Any]) -> PointStruct:
        text = doc.get("text", "")
        vector = doc.get("vector", [])
        doc_id = doc.get("document_id")
        chunk_id = doc.get("chunk_id")

        logger.debug(f"Processing document {doc_id}, chunk_id: {chunk_id}, text length: {len(text)}, vector length: {len(vector)}")

        payload = {
            "document_id": doc_id,
            "text": text,
            "source": doc.get("source", ""),
            "metadata": doc.get("metadata", {})
        }

        if chunk_id:
            payload["chunk_id"] = chunk_id

        return PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload=payload
        )

    def _upload_points_in_batches(self, collection_name: str, points: List[PointStruct], batch_size: int = 500) -> None:
        total_batches = (len(points) + batch_size - 1) // batch_size
        logger.info(f"Uploading {len(points)} points in {total_batches} batches (batch size: {batch_size})")

        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            logger.info(f"Uploading batch {batch_num}/{total_batches} ({len(batch)} points)...")
            result = self.client.upsert(collection_name=collection_name, points=batch)
            logger.debug(f"Batch {batch_num} upload result: {result}")

    def unlink_content(
        self,
        collection_name: str,
        document_ids: Optional[List[str]] = None,
        file_id: Optional[str] = None,
        collection_id: Optional[str] = None
    ) -> bool:
        """
        Unlink content from a collection by deleting points matching filters.

        Args:
            collection_name: Name of the Qdrant collection
            document_ids: List of document IDs to delete (legacy)
            file_id: Delete all chunks from a specific file (new schema)
            collection_id: Delete from a specific logical collection (new schema)

        Returns:
            True if deletion was successful
        """
        try:
            conditions = []

            if document_ids:
                # Legacy approach: delete by document_id
                logger.info(f"Unlinking {len(document_ids)} documents from collection '{collection_name}'")
                for doc_id in document_ids:
                    logger.debug(f"Deleting document {doc_id} from collection '{collection_name}'")
                    result = self.client.delete(
                        collection_name=collection_name,
                        points_selector=Filter(
                            must=[FieldCondition(key="document_id", match=MatchValue(value=doc_id))]
                        )
                    )
                    logger.debug(f"Delete result for {doc_id}: {result}")
                logger.info(f"Successfully unlinked all documents from collection '{collection_name}'")
                return True

            # New schema approach: filter by file_id and/or collection_id
            if file_id:
                conditions.append(
                    FieldCondition(key="metadata.file_id", match=MatchValue(value=file_id))
                )

            if collection_id:
                conditions.append(
                    FieldCondition(key="metadata.collection_id", match=MatchValue(value=collection_id))
                )

            if not conditions:
                logger.warning("No deletion criteria provided")
                return False

            logger.info(f"Unlinking content from collection '{collection_name}' with filters: file_id={file_id}, collection_id={collection_id}")

            result = self.client.delete(
                collection_name=collection_name,
                points_selector=Filter(must=conditions)
            )

            logger.info(f"Successfully unlinked content from collection '{collection_name}'. Result: {result}")
            return True

        except Exception as e:
            logger.error(f"Failed to unlink content from collection '{collection_name}': {str(e)}")
            logger.exception("Full exception details:")
            return False

    def query_collection(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 5,
        chunk_type: Optional[str] = None,
        collection_id: Optional[str] = None,
        collection_ids: Optional[List[str]] = None,
        source_type: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query a Qdrant collection with optional filters.

        Args:
            collection_name: Name of the collection (e.g., 'user_{user_id}')
            query_vector: Query embedding vector
            limit: Maximum number of results
            chunk_type: Filter by chunk type ('concept', 'example', 'question', 'other')
            collection_id: Filter by single logical collection (folder)
            collection_ids: Filter by multiple logical collections (folders)
            source_type: Filter by source type ('pdf', 'youtube', 'web')
            file_ids: Filter by specific file IDs

        Returns:
            List of search results with scores and payloads
        """
        try:
            query_filter = self._build_query_filter(
                chunk_type=chunk_type,
                collection_id=collection_id,
                collection_ids=collection_ids,
                source_type=source_type,
                file_ids=file_ids,
            )

            results = self._search_with_retry(collection_name, query_vector, limit, query_filter)
            return self._format_search_results(results)
        except Exception as e:
            logger.error(f"Error querying collection: {e}")
            return []

    def _build_query_filter(
        self,
        chunk_type: Optional[str] = None,
        collection_id: Optional[str] = None,
        collection_ids: Optional[List[str]] = None,
        source_type: Optional[str] = None,
        file_ids: Optional[List[str]] = None,
    ) -> Optional[Filter]:
        """
        Build Qdrant filter from query parameters.

        Args:
            chunk_type: Filter by chunk type
            collection_id: Filter by single logical collection
            collection_ids: Filter by multiple logical collections
            source_type: Filter by source type
            file_ids: Filter by specific file IDs

        Returns:
            Qdrant Filter object or None if no filters
        """
        conditions = []

        if chunk_type:
            conditions.append(
                FieldCondition(key="metadata.chunk_type", match=MatchValue(value=chunk_type))
            )

        if collection_id:
            conditions.append(
                FieldCondition(key="metadata.collection_id", match=MatchValue(value=collection_id))
            )

        if collection_ids:
            conditions.append(
                FieldCondition(key="metadata.collection_id", match=MatchAny(any=collection_ids))
            )

        if source_type:
            conditions.append(
                FieldCondition(key="metadata.source_type", match=MatchValue(value=source_type))
            )

        if file_ids:
            conditions.append(
                FieldCondition(key="metadata.file_id", match=MatchAny(any=file_ids))
            )

        if not conditions:
            return None

        return Filter(must=conditions)

    def _search_with_retry(self, collection_name: str, query_vector: List[float], limit: int, query_filter: Optional[Filter]) -> List[Any]:
        try:
            results = self.client.query_points(
                collection_name=collection_name,
                query=query_vector,
                limit=limit,
                query_filter=query_filter
            )
            return results.points if hasattr(results, 'points') else results
        except Exception as e:
            if "Index required" in str(e):
                logger.warning(f"Missing index detected for collection '{collection_name}', attempting to create indexes")
                if self.ensure_indexes(collection_name):
                    logger.info(f"Indexes created, retrying query for collection '{collection_name}'")
                    results = self.client.query_points(
                        collection_name=collection_name,
                        query=query_vector,
                        limit=limit,
                        query_filter=query_filter
                    )
                    return results.points if hasattr(results, 'points') else results
            raise

    def _format_search_results(self, results: List[Any]) -> List[Dict[str, Any]]:
        return [
            {
                "id": hit.id,
                "score": hit.score,
                "payload": hit.payload
            }
            for hit in results
        ]

    def get_all_embeddings(self, collection_name: str, limit: int = 100, offset: Optional[str] = None, include_vectors: bool = False) -> Dict[str, Any]:
        try:
            logger.info(f"Retrieving embeddings from collection '{collection_name}' with limit={limit}, include_vectors={include_vectors}")

            collection_info = self.client.get_collection(collection_name)
            total_count = collection_info.points_count

            result = self.client.scroll(
                collection_name=collection_name,
                limit=limit,
                offset=offset,
                with_vectors=include_vectors,
                with_payload=True
            )

            points, next_offset = result
            embeddings = self._format_embeddings(points, include_vectors)

            logger.info(f"Retrieved {len(embeddings)} embeddings from collection '{collection_name}'")

            return {
                "embeddings": embeddings,
                "next_offset": next_offset,
                "total_count": total_count,
                "has_more": next_offset is not None,
                "collection_info": {
                    "name": collection_name,
                    "points_count": total_count,
                    "vectors_count": collection_info.vectors_count if hasattr(collection_info, 'vectors_count') else total_count
                }
            }

        except Exception as e:
            logger.error(f"Failed to retrieve embeddings from collection '{collection_name}': {str(e)}")
            logger.exception("Full exception details:")
            return {
                "embeddings": [],
                "next_offset": None,
                "total_count": 0,
                "has_more": False,
                "collection_info": {},
                "error": str(e)
            }

    def _format_embeddings(self, points: List[Any], include_vectors: bool) -> List[Dict[str, Any]]:
        embeddings = []
        for point in points:
            embedding_item = {
                "id": point.id,
                "document_id": point.payload.get("document_id", ""),
                "text": point.payload.get("text", ""),
                "source": point.payload.get("source", ""),
                "metadata": point.payload.get("metadata", {})
            }

            if include_vectors and point.vector:
                embedding_item["vector"] = point.vector

            embeddings.append(embedding_item)
        return embeddings

    def batch_read_files(self, collection_name: str, document_ids: List[str]) -> Dict[str, Any]:
        try:
            logger.debug(f"Checking status of {len(document_ids)} documents in collection '{collection_name}'")
            return self._check_documents_status(collection_name, document_ids)
        except Exception as e:
            if "Index required" in str(e):
                logger.warning(f"Missing index detected for collection '{collection_name}', attempting to create indexes")
                if self.ensure_indexes(collection_name):
                    logger.info(f"Indexes created, retrying batch_read_files for collection '{collection_name}'")
                    try:
                        return self._check_documents_status(collection_name, document_ids)
                    except Exception as retry_error:
                        logger.error(f"Error checking file status after creating indexes: {retry_error}")
                        return {doc_id: "error" for doc_id in document_ids}

            logger.error(f"Failed to check file status in collection '{collection_name}': {str(e)}")
            logger.exception("Full exception details:")
            return {doc_id: "error" for doc_id in document_ids}

    def _check_documents_status(self, collection_name: str, document_ids: List[str]) -> Dict[str, str]:
        status = {}
        for doc_id in document_ids:
            logger.debug(f"Checking if document {doc_id} exists in collection '{collection_name}'")
            results = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[FieldCondition(key="document_id", match={"value": doc_id})]
                ),
                limit=1
            )
            status[doc_id] = "indexed" if results[0] else "not_found"
            logger.debug(f"Document {doc_id} status: {status[doc_id]}")
        return status

    def scroll_by_filter(self, collection_name: str, filters: Dict[str, str], limit: int = 100) -> List[Dict]:
        """
        Scroll through points in a collection with filters.

        Args:
            collection_name: Name of the collection
            filters: Dictionary of field->value filters
            limit: Maximum number of points to return

        Returns:
            List of chunks with metadata
        """
        try:
            # Build filter conditions
            must_conditions = []
            for key, value in filters.items():
                must_conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )

            # Scroll with filter
            results, _ = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=must_conditions) if must_conditions else None,
                limit=limit,
                with_payload=True,
                with_vectors=False
            )

            # Format results
            chunks = []
            for point in results:
                chunk_data = {
                    "id": point.id,
                    "text": point.payload.get("text", ""),
                    "metadata": point.payload.get("metadata", {}),
                    "chunk_id": point.payload.get("chunk_id", ""),
                    "document_id": point.payload.get("document_id", "")
                }
                chunks.append(chunk_data)

            return chunks

        except Exception as e:
            logger.error(f"Error scrolling collection {collection_name}: {e}")
            return []