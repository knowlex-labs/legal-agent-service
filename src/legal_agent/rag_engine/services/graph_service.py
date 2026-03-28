
import logging
import threading
from neo4j import GraphDatabase
from legal_agent.rag_engine.config import Config

logger = logging.getLogger(__name__)

class GraphService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(GraphService, cls).__new__(cls)
                    cls._instance._driver = None
                    cls._instance._initialized = False
        return cls._instance

    def _init_driver(self):
        """Initialize Neo4j driver following official AuraDB guide exactly."""
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            uri = Config.neo4j.URI
            user = Config.neo4j.USER
            password = Config.neo4j.PASSWORD

            logger.info(f"Initializing Neo4j driver: URI={uri}, User={user}")

            try:
                # Follow official Neo4j AuraDB guide exactly - no custom configuration
                # https://console-preview.neo4j.io/projects/.../developer-hub
                self._driver = GraphDatabase.driver(uri, auth=(user, password))

                # Verify connectivity immediately as per official guide
                self._driver.verify_connectivity()

                logger.info("Neo4j driver initialized and connectivity verified.")
                self._initialized = True

            except Exception as e:
                error_type = type(e).__name__
                error_msg = str(e)
                logger.error(f"Failed to initialize Neo4j driver: [{error_type}] {error_msg}")
                logger.error(f"Connection details - URI: {uri}, User: {user}, Password set: {bool(password)}")
                self._driver = None
                self._initialized = False
                # Raise the exception to surface connection issues immediately
                raise e

    def _ensure_driver(self):
        """Ensure driver is initialized before use."""
        if not self._initialized:
            self._init_driver()
        return self._driver

    def verify_connection(self):
        if self._driver:
            self._driver.verify_connectivity()

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None
            self._initialized = False

    def execute_query(self, query: str, parameters: dict = None, db: str = None):
        """Execute a Cypher query using official Neo4j guide format."""
        driver = self._ensure_driver()
        if not driver:
            error_msg = "Neo4j driver is not initialized. Check connection credentials and network access."
            logger.error(error_msg)
            raise ConnectionError(error_msg)

        database = db or Config.neo4j.DATABASE
        try:
            # Use official guide format: driver.execute_query() with database_= parameter
            records, summary, keys = driver.execute_query(
                query,
                database_=database,
                **parameters if parameters else {}
            )
            return records
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"Query execution failed: [{error_type}] {e}")
            logger.error(f"Database: {database}, Query preview: {query[:100]}...")
            raise e

    def flush_database(self):
        """Dangerous: Clears the entire database."""
        query = "MATCH (n) DETACH DELETE n"
        self.execute_query(query)

def get_graph_service():
    """Get the singleton GraphService instance."""
    return GraphService()
