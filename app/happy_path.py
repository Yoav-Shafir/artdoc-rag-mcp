"""Happy-path RAG pipeline, built one small step at a time.

Run: uv run poe pipeline
"""

import io

import boto3
import pypdfium2
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# pypdf directly: LangChain's PyPDFParser wraps the same engine but lives in the sunset langchain-community.
from pypdf import PdfReader

from app.config import config
from app.db import Database
from app.rag import answer, make_collection, make_embeddings, make_llm, search
from app.storage import S3Storage

# Mocked account iteration: the fileIds of account 46 that exist in MinIO.
# Later these come from Database.list_account_ids() / files_for_account().
ACCOUNT_ID = 46
FILE_IDS = [
    "04dd0ecf-78fc-4dc5-a60d-20438ac81156.jpg",
    "062d2aed-2cab-481f-8781-51171b93451f.pdf",
    "0ef031d3-bb33-43f6-b0b9-be28cca828fa.pdf",
    "472b966f-100a-436f-94f9-14626e77806f.jpg",
    "62391e0b-20e8-42ce-8c0f-33d8300ff515.png",
    "8024ad34-dd03-49d3-8820-7e3b596576a7.jpeg",
    "9c09a8b5-80ea-44d7-8703-b71f1fb58fe0.jpeg",
    "aae7fa56-60fa-499f-9ca4-a0bbb5ff9243.png",
    "7f94aa84-1da6-4beb-8143-884806c2c628.pdf",
    "84a3da9c-49a1-4426-b9b7-0b9b9e040bdf.pdf",
    "a055ad70-09b2-4b7a-add8-5f0c0de0d1ae.pdf",
    "936a349c-2647-4a43-896c-dd7cae5034ff.pdf"
]

# Test questions for the search step.
QUESTIONS = [
    "What is the provenance of the drawing?",
    "Who issued the invoice and when?",
    "What is the proposal number?",
]


def parse_pdf(pdf_bytes: bytes, file_id: str, textract) -> list[Document]:
    """Step 2: turn PDF bytes into one LangChain Document per page.

    Pages with a text layer are read directly with pypdf. Scanned pages (images
    of paper, no text layer) come back empty from pypdf, so they're sent to OCR (Optical Character Recognition).
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    documents = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text()  # Extract text from the page's text layer (if it has one)
        if not text.strip():
            # No text layer: this page is a scan. Read it with OCR (Optical Character Recognition) instead.
            text = ocr_page(textract, pdf_bytes, page_number)
            print(f"    page {page_number}: scanned, read with Textract OCR ({len(text)} chars)")
        documents.append(
            Document(page_content=text, metadata={"fileId": file_id, "page": page_number})
        )
    return documents


def make_textract():
    """AWS Textract client: OCR service that reads text from images of pages."""
    session = boto3.Session(profile_name=config.bedrock.aws_profile)  # AWS_PROFILE from .env
    return session.client("textract", region_name=config.bedrock.region)  # eu-west-2


def ocr_page(textract, pdf_bytes: bytes, page_number: int) -> str:
    """Read one scanned PDF page with Textract, keeping the page's reading order.

    Uses AnalyzeDocument with the LAYOUT feature: besides the text lines, Textract
    returns layout blocks (title, paragraph, header, ...) in reading order, so text
    in side-by-side columns isn't mixed together line by line.

    Future option (not built yet): if Textract's confidence on a page is low
    (handwriting, faint photos), send the page image to the Bedrock vision LLM
    to transcribe it instead. See the plan's "Later" section.
    """
    # 1. Render the PDF page as a PNG image. scale=2 (~144 dpi) is sharp enough for OCR.
    pdf = pypdfium2.PdfDocument(pdf_bytes)
    image = pdf[page_number - 1].render(scale=2).to_pil()  # pypdfium2 pages start at 0
    png = io.BytesIO()
    image.save(png, format="PNG")

    # 2. Ask Textract for the text lines plus the layout blocks.
    response = textract.analyze_document(
        Document={"Bytes": png.getvalue()}, FeatureTypes=["LAYOUT"]
    )

    # 3. Rebuild the text in reading order. Every block has an Id; a layout block
    #    points to its LINE blocks through a CHILD relationship.
    blocks = {block["Id"]: block for block in response["Blocks"]}
    paragraphs = []
    for block in response["Blocks"]:  # layout blocks arrive in reading order
        if not block["BlockType"].startswith("LAYOUT_"):
            continue
        line_ids = [
            child_id
            for relation in block.get("Relationships", [])
            if relation["Type"] == "CHILD"
            for child_id in relation["Ids"]
        ]
        lines = [blocks[i]["Text"] for i in line_ids if blocks[i]["BlockType"] == "LINE"]
        if lines:
            paragraphs.append(" ".join(lines))  # the lines of one layout block form a paragraph
    return "\n\n".join(paragraphs)  # blank line between paragraphs, as the splitter prefers


def main():
    """Run the pipeline for the mocked account's PDFs, printing each step's output."""
    storage = S3Storage()
    bucket = config.s3.bucket  # S3_BUCKET from .env: where the PDFs are read from
    db = Database()  # read-only access to the platform's MySQL (the `file` table here)

    # Splits text at paragraph, then line, then sentence, then word boundaries,
    # keeping each chunk under chunk_size characters. Neighbouring chunks share
    # chunk_overlap characters so a sentence cut at a boundary isn't lost.
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.chunking.size,  # 1000
        chunk_overlap=config.chunking.overlap,  # 150
    )

    embeddings = make_embeddings()  # BedrockEmbeddings instance: AWS Bedrock Titan v2
    collection = make_collection()  # ChromaDB collection object: connected to overstone-assets
    textract = make_textract()      # boto3 Textract client: AWS OCR service for scanned PDFs

    # Step 1: keep only PDFs, then fetch each one's bytes from MinIO.
    pdf_ids = [file_id for file_id in FILE_IDS if file_id.endswith(".pdf")]

    for file_id in pdf_ids:
        obj = storage.get_object(bucket, file_id)  # Fetch a single PDF file from MinIO (S3)
        print(f"{file_id}  {len(obj.body):>8} bytes  {obj.content_type}")

        # Get the file record from the MySQL file table.
        # record has: file_id, name, category, asset_id, account_id, asset_name, updated_at
        record = db.lookup_file(file_id)

        pages: list[Document] = parse_pdf(obj.body, file_id, textract)
        for doc in pages:
            # Attach the file's owner info (account_id and asset_id) from the database record to each page.
            # Only IDs go on the chunks; names and categories stay in MySQL.
            doc.metadata["accountId"] = record.account_id
            doc.metadata["assetId"] = record.asset_id
            preview = " ".join(doc.page_content.split())[:100]
            print(f"  {doc.metadata}  {len(doc.page_content):>5} chars  {preview!r}")

        # Step 3: split the pages into chunks. Each chunk copies the metadata
        # of the page it came from (fileId, page, accountId, assetId).
        chunks = splitter.split_documents(pages)

        # PDF pages can still end up with no chunks (e.g. a blank page, or OCR found nothing).
        if not chunks:
            print("    no text found, skipped")
            continue

        # Print each chunk for debugging.
        for chunk in chunks:
            preview = " ".join(chunk.page_content.split())[:60]
            print(f"    chunk {chunk.metadata}  {len(chunk.page_content):>5} chars  {preview!r}")

        # Step 4: one vector per chunk, in the same order as the chunks.
        vectors = embeddings.embed_documents([chunk.page_content for chunk in chunks])
        for vector in vectors:
            print(f"    vector: {len(vector)} numbers, starts {[round(x, 3) for x in vector[:4]]}")

        # Step 5: store text + vector + metadata. 
        # Delete this file's old chunks first (ChromaDB upsert merges metadata, doesn't replace it).
        collection.delete(where={"fileId": file_id})
        # After upsert, each chunk in ChromaDB looks like:
        # {"id": "fileId:0", "document": "text...", "embedding": [0.005, 0.014, ...], "metadata": {"accountId": 46, "assetId": 7442, "fileId": "...", "page": 1}}
        collection.upsert(
            ids=[f"{file_id}:{index}" for index in range(len(chunks))],  # e.g. "062d2aed-...pdf:0", "062d2aed-...pdf:1"
            documents=[chunk.page_content for chunk in chunks],
            embeddings=vectors,
            metadatas=[chunk.metadata for chunk in chunks],
        )

    print(f"\nChroma collection '{collection.name}' now holds {collection.count()} chunks")

    llm = make_llm()  # ChatBedrockConverse instance: AWS Bedrock Nova 2 Lite LLM

    # Steps 6 + 7 for each test question: search, then answer with citations.
    for question in QUESTIONS:
        # Step 6: Search ChromaDB for chunks similar to the question using vector similarity.
        # results is a list[dict] with fields: text, metadata, score, fileName, label
        results = search(db, collection, embeddings, question, ACCOUNT_ID)
        print(f"\nQ: {question}")
        for hit in results:
            preview = " ".join(hit["text"].split())[:70]
            print(f"  {hit['score']:.3f}  {hit['label']:<24}  {preview!r}")

        # Step 7: Ask the LLM to generate an answer from the search results with citations.
        # answer_text example: "The invoice was issued by Esthella Provas [1] on September 19 [1]."
        answer_text = answer(llm, question, results)
        print(f"A: {answer_text}")
        # Print a legend: show which file and page each citation number [1], [2], etc. refers to.
        for number, hit in enumerate(results, start=1):
            print(f"   [{number}] {hit['label']}")

if __name__ == "__main__":
    main()
