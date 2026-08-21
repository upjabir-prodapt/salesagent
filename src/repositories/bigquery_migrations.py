"""BigQuery schema migrations and table definitions."""

from google.cloud import bigquery


# BigQuery search cache table schema
SEARCH_CACHE_SCHEMA = [
    bigquery.SchemaField("company_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("query", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("query_hash", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("search_results", "STRING", mode="NULLABLE"),  # JSON as string
    bigquery.SchemaField("domain", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("search_date", "TIMESTAMP", mode="REQUIRED"),
]

# Cost attribution table schema (updated)
COST_ATTRIBUTION_SCHEMA = [
    bigquery.SchemaField("job_execution_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("username", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("email", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("business_unit", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("model_version", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("temperature", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("prompt_template_version", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("input_tokens", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("output_tokens", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("total_tokens", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("search_count", "INT64", mode="NULLABLE"),  # NEW FIELD
    bigquery.SchemaField("search_cost_usd", "FLOAT64", mode="NULLABLE"),  # NEW FIELD
    bigquery.SchemaField("token_cost_usd", "FLOAT64", mode="NULLABLE"),  # NEW FIELD
    bigquery.SchemaField("total_cost_usd", "FLOAT64", mode="NULLABLE"),  # NEW FIELD
    bigquery.SchemaField("latency_seconds", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("cost_usd", "FLOAT64", mode="NULLABLE"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
]


def create_search_cache_table(
    project: str, dataset: str, table_name: str = "search_cache"
) -> bigquery.Table:
    """Create search cache table schema."""
    table_id = f"{project}.{dataset}.{table_name}"
    table = bigquery.Table(table_id, schema=SEARCH_CACHE_SCHEMA)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="search_date",
    )
    table.clustering_fields = ["company_name", "domain"]
    return table


def migrate_cost_attribution_table(client: bigquery.Client, table_ref: str) -> None:
    """Migrate cost_attribution table to add new search cost fields."""
    try:
        # Get current table
        table = client.get_table(table_ref)
        original_schema = table.schema

        # Check if new fields exist
        field_names = {field.name for field in original_schema}

        new_fields = []
        if "search_count" not in field_names:
            new_fields.append(bigquery.SchemaField("search_count", "INT64", mode="NULLABLE"))
        if "search_cost_usd" not in field_names:
            new_fields.append(bigquery.SchemaField("search_cost_usd", "FLOAT64", mode="NULLABLE"))
        if "token_cost_usd" not in field_names:
            new_fields.append(bigquery.SchemaField("token_cost_usd", "FLOAT64", mode="NULLABLE"))
        if "total_cost_usd" not in field_names:
            new_fields.append(bigquery.SchemaField("total_cost_usd", "FLOAT64", mode="NULLABLE"))

        if new_fields:
            updated_schema = list(original_schema) + new_fields
            table.schema = updated_schema
            table = client.update_table(table, ["schema"])
            print(f"Updated {table_ref} schema with {len(new_fields)} new fields")
        else:
            print(f"{table_ref} already has all required fields")

    except Exception as e:
        print(f"Failed to migrate {table_ref}: {e}")
        raise


__all__ = [
    "SEARCH_CACHE_SCHEMA",
    "COST_ATTRIBUTION_SCHEMA",
    "create_search_cache_table",
    "migrate_cost_attribution_table",
]
