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


# ============================================================
# COMMON RESPONSE
# ============================================================

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
            "Access-Control-Allow-Headers": "Content-Type"
        }
    )


# ============================================================
# STORAGE HELPERS
# ============================================================

def get_blob_service_client():
    """Create a Blob Service client using the configured connection."""

    connection_string = os.getenv(
        "DIET_STORAGE_CONNECTION_STRING"
    )

    if not connection_string:
        raise ValueError(
            "DIET_STORAGE_CONNECTION_STRING is not configured."
        )

    return BlobServiceClient.from_connection_string(
        connection_string
    )


# ============================================================
# DATA CLEANING
# ============================================================

def load_and_clean_dataset():
    """
    Download All_Diets.csv from Azure Blob Storage
    and perform data cleaning.
    """

    container_name = os.getenv(
        "DIET_CONTAINER_NAME",
        "diet-data"
    )

    blob_name = os.getenv(
        "DIET_BLOB_NAME",
        "All_Diets.csv"
    )

    blob_service_client = get_blob_service_client()

    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=blob_name
    )

    logging.info(
        "Downloading source dataset: %s/%s",
        container_name,
        blob_name
    )

    blob_data = blob_client.download_blob().readall()

    df = pd.read_csv(
        io.BytesIO(blob_data)
    )

    required_columns = [
        "Diet_type",
        "Recipe_name",
        "Cuisine_type",
        "Protein(g)",
        "Carbs(g)",
        "Fat(g)"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required dataset columns: "
            f"{missing_columns}"
        )

    # --------------------------------------------------------
    # Remove records without diet type
    # --------------------------------------------------------

    df = df.dropna(
        subset=["Diet_type"]
    ).copy()

    # --------------------------------------------------------
    # Clean text columns
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Clean nutrient columns
    # --------------------------------------------------------

    nutrient_columns = [
        "Protein(g)",
        "Carbs(g)",
        "Fat(g)"
    ]

    for column in nutrient_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

        column_mean = df[column].mean()

        if pd.isna(column_mean):
            column_mean = 0

        df[column] = df[column].fillna(
            column_mean
        )

    # --------------------------------------------------------
    # Calculate nutrient ratios
    # --------------------------------------------------------

    safe_carbs = df["Carbs(g)"].replace(
        0,
        pd.NA
    )

    safe_fat = df["Fat(g)"].replace(
        0,
        pd.NA
    )

    df["Protein_to_Carbs_ratio"] = (
        df["Protein(g)"] / safe_carbs
    ).fillna(0).round(2)

    df["Carbs_to_Fat_ratio"] = (
        df["Carbs(g)"] / safe_fat
    ).fillna(0).round(2)

    logging.info(
        "Dataset cleaning completed. Records: %s",
        len(df)
    )

    return df, container_name, blob_name


# ============================================================
# CLEANED DATASET CACHE
# ============================================================

def save_cleaned_dataset(df):
    """
    Save the cleaned dataset to Blob Storage.

    This file is later used by the recipe API so the
    original CSV does not need to be cleaned again.
    """

    container_name = os.getenv(
        "DIET_CONTAINER_NAME",
        "diet-data"
    )

    cleaned_blob_name = os.getenv(
        "CLEANED_BLOB_NAME",
        "cleaned_diets.csv"
    )

    blob_service_client = get_blob_service_client()

    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cleaned_blob_name
    )

    csv_data = df.to_csv(
        index=False
    )

    blob_client.upload_blob(
        csv_data,
        overwrite=True
    )

    logging.info(
        "Cleaned dataset saved to %s/%s",
        container_name,
        cleaned_blob_name
    )


def load_cleaned_dataset():
    """
    Load the already-cleaned dataset from Blob Storage.

    API requests use this instead of cleaning
    All_Diets.csv again.
    """

    container_name = os.getenv(
        "DIET_CONTAINER_NAME",
        "diet-data"
    )

    cleaned_blob_name = os.getenv(
        "CLEANED_BLOB_NAME",
        "cleaned_diets.csv"
    )

    blob_service_client = get_blob_service_client()

    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cleaned_blob_name
    )

    blob_data = blob_client.download_blob().readall()

    df = pd.read_csv(
        io.BytesIO(blob_data)
    )

    return df


# ============================================================
# DASHBOARD RESULT CALCULATIONS
# ============================================================

def calculate_dashboard_results(
    df,
    container_name,
    blob_name
):
    """
    Calculate all dashboard visualization results.

    This function is called by the Blob Trigger,
    not by each dashboard request.
    """

    nutrient_columns = [
        "Protein(g)",
        "Carbs(g)",
        "Fat(g)"
    ]

    # --------------------------------------------------------
    # Average macronutrients
    # --------------------------------------------------------

    average_macros = (
        df.groupby(
            "Diet_type"
        )[nutrient_columns]
        .mean()
        .round(2)
        .reset_index()
    )

    # --------------------------------------------------------
    # Scatter plot
    # --------------------------------------------------------

    scatter_sample = (
        df[
            [
                "Diet_type",
                "Recipe_name",
                "Cuisine_type",
                "Protein(g)",
                "Carbs(g)",
                "Fat(g)"
            ]
        ]
        .sort_values(
            "Protein(g)",
            ascending=False
        )
        .head(200)
        .round(
            {
                "Protein(g)": 2,
                "Carbs(g)": 2,
                "Fat(g)": 2
            }
        )
    )

    # --------------------------------------------------------
    # Correlation heatmap
    # --------------------------------------------------------

    correlation = (
        df[nutrient_columns]
        .corr()
        .round(3)
    )

    heatmap_data = []

    for row_name in correlation.index:

        for column_name in correlation.columns:

            heatmap_data.append(
                {
                    "x": column_name,
                    "y": row_name,
                    "value": float(
                        correlation.loc[
                            row_name,
                            column_name
                        ]
                    )
                }
            )

    # --------------------------------------------------------
    # Recipe distribution
    # --------------------------------------------------------

    recipe_distribution = (
        df["Diet_type"]
        .value_counts()
        .rename_axis(
            "Diet_type"
        )
        .reset_index(
            name="Recipe_count"
        )
    )

    # --------------------------------------------------------
    # Top protein recipes
    # --------------------------------------------------------

    top_protein_recipes = (
        df.sort_values(
            "Protein(g)",
            ascending=False
        )
        .groupby(
            "Diet_type",
            group_keys=False
        )
        .head(5)
        [
            [
                "Diet_type",
                "Recipe_name",
                "Cuisine_type",
                "Protein(g)"
            ]
        ]
        .round(
            {
                "Protein(g)": 2
            }
        )
    )

    # --------------------------------------------------------
    # Most common cuisine
    # --------------------------------------------------------

    most_common_cuisines = (
        df.groupby(
            "Diet_type"
        )["Cuisine_type"]
        .agg(
            lambda values:
            values.mode().iloc[0]
            if not values.mode().empty
            else "Unknown"
        )
        .reset_index(
            name="Most_common_cuisine"
        )
    )

    # --------------------------------------------------------
    # Average nutrient ratios
    # --------------------------------------------------------

    average_ratios = (
        df.groupby(
            "Diet_type"
        )
        [
            [
                "Protein_to_Carbs_ratio",
                "Carbs_to_Fat_ratio"
            ]
        ]
        .mean()
        .round(2)
        .reset_index()
    )

    # --------------------------------------------------------
    # Highest protein diet
    # --------------------------------------------------------

    highest_protein_row = average_macros.loc[
        average_macros[
            "Protein(g)"
        ].idxmax()
    ]

    # --------------------------------------------------------
    # Nutritional clusters
    # --------------------------------------------------------

    cluster_macros = (
        average_macros.copy()
    )

    def determine_cluster(row):

        nutrients = {
            "Protein Focused":
                row["Protein(g)"],

            "Carbohydrate Focused":
                row["Carbs(g)"],

            "Fat Focused":
                row["Fat(g)"]
        }

        return max(
            nutrients,
            key=nutrients.get
        )

    cluster_macros["Cluster"] = (
        cluster_macros.apply(
            determine_cluster,
            axis=1
        )
    )

    clusters = []

    for cluster_name, group in (
        cluster_macros.groupby(
            "Cluster"
        )
    ):

        clusters.append(
            {
                "cluster_name":
                    cluster_name,

                "diet_types":
                    group[
                        "Diet_type"
                    ].tolist(),

                "average_protein_g":
                    round(
                        float(
                            group[
                                "Protein(g)"
                            ].mean()
                        ),
                        2
                    ),

                "average_carbs_g":
                    round(
                        float(
                            group[
                                "Carbs(g)"
                            ].mean()
                        ),
                        2
                    ),

                "average_fat_g":
                    round(
                        float(
                            group[
                                "Fat(g)"
                            ].mean()
                        ),
                        2
                    )
            }
        )

    # --------------------------------------------------------
    # Complete cached response
    # --------------------------------------------------------

    result = {
        "status": "success",

        "metadata": {
            "container":
                container_name,

            "blob":
                blob_name,

            "total_recipes":
                int(
                    len(df)
                ),

            "total_diet_types":
                int(
                    df[
                        "Diet_type"
                    ].nunique()
                ),

            "cache_generated_at":
                datetime.now(
                    timezone.utc
                ).isoformat()
        },

        "filters": {
            "diet_types":
                sorted(
                    df[
                        "Diet_type"
                    ]
                    .dropna()
                    .unique()
                    .tolist()
                )
        },

        "bar_chart": {
            "title":
                "Average Macronutrient "
                "Content by Diet Type",

            "data":
                average_macros.to_dict(
                    orient="records"
                )
        },

        "scatter_plot": {
            "title":
                "Protein versus "
                "Carbohydrates by Recipe",

            "data":
                scatter_sample.to_dict(
                    orient="records"
                )
        },

        "heatmap": {
            "title":
                "Nutrient Correlations",

            "nutrients":
                nutrient_columns,

            "data":
                heatmap_data
        },

        "pie_chart": {
            "title":
                "Recipe Distribution "
                "by Diet Type",

            "data":
                recipe_distribution.to_dict(
                    orient="records"
                )
        },

        "average_macros":
            average_macros.to_dict(
                orient="records"
            ),

        "top_protein_recipes":
            top_protein_recipes.to_dict(
                orient="records"
            ),

        "most_common_cuisines":
            most_common_cuisines.to_dict(
                orient="records"
            ),

        "average_ratios":
            average_ratios.to_dict(
                orient="records"
            ),

        "highest_protein_diet": {
            "Diet_type":
                highest_protein_row[
                    "Diet_type"
                ],

            "Average_protein_g":
                round(
                    float(
                        highest_protein_row[
                            "Protein(g)"
                        ]
                    ),
                    2
                )
        },

        "clusters":
            clusters
    }

    logging.info(
        "Dashboard calculations completed."
    )

    return result


# ============================================================
# DASHBOARD CACHE STORAGE
# ============================================================

def save_dashboard_cache(result):
    """Save calculated dashboard results to Blob Storage."""

    container_name = os.getenv(
        "DIET_CONTAINER_NAME",
        "diet-data"
    )

    cache_blob_name = os.getenv(
        "DASHBOARD_CACHE_BLOB_NAME",
        "dashboard_cache.json"
    )

    blob_service_client = get_blob_service_client()

    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cache_blob_name
    )

    cache_data = json.dumps(
        result,
        default=str
    )

    blob_client.upload_blob(
        cache_data,
        overwrite=True
    )

    logging.info(
        "Dashboard cache saved to %s/%s",
        container_name,
        cache_blob_name
    )


def load_dashboard_cache():
    """
    Load pre-calculated visualization data
    from Blob Storage.
    """

    container_name = os.getenv(
        "DIET_CONTAINER_NAME",
        "diet-data"
    )

    cache_blob_name = os.getenv(
        "DASHBOARD_CACHE_BLOB_NAME",
        "dashboard_cache.json"
    )

    blob_service_client = get_blob_service_client()

    blob_client = blob_service_client.get_blob_client(
        container=container_name,
        blob=cache_blob_name
    )

    blob_data = blob_client.download_blob().readall()

    return json.loads(
        blob_data.decode("utf-8")
    )


# ============================================================
# BLOB TRIGGER
# ============================================================

@app.blob_trigger(
    arg_name="blob",
    path="diet-data/All_Diets.csv",
    connection="DIET_STORAGE_CONNECTION_STRING"
)
def process_diet_file(
    blob: func.InputStream
):
    """
    Run automatically when All_Diets.csv changes.

    Phase 3 workflow:

    1. All_Diets.csv changes
    2. Clean dataset once
    3. Save cleaned_diets.csv
    4. Calculate dashboard results once
    5. Save dashboard_cache.json
    """

    logging.info(
        "All_Diets.csv changed. "
        "Starting Phase 3 processing."
    )

    logging.info(
        "Blob name: %s | Size: %s bytes",
        blob.name,
        blob.length
    )

    try:

        # ----------------------------------------------------
        # Clean the source dataset once
        # ----------------------------------------------------

        df, container_name, blob_name = (
            load_and_clean_dataset()
        )

        logging.info(
            "Dataset cleaned successfully. "
            "%s records processed.",
            len(df)
        )

        # ----------------------------------------------------
        # Save cleaned dataset
        # ----------------------------------------------------

        save_cleaned_dataset(
            df
        )

        # ----------------------------------------------------
        # Calculate visualization results once
        # ----------------------------------------------------

        logging.info(
            "Starting dashboard calculations."
        )

        dashboard_results = (
            calculate_dashboard_results(
                df,
                container_name,
                blob_name
            )
        )

        # ----------------------------------------------------
        # Save calculated results
        # ----------------------------------------------------

        save_dashboard_cache(
            dashboard_results
        )

        logging.info(
            "Phase 3 processing completed successfully."
        )

        logging.info(
            "Cleaned dataset and dashboard cache updated."
        )

    except Exception:

        logging.exception(
            "Error while processing All_Diets.csv"
        )

        raise


# ============================================================
# ANALYZE DIETS API
# ============================================================

@app.route(
    route="analyze_diets",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS
)
def analyze_diets(
    req: func.HttpRequest
) -> func.HttpResponse:
    """
    Return pre-calculated dashboard results.

    The calculations are NOT performed here.
    They were already performed by the Blob Trigger.
    """

    if req.method == "OPTIONS":
        return json_response({})

    start_time = time.perf_counter()

    try:

        # Load cached calculations only
        result = load_dashboard_cache()

        execution_time = round(
            time.perf_counter()
            - start_time,
            3
        )

        # Add API response time without changing
        # when the actual calculations happened.
        result["metadata"][
            "api_response_time_seconds"
        ] = execution_time

        result["metadata"][
            "data_source"
        ] = "dashboard_cache.json"

        return json_response(
            result
        )

    except Exception as error:

        logging.exception(
            "Failed to load dashboard cache."
        )

        return json_response(
            {
                "status": "error",

                "message": (
                    "Dashboard cache is not available. "
                    "Upload or replace All_Diets.csv "
                    "to generate the Phase 3 cache."
                ),

                "details":
                    str(error)
            },
            status_code=500
        )


# ============================================================
# RECIPE API
# ============================================================

@app.route(
    route="recipes",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS
)
def get_recipes(
    req: func.HttpRequest
) -> func.HttpResponse:
    """
    Return filtered and paginated recipes.

    Example:

    /api/recipes
        ?diet_type=Vegan
        &search=soup
        &page=1
        &page_size=10

    This endpoint uses cleaned_diets.csv.
    It does not clean All_Diets.csv again.
    """

    if req.method == "OPTIONS":
        return json_response({})

    start_time = time.perf_counter()

    try:

        # ----------------------------------------------------
        # Load already-cleaned data
        # ----------------------------------------------------

        df = load_cleaned_dataset()

        # ----------------------------------------------------
        # Query parameters
        # ----------------------------------------------------

        diet_type = req.params.get(
            "diet_type",
            "all"
        ).strip()

        search = req.params.get(
            "search",
            ""
        ).strip()

        # ----------------------------------------------------
        # Page
        # ----------------------------------------------------

        try:

            page = max(
                int(
                    req.params.get(
                        "page",
                        "1"
                    )
                ),
                1
            )

        except ValueError:

            page = 1

        # ----------------------------------------------------
        # Page size
        # ----------------------------------------------------

        try:

            page_size = int(
                req.params.get(
                    "page_size",
                    "10"
                )
            )

        except ValueError:

            page_size = 10

        page_size = min(
            max(
                page_size,
                1
            ),
            100
        )

        # ----------------------------------------------------
        # Start filtering
        # ----------------------------------------------------

        filtered_df = df.copy()

        # ----------------------------------------------------
        # Diet type filter
        # ----------------------------------------------------

        if diet_type.lower() != "all":

            filtered_df = filtered_df[
                filtered_df[
                    "Diet_type"
                ]
                .astype(str)
                .str.lower()
                ==
                diet_type.lower()
            ]

        # ----------------------------------------------------
        # Keyword search
        # ----------------------------------------------------

        if search:

            search_mask = (

                filtered_df[
                    "Diet_type"
                ]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                    regex=False
                )

                |

                filtered_df[
                    "Recipe_name"
                ]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                    regex=False
                )

                |

                filtered_df[
                    "Cuisine_type"
                ]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                    regex=False
                )
            )

            filtered_df = filtered_df[
                search_mask
            ]

        # ----------------------------------------------------
        # Pagination calculation
        # ----------------------------------------------------

        total_items = int(
            len(filtered_df)
        )

        total_pages = max(
            (
                total_items
                + page_size
                - 1
            )
            // page_size,
            1
        )

        if page > total_pages:
            page = total_pages

        start_index = (
            page - 1
        ) * page_size

        end_index = (
            start_index
            + page_size
        )

        # ----------------------------------------------------
        # Page result
        # ----------------------------------------------------

        recipe_columns = [
            "Diet_type",
            "Recipe_name",
            "Cuisine_type",
            "Protein(g)",
            "Carbs(g)",
            "Fat(g)",
            "Protein_to_Carbs_ratio",
            "Carbs_to_Fat_ratio"
        ]

        recipes = (
            filtered_df[
                recipe_columns
            ]
            .iloc[
                start_index:
                end_index
            ]
            .round(
                {
                    "Protein(g)": 2,
                    "Carbs(g)": 2,
                    "Fat(g)": 2,
                    "Protein_to_Carbs_ratio": 2,
                    "Carbs_to_Fat_ratio": 2
                }
            )
        )

        execution_time = round(
            time.perf_counter()
            - start_time,
            3
        )

        return json_response(
            {
                "status":
                    "success",

                "data_source":
                    "cleaned_diets.csv",

                "filters": {
                    "diet_type":
                        diet_type,

                    "search":
                        search
                },

                "pagination": {
                    "page":
                        page,

                    "page_size":
                        page_size,

                    "total_items":
                        total_items,

                    "total_pages":
                        total_pages,

                    "has_previous":
                        page > 1,

                    "has_next":
                        page < total_pages
                },

                "recipes":
                    recipes.to_dict(
                        orient="records"
                    ),

                "execution_time_seconds":
                    execution_time
            }
        )

    except Exception as error:

        logging.exception(
            "The recipes function failed."
        )

        return json_response(
            {
                "status":
                    "error",

                "message":
                    str(error)
            },
            status_code=500
        )


# ============================================================
# CLUSTERS API
# ============================================================

@app.route(
    route="clusters",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS
)
def get_clusters(
    req: func.HttpRequest
) -> func.HttpResponse:
    """
    Return nutritional groups.

    Cluster calculations are also read from
    dashboard_cache.json instead of being
    calculated again on every request.
    """

    if req.method == "OPTIONS":
        return json_response({})

    start_time = time.perf_counter()

    try:

        cached_result = (
            load_dashboard_cache()
        )

        clusters = cached_result.get(
            "clusters",
            []
        )

        execution_time = round(
            time.perf_counter()
            - start_time,
            3
        )

        return json_response(
            {
                "status":
                    "success",

                "data_source":
                    "dashboard_cache.json",

                "clusters":
                    clusters,

                "execution_time_seconds":
                    execution_time
            }
        )

    except Exception as error:

        logging.exception(
            "The clusters function failed."
        )

        return json_response(
            {
                "status":
                    "error",

                "message":
                    str(error)
            },
            status_code=500
        )