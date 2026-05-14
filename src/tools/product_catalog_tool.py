"""
Product Catalog Search Tool

Provides semantic search capabilities for the Colt Product Catalog using Vertex AI Vector Search.
"""

import google.cloud.aiplatform as aiplatform
from vertexai.language_models import TextEmbeddingModel
from typing import List
from ..core.config import settings

# Initialize AI Platform
aiplatform.init(
    project=settings.GOOGLE_CLOUD_PROJECT, location=settings.GOOGLE_CLOUD_LOCATION
)


def get_query_embedding(query: str) -> List[float]:
    """Converts the search query into a vector."""
    model = TextEmbeddingModel.from_pretrained(settings.VECTOR_SEARCH_EMBEDDING_MODEL)
    embeddings = model.get_embeddings([query])
    return embeddings[0].values


def colt_product_search(query: str) -> str:
    """
    ADK-compatible tool to search the Colt Product Catalog using Vertex AI Vector Search.

    Args:
        query: The customer need or keyword to search for (e.g., 'high bandwidth cloud connect')
    """
    try:
        # Initialize the Index Endpoint
        index_endpoint = aiplatform.MatchingEngineIndexEndpoint(
            settings.VECTOR_SEARCH_INDEX_ENDPOINT_ID
        )

        # 1. Embed the user's query
        query_vector = get_query_embedding(query)

        # 2. Perform the Vector Search
        # We ask for the top 5 most relevant product segments
        response = index_endpoint.find_neighbors(
            deployed_index_id=settings.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
            queries=[query_vector],
            num_neighbors=5,
        )

        # 3. Parse the results
        # In a real RAG setup, you would use the IDs returned to fetch the
        # actual text from a database (Firestore/BigQuery) or GCS.
        results = []
        for neighbor in response[0]:
            # Example: 'neighbor.id' would link to the text chunk in your storage
            results.append(
                f"Match found in Catalog (ID: {neighbor.id}, Distance: {neighbor.distance})"
            )

        if not results:
            return "No matching products found in the Colt Catalog for this query."

        return "\n\n".join(results)
    except Exception as e:
        return f"Error performing product search: {str(e)}"
