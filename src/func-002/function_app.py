import logging
import os
import re
from time import sleep
from typing import Any

import azure.functions as func
import httpx
import pandas as pd
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from openai import AzureOpenAI
from sqlalchemy import create_engine
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import SQLAlchemyError


BATCH_UPLOAD_SIZE = int(os.getenv("BATCH_UPLOAD_SIZE", 100))
TIMEOUT = int(os.getenv("TIMEOUT", 160))  # seconds
SLEEP_RATE_LIMIT = int(os.getenv("SLEEP_RATE_LIMIT", 60))  # seconds

# Configuration of clients and environment variables
httpx_client = httpx.Client(verify=False)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")

# Azure Search and OpenAI configuration
client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_KEY"),
    api_version=os.getenv("AZURE_OPENAI_VERSION"),
    http_client=httpx_client,
)

search_client = SearchClient(
    endpoint=os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT"),
    index_name=os.getenv("AZURE_SEARCH_INDEX_NAME"),
    credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_API_KEY")),
)

# PostgreSQL database connection
DATABASE_URL = URL.create(
    drivername="postgresql",
    username=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    host=os.getenv("DB_HOST"),
    database=os.getenv("DB_NAME"),
    port=os.getenv("DB_PORT", 5432),
)
SQL_QUERY = os.getenv("SQL_QUERY", 'SELECT * FROM "ViewAllRex";')

app = func.FunctionApp()


def clean_string(s: Any) -> Any:
    if isinstance(s, str):
        return re.sub(r"[\x00-\x1F\x7F]", "", s)
    return s


def fetch_data_from_db() -> pd.DataFrame | None:
    logging.info("Fetching data from the database.")
    try:
        engine = create_engine(DATABASE_URL)
        logging.info(f"Connecting to database: {DATABASE_URL} ...")
        with engine.connect() as connection:
            logging.info("Connection established.")

            # Fetch data into a DataFrame
            rexs_df = pd.read_sql(SQL_QUERY, connection)
            logging.info("Data extraction succeeded.")
            return rexs_df
    except SQLAlchemyError as error:
        logging.error(f"Database connection error: {error}")
        return None


def fetch_and_clean_data() -> pd.DataFrame | None:
    logging.info("Starting data extraction and cleaning.")
    rexs_df = fetch_data_from_db()
    if rexs_df is not None or rexs_df.empty:
        rexs_df = rexs_df.applymap(clean_string)
        logging.info("Data extraction and cleaning succeeded.")

        return rexs_df


def rex_content(row: pd.Series) -> str:
    return (
        f"Rex title: {row['Title']}\n"
        f"Status: {row['RexStatusCode']}\n"
        f"Organization Unit ID: {row['OrganisationUnitID']}\n"
        f"Global Business Unit (GBU) ID: {row['GlobalBusinessUnitID']}\n"
        f"Assessment: {row['AssessmentName']}\n"
        f"What Went Wrong Or Well: {row['WhatWentWrongOrWell']}\n"
        f"Corrective actions / Gains achieved: {row['CorrectiveActionOrGainsAchieved']}\n"
        f"Lesson learned: {row['LessonLearned']}\n"
        f"Relevant for Global Business Unit (GBU): {row['RelevantForGbu']}\n"
        f"Relevant for entity: {row['RelevantForEntity']}\n"
        f"Relevant for portfolio: {row['RelevantForPortfolio']}\n"
        f"Discipline / Work package ID: {row['DisciplineWorkPackageID']}\n"
        f"Project phase: {row['RexProjectPhaseName']}\n"
        f"Knowledge area: {row['KnowledgeAreaName']}\n"
        f"Key factor: {row['CategoryFactorName']}\n"
        f"Impact CAPEX: {row['CapexImpactName']}\n"
        f"Impact OPEX: {row['OpexImpactName']}\n"
        f"Impact on Deadlines: {row['ImpactDelayName']}\n"
        f"Impact on Revenues: {row['RevenueImpactName']}\n"
        f"Impact HSE: {row['ImpactHseName']}\n"
        f"Impact on Stakeholders: {row['ImpactReputationName']}\n"
        f"Supplier: {row['SupplierName']}\n"
        f"Creator First Name: {row['CreatorFirstName']}\n"
        f"Creator Last Name: {row['CreatorLastName']}\n"
        f"Plant: {row['PlantName']}\n"
        f"Country: {row['CountryName']}\n"
        f"Technology: {row['PlantTypeName']}\n"
        f"Creation Date UTC: {row['CreatedDateUtc']}\n"
        f"Modification Date UTC: {row['ModifiedDateUtc']}\n"
        f"GUID: {row['GUID']}\n"
        f"Importance Level: {row['ImportanceLevel']}"
    )


def index_documents(rexs_df: pd.DataFrame) -> None:
    documents = []
    for _, row in rexs_df.iterrows():
        # Concatenation of all fields into a single string for the "content" field

        content = rex_content(row)

        # Create the embedding for the concatenated content
        try:
            response = client.embeddings.create(
                input=content, model=EMBEDDING_MODEL, timeout=TIMEOUT
            )
            document_embedding = response.data[0].embedding
        except Exception as e:
            logging.error(f"Error while creating embedding for {row['GUID']}: {e}")
            logging.warning(
                f"Sleeping for {SLEEP_RATE_LIMIT} seconds to avoid rate limits."
            )
            sleep(SLEEP_RATE_LIMIT)  # Sleep for 1 second to avoid rate limits

        # Prepare the document for indexing
        document = {
            "id": str(row["GUID"]),
            "content": content,
            "link_rex": row["RexLink"],
            "url": row["RexLink"],
            "content_vector": document_embedding,
        }
        documents.append(document)

        # Upload in batches of 100
        if len(documents) >= BATCH_UPLOAD_SIZE:
            try:
                search_client.upload_documents(documents)
                logging.info(
                    f"Batch of {len(documents)} documents successfully indexed."
                )
                documents.clear()

                sleep(SLEEP_RATE_LIMIT)  # Sleep for 1 second to avoid rate limits
            except Exception as e:
                logging.error(f"Error while indexing batch: {e}")

    # Index any remaining documents
    if documents:
        try:
            search_client.upload_documents(documents)
            logging.info(
                f"Final batch of {len(documents)} documents successfully indexed."
            )
        except Exception as e:
            logging.error(f"Error while indexing final batch: {e}")


@app.timer_trigger(
    schedule=os.getenv("SCHEDULE", "0 */15 * * * *"),
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
def rex_index_vector(mytimer: func.TimerRequest) -> None:
    logging.info(
        f"Timer trigger function started: {mytimer.past_due} status: {mytimer.schedule_status} schedule: {mytimer.schedule}"
    )
    logging.info(f"START: document indexing from the database in progress.")

    rexs_df = fetch_and_clean_data()

    if rexs_df is not None:
        index_documents(rexs_df)
        logging.info("END: Document indexing from the database completed successfully.")
    else:
        logging.error("END: No data extracted. Indexing is canceled.")
