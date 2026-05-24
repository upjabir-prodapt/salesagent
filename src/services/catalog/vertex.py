"""Vertex AI Matching Engine index update and deployment."""

from __future__ import annotations

import time

import google.cloud.aiplatform as aiplatform
from google.cloud.aiplatform import matching_engine

from ...core.config import Settings
from ...core.logging_config import logger
from .embeddings import init_vertex


class VertexIndexManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        init_vertex(settings)

    def get_index(self) -> aiplatform.MatchingEngineIndex:
        return aiplatform.MatchingEngineIndex(
            index_name=self._settings.VECTOR_SEARCH_INDEX_ID,
        )

    def get_endpoint(self) -> aiplatform.MatchingEngineIndexEndpoint:
        return aiplatform.MatchingEngineIndexEndpoint(
            index_endpoint_name=self._settings.VECTOR_SEARCH_INDEX_ENDPOINT_ID,
        )

    def vector_count(self) -> int:
        index = self.get_index()
        stats = getattr(index.gca_resource, "index_stats", None)
        if stats and stats.vectors_count:
            return int(stats.vectors_count)
        return 0

    def is_deployed(self) -> bool:
        endpoint = self.get_endpoint()
        deployed = getattr(endpoint.gca_resource, "deployed_indexes", None) or []
        return any(
            d.id == self._settings.VECTOR_SEARCH_DEPLOYED_INDEX_ID for d in deployed
        )

    def create_index(self, *, initial_embeddings_uri: str) -> aiplatform.MatchingEngineIndex:
        distance = getattr(
            matching_engine.matching_engine_index_config.DistanceMeasureType,
            self._settings.VECTOR_SEARCH_DISTANCE_MEASURE_TYPE,
            self._settings.VECTOR_SEARCH_DISTANCE_MEASURE_TYPE,
        )
        logger.info(
            "Creating index %s", self._settings.VECTOR_SEARCH_INDEX_DISPLAY_NAME
        )
        return aiplatform.MatchingEngineIndex.create_tree_ah_index(
            display_name=self._settings.VECTOR_SEARCH_INDEX_DISPLAY_NAME,
            contents_delta_uri=initial_embeddings_uri,
            dimensions=self._settings.VECTOR_SEARCH_EMBEDDING_DIMENSIONS,
            approximate_neighbors_count=self._settings.VECTOR_SEARCH_APPROXIMATE_NEIGHBORS_COUNT,
            leaf_node_embedding_count=self._settings.VECTOR_SEARCH_LEAF_NODE_EMBEDDING_COUNT,
            leaf_nodes_to_search_percent=self._settings.VECTOR_SEARCH_LEAF_NODES_TO_SEARCH_PERCENT,
            distance_measure_type=distance,
            index_update_method="BATCH_UPDATE",
            description="Colt product catalog vector index",
        )

    def update_index(
        self,
        embeddings_delta_uri: str,
        *,
        complete_overwrite: bool = True,
    ) -> int:
        index = self.get_index()
        previous = self.vector_count()
        logger.info(
            "Updating index %s (overwrite=%s, was %s vectors)",
            self._settings.VECTOR_SEARCH_INDEX_ID,
            complete_overwrite,
            previous,
        )
        index.update_embeddings(
            contents_delta_uri=embeddings_delta_uri,
            is_complete_overwrite=complete_overwrite,
        )
        return self.wait_for_update(expected_count=None, previous_count=previous)

    def wait_for_update(
        self,
        *,
        expected_count: int | None,
        previous_count: int,
    ) -> int:
        deadline = time.time() + self._settings.VECTOR_SEARCH_INDEX_UPDATE_TIMEOUT_SEC
        interval = self._settings.VECTOR_SEARCH_INDEX_UPDATE_POLL_INTERVAL_SEC
        last = previous_count

        while time.time() < deadline:
            last = self.vector_count()
            logger.info("Index vectors: %s (was %s)", last, previous_count)
            if expected_count is not None and last == expected_count:
                return last
            if last != previous_count and last > 0:
                return last
            time.sleep(interval)

        raise TimeoutError(
            f"Index update timed out after "
            f"{self._settings.VECTOR_SEARCH_INDEX_UPDATE_TIMEOUT_SEC}s "
            f"(vectors={last})"
        )

    def deploy(self, *, force: bool = False) -> None:
        if self.is_deployed() and not force:
            logger.info(
                "Index already deployed as %s on endpoint %s",
                self._settings.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
                self._settings.VECTOR_SEARCH_INDEX_ENDPOINT_ID,
            )
            return

        index = self.get_index()
        endpoint = self.get_endpoint()
        logger.info(
            "Deploying index to endpoint %s as %s",
            self._settings.VECTOR_SEARCH_INDEX_ENDPOINT_ID,
            self._settings.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
        )
        endpoint.deploy_index(
            index=index,
            deployed_index_id=self._settings.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
            display_name=f"{self._settings.VECTOR_SEARCH_INDEX_DISPLAY_NAME} (deployed)",
            machine_type=self._settings.VECTOR_SEARCH_DEPLOY_MACHINE_TYPE,
            min_replica_count=self._settings.VECTOR_SEARCH_DEPLOY_MIN_REPLICAS,
            max_replica_count=self._settings.VECTOR_SEARCH_DEPLOY_MAX_REPLICAS,
        )
        logger.info("Deploy operation submitted")
