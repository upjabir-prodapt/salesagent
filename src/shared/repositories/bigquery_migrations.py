"""BigQuery schema migrations and table definitions."""

from google.cloud import bigquery

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
            new_fields.append(
                bigquery.SchemaField("search_count", "INT64", mode="NULLABLE")
            )
        if "search_cost_usd" not in field_names:
            new_fields.append(
                bigquery.SchemaField("search_cost_usd", "FLOAT64", mode="NULLABLE")
            )
        if "token_cost_usd" not in field_names:
            new_fields.append(
                bigquery.SchemaField("token_cost_usd", "FLOAT64", mode="NULLABLE")
            )
        if "total_cost_usd" not in field_names:
            new_fields.append(
                bigquery.SchemaField("total_cost_usd", "FLOAT64", mode="NULLABLE")
            )

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
    "COST_ATTRIBUTION_SCHEMA",
    "migrate_cost_attribution_table",
]
