
import os
os.environ["GCE_METADATA_MTLS_MODE"] = "none"
from google.cloud import bigquery
from loguru import logger

# Mimic the app setup
os.environ["GOOGLE_CLOUD_PROJECT"] = "aicoesandox"

def test_bq():
    try:
        logger.info("Testing BigQuery Client initialization (with MTLS bypass)...")
        client = bigquery.Client(project="aicoesandox")
        logger.info("Client initialized successfully.")
        
        logger.info("Testing a simple query...")
        # Just check if we can reach the service
        query = "SELECT 1"
        query_job = client.query(query)
        results = list(query_job.result())
        logger.info(f"Query successful: {results}")
        
    except Exception as e:
        logger.error(f"BQ Test failed: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    test_bq()
