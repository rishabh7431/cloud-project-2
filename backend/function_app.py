import io
import json
import logging
import os
import time
from datetime import datetime, timezone

import azure.functions as func
import pandas as pd
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()


# -----------------------------------------------------------------------------
# Common helpers
# -----------------------------------------------------------------------------

def json_response(data, status_code=200):
    """Return a JSON response that the frontend can access."""
    return func.HttpResponse(
        body=json.dumps(data, default=str),
        status_code=status_code,
        mimetype="application/json",
        charset="utf-8",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
    )


def get_blob_service_client():
    """Create the Blob Storage client used by all backend functions."""
    connection_string = os.getenv("DIET_STORAGE_CONNECTION_STRING")

    if not connection_string:
        raise ValueError(
            "DIET_STORAGE_CONNECTION_STRING is not configured."
        )

    return BlobServiceClient.from_connection_string(connection_string)


# -----------------------------------------------------------------------------
# Source data cleaning
# Source: diet-data/All_Diets.csv
# -----------------------------------------------------------------------------

def load_and_clean_dataset():
    """Download All_Diets.csv from Blob Storage and clean it once."""
    container_name = os.getenv("DIET_CONTAINER_NAME", "diet-data")
    blob_name = os.getenv("DIET_BLOB_NAME", "All_Diets.csv")

    blob_service_client = get_blob_service_client()
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=blob_name,
    )

    logging.info("Downloading source dataset: %s/%s", container_name, blob_name)

    blob_data = blob_client.download_blob().readall()
    df = pd.read_csv(io.BytesIO(blob_data))

    required_columns = [
        "Diet_type",
        "Recipe_name",
        "Cuisine_type",
        "Protein(g)",
        "Carbs(g)",
        "Fat(g)",
    ]

    missing_columns = [
        column for column in required_columns if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required dataset columns: {missing_columns}"
        )

    # Remove records without a diet type.
    df = df.dropna(subset=["Diet_type"]).copy()

    # Clean text columns.
    df["Diet_type"] = (
        df["Diet_type"]
        .astype(str)
        .str.strip()
        .str.title()
    )

    df["Recipe_name"] = (
        df["Recipe_name"]
        .fillna("Unknown Recipe")
        .astype(str)
        .str.strip()
    )

    df["Cuisine_type"] = (
        df["Cuisine_type"]
        .fillna("Unknown")
        .astype(str)
        .str.strip()
        .str.title()
    )

    nutrient_columns = ["Protein(g)", "Carbs(g)", "Fat(g)"]

    # Convert nutrient columns to numbers and replace missing values with means.
    for column in nutrient_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        column_mean = df[column].mean()

        if pd.isna(column_mean):
            column_mean = 0

        df[column] = df[column].fillna(column_mean)

    # Calculate ratios safely.
    safe_carbs = df["Carbs(g)"].replace(0, pd.NA)
    safe_fat = df["Fat(g)"].replace(0, pd.NA)

    df["Protein_to_Carbs_ratio"] = (
        df["Protein(g)"] / safe_carbs
    ).fillna(0).round(2)

    df["Carbs_to_Fat_ratio"] = (
        df["Carbs(g)"] / safe_fat
    ).fillna(0).round(2)

    logging.info("Dataset cleaning completed. Records: %s", len(df))

    return df, container_name, blob_name


# -----------------------------------------------------------------------------
# Processed dataset storage
# Destination: processed-data/Cleaned_All_Diets.csv
# -----------------------------------------------------------------------------

def save_cleaned_dataset(df):
    """Save the cleaned dataset to the processed-data container."""
    container_name = os.getenv(
        "PROCESSED_CONTAINER_NAME",
        "processed-data",
    )
    cleaned_blob_name = os.getenv(
        "CLEANED_BLOB_NAME",
        "Cleaned_All_Diets.csv",
    )

    blob_service_client = get_blob_service_client()
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cleaned_blob_name,
    )

    blob_client.upload_blob(
        df.to_csv(index=False),
        overwrite=True,
    )

    logging.info(
        "Cleaned dataset saved to %s/%s",
        container_name,
        cleaned_blob_name,
    )


def load_cleaned_dataset():
    """Load Cleaned_All_Diets.csv without cleaning the source file again."""
    container_name = os.getenv(
        "PROCESSED_CONTAINER_NAME",
        "processed-data",
    )
    cleaned_blob_name = os.getenv(
        "CLEANED_BLOB_NAME",
        "Cleaned_All_Diets.csv",
    )

    blob_service_client = get_blob_service_client()
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cleaned_blob_name,
    )

    blob_data = blob_client.download_blob().readall()
    return pd.read_csv(io.BytesIO(blob_data))


# -----------------------------------------------------------------------------
# Dashboard result calculation
# Calculated only when All_Diets.csv changes.
# -----------------------------------------------------------------------------

def calculate_dashboard_results(df, source_container, source_blob):
    """Calculate all visualization and cluster results for the cache."""
    calculation_started = time.perf_counter()
    nutrient_columns = ["Protein(g)", "Carbs(g)", "Fat(g)"]

    # Bar chart: average macronutrients by diet type.
    average_macros = (
        df.groupby("Diet_type")[nutrient_columns]
        .mean()
        .round(2)
        .reset_index()
    )

    # Scatter plot: top 200 recipes by protein.
    scatter_sample = (
        df[
            [
                "Diet_type",
                "Recipe_name",
                "Cuisine_type",
                "Protein(g)",
                "Carbs(g)",
                "Fat(g)",
            ]
        ]
        .sort_values("Protein(g)", ascending=False)
        .head(200)
        .round(
            {
                "Protein(g)": 2,
                "Carbs(g)": 2,
                "Fat(g)": 2,
            }
        )
    )

    # Heatmap: nutrient correlations.
    correlation = df[nutrient_columns].corr().round(3)
    heatmap_data = []

    for row_name in correlation.index:
        for column_name in correlation.columns:
            heatmap_data.append(
                {
                    "x": column_name,
                    "y": row_name,
                    "value": float(
                        correlation.loc[row_name, column_name]
                    ),
                }
            )

    # Pie chart: number of recipes per diet type.
    recipe_distribution = (
        df["Diet_type"]
        .value_counts()
        .rename_axis("Diet_type")
        .reset_index(name="Recipe_count")
    )

    # Top five protein recipes for every diet.
    top_protein_recipes = (
        df.sort_values("Protein(g)", ascending=False)
        .groupby("Diet_type", group_keys=False)
        .head(5)[
            [
                "Diet_type",
                "Recipe_name",
                "Cuisine_type",
                "Protein(g)",
            ]
        ]
        .round({"Protein(g)": 2})
    )

    # Most common cuisine for every diet.
    most_common_cuisines = (
        df.groupby("Diet_type")["Cuisine_type"]
        .agg(
            lambda values: (
                values.mode().iloc[0]
                if not values.mode().empty
                else "Unknown"
            )
        )
        .reset_index(name="Most_common_cuisine")
    )

    # Average nutrient ratios.
    average_ratios = (
        df.groupby("Diet_type")
        [
            [
                "Protein_to_Carbs_ratio",
                "Carbs_to_Fat_ratio",
            ]
        ]
        .mean()
        .round(2)
        .reset_index()
    )

    highest_protein_row = average_macros.loc[
        average_macros["Protein(g)"].idxmax()
    ]

    # Nutritional clusters. These are also cached so /clusters does not
    # recalculate them on every request.
    cluster_macros = average_macros.copy()

    def determine_cluster(row):
        nutrients = {
            "Protein Focused": row["Protein(g)"],
            "Carbohydrate Focused": row["Carbs(g)"],
            "Fat Focused": row["Fat(g)"],
        }
        return max(nutrients, key=nutrients.get)

    cluster_macros["Cluster"] = cluster_macros.apply(
        determine_cluster,
        axis=1,
    )

    clusters = []

    for cluster_name, group in cluster_macros.groupby("Cluster"):
        clusters.append(
            {
                "cluster_name": cluster_name,
                "diet_types": group["Diet_type"].tolist(),
                "average_protein_g": round(
                    float(group["Protein(g)"].mean()),
                    2,
                ),
                "average_carbs_g": round(
                    float(group["Carbs(g)"].mean()),
                    2,
                ),
                "average_fat_g": round(
                    float(group["Fat(g)"].mean()),
                    2,
                ),
            }
        )

    cache_generation_time = round(
        time.perf_counter() - calculation_started,
        3,
    )

    return {
        "status": "success",
        "metadata": {
            "container": source_container,
            "blob": source_blob,
            "total_recipes": int(len(df)),
            "total_diet_types": int(df["Diet_type"].nunique()),
            "cache_generated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "cache_generation_time_seconds": cache_generation_time,
        },
        "filters": {
            "diet_types": sorted(
                df["Diet_type"].dropna().unique().tolist()
            )
        },
        "bar_chart": {
            "title": "Average Macronutrient Content by Diet Type",
            "data": average_macros.to_dict(orient="records"),
        },
        "scatter_plot": {
            "title": "Protein versus Carbohydrates by Recipe",
            "data": scatter_sample.to_dict(orient="records"),
        },
        "heatmap": {
            "title": "Nutrient Correlations",
            "nutrients": nutrient_columns,
            "data": heatmap_data,
        },
        "pie_chart": {
            "title": "Recipe Distribution by Diet Type",
            "data": recipe_distribution.to_dict(orient="records"),
        },
        "average_macros": average_macros.to_dict(orient="records"),
        "top_protein_recipes": top_protein_recipes.to_dict(
            orient="records"
        ),
        "most_common_cuisines": most_common_cuisines.to_dict(
            orient="records"
        ),
        "average_ratios": average_ratios.to_dict(orient="records"),
        "highest_protein_diet": {
            "Diet_type": highest_protein_row["Diet_type"],
            "Average_protein_g": round(
                float(highest_protein_row["Protein(g)"]),
                2,
            ),
        },
        "clusters": clusters,
    }


# -----------------------------------------------------------------------------
# Dashboard cache storage
# Destination: dashboard-cache/dashboard_cache.json
# -----------------------------------------------------------------------------

def save_dashboard_cache(result):
    """Save pre-calculated dashboard results to dashboard-cache."""
    container_name = os.getenv(
        "DASHBOARD_CACHE_CONTAINER_NAME",
        "dashboard-cache",
    )
    cache_blob_name = os.getenv(
        "DASHBOARD_CACHE_BLOB_NAME",
        "dashboard_cache.json",
    )

    blob_service_client = get_blob_service_client()
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cache_blob_name,
    )

    blob_client.upload_blob(
        json.dumps(result, default=str),
        overwrite=True,
    )

    logging.info(
        "Dashboard cache saved to %s/%s",
        container_name,
        cache_blob_name,
    )


def load_dashboard_cache():
    """Load the pre-calculated dashboard cache."""
    container_name = os.getenv(
        "DASHBOARD_CACHE_CONTAINER_NAME",
        "dashboard-cache",
    )
    cache_blob_name = os.getenv(
        "DASHBOARD_CACHE_BLOB_NAME",
        "dashboard_cache.json",
    )

    blob_service_client = get_blob_service_client()
    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cache_blob_name,
    )

    blob_data = blob_client.download_blob().readall()
    return json.loads(blob_data.decode("utf-8"))


# -----------------------------------------------------------------------------
# Blob Trigger
# Watches: diet-data/All_Diets.csv
# -----------------------------------------------------------------------------

@app.blob_trigger(
    arg_name="blob",
    path="diet-data/All_Diets.csv",
    connection="DIET_STORAGE_CONNECTION_STRING",
)
def process_diet_file(blob: func.InputStream):
    """
    Run automatically when All_Diets.csv is uploaded or replaced.

    Workflow:
    1. Clean the source dataset once.
    2. Save processed-data/Cleaned_All_Diets.csv.
    3. Calculate dashboard results once.
    4. Save dashboard-cache/dashboard_cache.json.
    """
    logging.info(
        "All_Diets.csv changed. Starting Phase 3 processing."
    )
    logging.info(
        "Blob name: %s | Size: %s bytes",
        blob.name,
        blob.length,
    )

    try:
        df, source_container, source_blob = load_and_clean_dataset()

        logging.info(
            "Dataset cleaned successfully. %s records processed.",
            len(df),
        )

        save_cleaned_dataset(df)

        logging.info("Starting dashboard calculations.")
        dashboard_results = calculate_dashboard_results(
            df,
            source_container,
            source_blob,
        )

        save_dashboard_cache(dashboard_results)

        logging.info(
            "Phase 3 processing completed successfully."
        )

    except Exception:
        logging.exception(
            "Error while processing All_Diets.csv"
        )
        raise


# -----------------------------------------------------------------------------
# HTTP API: dashboard insights
# Reads dashboard-cache/dashboard_cache.json only.
# -----------------------------------------------------------------------------

@app.route(
    route="analyze_diets",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def analyze_diets(req: func.HttpRequest) -> func.HttpResponse:
    """Return cached dashboard visualization data."""
    if req.method == "OPTIONS":
        return json_response({})

    start_time = time.perf_counter()

    try:
        result = load_dashboard_cache()

        execution_time = round(
            time.perf_counter() - start_time,
            3,
        )

        # Preserve the field the existing frontend already displays.
        result.setdefault("metadata", {})[
            "execution_time_seconds"
        ] = execution_time
        result["metadata"]["data_source"] = (
            "dashboard-cache/dashboard_cache.json"
        )
        result["metadata"]["cache_hit"] = True

        return json_response(result)

    except Exception as error:
        logging.exception("Failed to load dashboard cache.")

        return json_response(
            {
                "status": "error",
                "message": (
                    "Dashboard cache is not available. "
                    "Upload or replace All_Diets.csv to generate it."
                ),
                "details": str(error),
            },
            status_code=500,
        )


# -----------------------------------------------------------------------------
# HTTP API: recipe search, diet filter and pagination
# Reads processed-data/Cleaned_All_Diets.csv only.
# -----------------------------------------------------------------------------

@app.route(
    route="recipes",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_recipes(req: func.HttpRequest) -> func.HttpResponse:
    """
    Return filtered and paginated recipes.

    Example:
    /api/recipes?diet_type=Vegan&search=soup&page=1&page_size=10
    """
    if req.method == "OPTIONS":
        return json_response({})

    start_time = time.perf_counter()

    try:
        df = load_cleaned_dataset()

        diet_type = req.params.get("diet_type", "all").strip()
        search = req.params.get("search", "").strip()

        try:
            page = max(int(req.params.get("page", "1")), 1)
        except ValueError:
            page = 1

        try:
            page_size = int(req.params.get("page_size", "10"))
        except ValueError:
            page_size = 10

        page_size = min(max(page_size, 1), 100)

        filtered_df = df.copy()

        # Diet type filter.
        if diet_type.lower() != "all":
            filtered_df = filtered_df[
                filtered_df["Diet_type"]
                .astype(str)
                .str.lower()
                == diet_type.lower()
            ]

        # Keyword search across diet type, recipe name and cuisine.
        if search:
            search_mask = (
                filtered_df["Diet_type"]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                    regex=False,
                )
                |
                filtered_df["Recipe_name"]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                    regex=False,
                )
                |
                filtered_df["Cuisine_type"]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                    regex=False,
                )
            )
            filtered_df = filtered_df[search_mask]

        total_items = int(len(filtered_df))
        total_pages = max(
            (total_items + page_size - 1) // page_size,
            1,
        )

        if page > total_pages:
            page = total_pages

        start_index = (page - 1) * page_size
        end_index = start_index + page_size

        recipe_columns = [
            "Diet_type",
            "Recipe_name",
            "Cuisine_type",
            "Protein(g)",
            "Carbs(g)",
            "Fat(g)",
            "Protein_to_Carbs_ratio",
            "Carbs_to_Fat_ratio",
        ]

        recipes = (
            filtered_df[recipe_columns]
            .iloc[start_index:end_index]
            .round(
                {
                    "Protein(g)": 2,
                    "Carbs(g)": 2,
                    "Fat(g)": 2,
                    "Protein_to_Carbs_ratio": 2,
                    "Carbs_to_Fat_ratio": 2,
                }
            )
        )

        execution_time = round(
            time.perf_counter() - start_time,
            3,
        )

        return json_response(
            {
                "status": "success",
                "data_source": (
                    "processed-data/Cleaned_All_Diets.csv"
                ),
                "filters": {
                    "diet_type": diet_type,
                    "search": search,
                },
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_items": total_items,
                    "total_pages": total_pages,
                    "has_previous": page > 1,
                    "has_next": page < total_pages,
                },
                "recipes": recipes.to_dict(orient="records"),
                "execution_time_seconds": execution_time,
            }
        )

    except Exception as error:
        logging.exception("The recipes function failed.")

        return json_response(
            {
                "status": "error",
                "message": str(error),
            },
            status_code=500,
        )


# -----------------------------------------------------------------------------
# HTTP API: nutritional clusters
# Reads dashboard-cache/dashboard_cache.json only.
# -----------------------------------------------------------------------------

@app.route(
    route="clusters",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_clusters(req: func.HttpRequest) -> func.HttpResponse:
    """Return cached nutritional clusters."""
    if req.method == "OPTIONS":
        return json_response({})

    start_time = time.perf_counter()

    try:
        cached_result = load_dashboard_cache()
        clusters = cached_result.get("clusters", [])

        execution_time = round(
            time.perf_counter() - start_time,
            3,
        )

        return json_response(
            {
                "status": "success",
                "data_source": (
                    "dashboard-cache/dashboard_cache.json"
                ),
                "clusters": clusters,
                "execution_time_seconds": execution_time,
            }
        )

    except Exception as error:
        logging.exception("The clusters function failed.")

        return json_response(
            {
                "status": "error",
                "message": str(error),
            },
            status_code=500,
        )