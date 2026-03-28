from qdrant_client.http.exceptions import UnexpectedResponse


class QdrantAlreadyExistsException(Exception):
    pass


class QdrantIndexRequiredException(Exception):
    pass


def handle_qdrant_exception(e: Exception):
    error_str = str(e).lower()

    if isinstance(e, UnexpectedResponse) and e.status_code == 409:
        raise QdrantAlreadyExistsException(str(e)) from e

    if "index required" in error_str:
        raise QdrantIndexRequiredException(str(e)) from e

    raise e