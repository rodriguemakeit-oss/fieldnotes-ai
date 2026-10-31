import logging
import os

import azure.functions as func
import httpx
from azure.core.credentials import AzureKeyCredential
from azure.functions import FunctionApp
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI


app = FunctionApp()

# HTTPX client
httpx_client = httpx.Client(verify=False)

# Chatbot parameters
LLM_MODEL = os.getenv("LLM_MODEL")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
TEMPERATURE = float(os.getenv("TEMPERATURE"))
K_NEAREST_NEIGHBORS = os.getenv(
    "K_NEAREST_NEIGHBORS", 7
)  # number of nearest neighbors to retrieve

# Global variables
DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "t", "yes")
TIMEOUT = int(os.getenv("TIMEOUT", 60))  # seconds
TOP_K = int(os.getenv("TOP_K", 10))  # number of documents to retrieve
HISTORIC_LENGTH = int(
    os.getenv("HISTORIC_LENGTH", 5)
)  # number of messages to keep in the conversation history

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

# Init conversation (run once at startup)
user_conversations = {}

# NOTE System prompt for the LLM in one line because if you break it to several lines will not behave as expected. Cf Azure Chat playground "View code" option
system_prompt = 'You are a AI assistant that helps people develop, build, or maintain renewable energy power plants by providing recommendations based on real-life experiences from other users.\n\n**Instructions:**\n\n1. **Language detection and translation:**\n- Detect the language of the users prompt and always respond in the same language.\n- Always make sure that your entire responses, including data you output, matches user prompt language.\n- Translate all rex content inside [Rex content] in the user message language.\n- Exception : For title provide both tranlsated and original title.\n\n2. **Experience Classification:**\n- Identify experiences as either Best Practices or Improvements.\n    - **Best Practices:** Include information on gains obtained and are recommended practices.\n    - **Improvements:** Experiences to avoid or inspire corrective actions if they exist.\n- Both types of experiences should include Lessons Learned to be implemented.\n\n3. **Advisory Role:**\n- If user question is not clear skip the rest of instructions and answer you dont understand\n- Advise the user and document topics addressed, while never giving document number/id/guid.\n- Prioritize experiences with a creation or modification date within the last 3 years.\n- Use only the information present in the documents to answer questions.\n- Do not use personal knowledge or the internet to respond if you have any rex/document content.\n- Use your personal knowledge if you dont have any rex/document content.\n- Do not mention document numbers or references in responses.\n- Provide feedback and information based solely on the content without referencing specific documents.\n- If the user says hello, thanks, goodbye, or something welcoming, respond to that question without using any external data. Remind me you are an AI assistant and that you are here to help them.\n\n4. **Response Structure:**\n- **Global Summary:** Provide a summary of main answers and recommendations (maximum 6 lines).\n- **Detailed Answers:** Classify answers by main themes (3 to 10 relevant answers).\n    - **Themes:** Announce each theme in bold and underline it.\n    - **Answers:** Present answers in a numbered list, including references to the renewable energy production plant, technology, or country concerned.\n    - **Feedback Titles:** List all feedback titles used to construct the answers, including feedback link information if available.\n- Always put "title" in your answer before a rex title. Translate "title" in user question language before the title of the feedback/rex.\n\n5. **Conclusion:**\n- Provide a conclusion of two or three lines.\n- Invite the user to ask follow-up questions to refine the answers provided.\n\n**Very important !! Notes:**\n- Ensure focus on explainability and actionable insights without exposing the database or document structure.\n- Translate any structured content or titles into the users language.\n- Titles must always follow the translated format: "Title" in the relevant language followed by both the translated and original versions of the title.'


def chatbot(user_id, query):
    logging.info(f"User ID: {user_id}, Query: {query}")

    if DEBUG and query == "reset":
        user_conversations[user_id] = []
        return "[DEBUG] Conversation reset.", httpx.codes.OK.value

    try:
        # Initialisation de la conversation si nécessaire
        if user_id not in user_conversations:
            user_conversations[user_id] = [{"role": "system", "content": system_prompt}]

        # Create embedding for vector search
        embedding_response = client.embeddings.create(
            input=query, model=EMBEDDING_MODEL, timeout=TIMEOUT
        )

        # Extracts the embedding vector
        embedding = embedding_response.data[0].embedding

        # Vector query that includes the "content_vector" field for search, looking for K nearest neighbors according to the embedding
        vector_query = VectorizedQuery(
            vector=embedding,
            k_nearest_neighbors=K_NEAREST_NEIGHBORS,
            fields="content_vector",
        )

        # Search using the vector query and select the content and link_rex fields, and return the top K results
        search_results = search_client.search(
            search_text=query,
            vector_queries=[vector_query],
            select=["content", "link_rex"],
            top=TOP_K,
        )

        matched_documents = [doc for doc in search_results]
        logging.info(f"Number of documents found : {len(matched_documents)}")

        if not matched_documents:
            return "No answer found sorry.", httpx.codes.NOT_FOUND.value

        # All matched documents content including their REX link
        all_matched_documents = "\n\n".join(
            [
                f"[Rex content]: {doc['content']}\n[REX link]: {doc.get('link_rex', 'Not available')}"
                for i, doc in enumerate(matched_documents)
            ]
        )

        # LLM user prompt including the documents and the user query
        complete_prompt = f"REXs:{all_matched_documents}\nQuestion: {query}\nAnswer:"

        if DEBUG:
            logging.info(f"Prompt: {complete_prompt}")

        user_conversations[user_id].append({"role": "user", "content": query})

        # LLM Chat Completion
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=user_conversations[user_id]
            + [{"role": "user", "content": complete_prompt}],
            temperature=TEMPERATURE,
        )

        answer = response.choices[0].message.content
        user_conversations[user_id].append({"role": "assistant", "content": answer})

        # Keep only the last N messages in the conversation history
        user_conversations[user_id] = user_conversations[user_id][-HISTORIC_LENGTH:]

        return answer, httpx.codes.OK.value

    except Exception as e:
        logging.error(f"Error in handle_chatbot: {str(e)}")
        return (
            f"An error occurred on: {str(e)}",
            httpx.codes.INTERNAL_SERVER_ERROR.value,
        )


@app.function_name(name="rex_chatbot_handle")
@app.route(route="chat", methods=["POST"])
def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("START: Processing user question.")

    try:
        req_body = req.get_json()
        query = req_body.get("query")
        user_id = req_body.get("user_id")

        if query is None:
            return func.HttpResponse(
                "Parameter 'query' is missing",
                status_code=httpx.codes.BAD_REQUEST.value,
            )

        query = query.strip()

        answer, status_code = chatbot(user_id, query)
        logging.info("END: Answer retrieved successfully.")
        return func.HttpResponse(answer, status_code=status_code)

    except Exception as e:
        logging.error(f"END: Error when handling request: {str(e)}")
        return func.HttpResponse(
            f"Internal server error: {str(e)}",
            status_code=httpx.codes.INTERNAL_SERVER_ERROR.value,
        )
