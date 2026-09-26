# RAG Module Responsibilities

This document summarises the role of each module in the Web Platform RAG ingestion and retrieval pipeline.

## `app/config.py`

Loads and validates environment-based configuration through a single `Settings` object. It contains connection details and model identifiers, but no business logic or service clients.

## `app/storage/s3_storage.py`

Provides access to MinIO locally and Amazon S3 in deployed environments through Boto3. It retrieves and stores raw objects and returns their bytes and source metadata. It does not parse document contents.

## `app/documents/parser.py`

Defines the common parser interface. Every format-specific parser follows this contract so the ingestion service can process different file types consistently.

## `app/documents/json_asset_parser.py`

Converts an asset JSON object into meaningful searchable text and one or more LangChain `Document` objects. It also attaches required metadata such as `accountId`, `assetId`, document type, and source key.

## `app/documents/pdf_parser.py`

Extracts text from PDF files and converts it into LangChain `Document` objects. It preserves source and page metadata so retrieved content can be traced and cited.

## `app/chunking/text_chunker.py`

Splits long Documents into smaller searchable chunks. It preserves existing metadata and adds chunk-specific information such as `chunkIndex`. It does not generate embeddings.

## `app/embeddings/bedrock_embeddings.py`

Uses an Amazon Bedrock embedding model to convert document chunks and user queries into vectors. It hides Bedrock-specific API details from the rest of the application.

## `app/vector_store/chroma_store.py`

Manages ChromaDB operations, including inserting, replacing, deleting, and querying vectors. It applies metadata filters and uses deterministic record IDs to prevent duplicate chunks during re-ingestion.

## `app/retrieval/retriever.py`

Coordinates semantic searches against ChromaDB. It embeds the query, applies mandatory tenant filters such as `accountId`, and returns the most relevant chunks with their source metadata.

## `app/generation/prompt.py`

Builds the final prompt from the user's question and retrieved context. It instructs the model to use only the supplied evidence, cite its sources, and report when the context is insufficient.

## `app/generation/bedrock_llm.py`

Sends the constructed prompt to Claude through Amazon Bedrock and returns the generated response. It contains model invocation logic but no retrieval or access-control rules.

## `app/ingestion/ingestion_service.py`

Coordinates the ingestion workflow:

1. Retrieve an object from storage.
2. Select the correct parser.
3. Convert it into Documents.
4. Split the Documents into chunks.
5. Generate embeddings.
6. Store the chunks and vectors in ChromaDB.

It orchestrates these components without implementing their internal responsibilities.

## Entry-point modules

### `app/process_document.py`

A development command that runs retrieval, parsing, and chunking for one object and prints intermediate results for inspection.

### `app/ingest.py`

A command-line entry point for ingesting one or more source documents into ChromaDB.

### `app/search.py`

A development command that performs retrieval without involving Claude. It displays matching chunks, metadata, and similarity information so retrieval quality can be evaluated independently.

### `app/ask.py`

Runs the complete RAG query workflow: retrieve relevant chunks, construct the prompt, call Claude, and return the answer with source references.

## `tests/`

Contains unit and integration tests for parsing, chunking, metadata propagation, deterministic IDs, account isolation, storage access, ChromaDB operations, retrieval quality, and re-ingestion behaviour.

## Core design rule

Each module should have one clear responsibility. Storage retrieves files, parsers understand formats, the chunker divides text, the embedding provider creates vectors, ChromaDB stores and searches them, and orchestration modules connect those stages.
