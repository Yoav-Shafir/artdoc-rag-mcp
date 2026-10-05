# How Overstone AI Search Answers a Question

**Status:** built and running · **Last updated:** 2026-10-05

This page explains, in plain language, how the AI search turns a question into an answer or a list of
assets, and why it works this way. Technical details are linked at the end.

## 1. Two kinds of information

Questions about a collection usually mix two kinds of information that live in different places:

| Example part of a question | Kind of information | Where it lives |
|---|---|---|
| "paintings", "by Picasso", "from the 1960s" | **Asset details**: exact facts about each asset | The platform's database |
| "bought at Christie's", "with a certificate" | **What the documents say**: invoices, valuations, provenance… | The PDFs attached to assets |

Some questions need only one of them; many need both:

> "Which furniture pieces were bought at Christie's?"
> → *furniture* is an asset detail; *bought at Christie's* is in the documents.

## 2. Why a simple search isn't enough

**Searching documents finds things by meaning, not by exact rules.** It's very good at "which text is about
buying at auction?", but it can't say for certain "this asset is furniture". That's a fact in the database.

**Searching everything and filtering afterwards loses results.** If we searched all documents first and then
kept only furniture, documents about paintings could fill the top results and push the furniture out of
view. So the asset details have to narrow the search *before* it runs.

**A search always returns something.** Even an unrelated document gets a small "closeness" score. So the
system needs a way to decide what's close enough to show (see step 5).

## 3. How it works, step by step

```
Question
   │
   ▼
1. SAFETY CHECK   Is the question safe to answer?
   ▼
2. UNDERSTAND     What does the question ask for?
   ▼
3. NARROW DOWN    Which assets and documents are in scope?
   ▼
4. SEARCH         Find the matching text inside those documents
   ▼
5. REPLY          A written answer with sources, or a list of assets
```

### Step 1: Safety check

Every question is first checked by an AWS safety service (Bedrock Guardrails). Attempts to trick the AI
("ignore your instructions…") and abusive questions are stopped here, before anything is searched.

### Step 2: Understand the question

The AI reads the question and fills in a short form. It never writes database queries itself; it only
fills in fields with allowed values:

| Field | Meaning | Example: "Which furniture pieces were bought at Christie's?" |
|---|---|---|
| **Reply type** | A written answer, or a list of assets? | a list |
| **Asset details** | Type, artist, years, medium, period, condition | type = furniture |
| **Document type** | Only if the question names one, e.g. "certificates" | (none) |
| **What to look for in the documents** | The part about document content | "bought at Christie's" |
| **Explanation** | One sentence on how it read the question | shown to the user |

Safeguards on this step:
- **Only real values are allowed.** "Post-war" becomes the exact database value "POST-WAR (1945-1970)";
  an invented value is rejected.
- **A document type is used only if the question really names one.** "Which assets are insured for more
  than $100,000?" doesn't name a document type, so all documents are searched. If the AI guessed
  "insurance policy" there, it could hide an answer written in a valuation report.
- **The same question gets the same reading every time** (the AI runs with no randomness).

### Step 3: Narrow down

The search area is limited, in this order:
1. **The user's own account.** Always, so one client never sees another's documents.
2. **The document type**, if one was named.
3. **The matching assets**, if asset details were given: the database lists them (e.g. all furniture),
   and only their documents are searched.

If no asset matches the details, the system says so straight away instead of searching.

### Step 4 and 5: Search and reply

There are four ways to reply, depending on step 2:

| Question | What happens | Reply |
|---|---|---|
| "Which assets have a certificate of authenticity?" | Document type only: no text search needed | Every asset that has that document type, marked **exact match** |
| "Show me all paintings" | Asset details only | The assets the database found, marked **exact match** |
| "Which assets were bought at Christie's?" | Search the documents' text and rank the assets | Up to **10** assets, best match first, each with its **matching text** and a relevance bar |
| "Who issued the invoice for Half of You?" | Find the **3** most relevant pieces of text | A **written answer**, with sources [1], [2]… that open the PDF at the right page |

**Ranked lists are trimmed.** Every asset gets some closeness score, even unrelated ones. To avoid listing
the whole collection, only assets with a reasonable score are kept (at least 0.2 out of 1), and at most 10.

**Every result shows why it's there:** the matching text, the file name and the page. The user can check
each one in a click, which matters because ranked lists can include weaker matches near the bottom.

## 4. What users see

- **"How your question was understood":** the explanation, the asset details and document type used, how
  many documents were searched, and how long it took.
- **Answers:** sources grouped by file and page; clicking a file name opens the PDF at that page.
- **Lists:** a relevance bar (or "exact match"), the matching text, and **"Show in table"** to filter the
  asset table to these results.

## 5. Known limits

| Situation | What happens today |
|---|---|
| A list question with nothing to search, e.g. "show all bank details" | Returns nothing. Planned fix: search using the question itself |
| A question naming an artwork title, e.g. "Who signed the certificate for Muhammad Ali?" | The title can be mistaken for an artist, or not linked to the asset. Planned fix: recognise asset names |
| Counting: "How many assets have a certificate?" | Not supported yet |
| Comparing items: "Which items share an owner?" | Not supported: needs owners extracted into the database |
| Dates inside documents: "documents from last year" | Not supported: document dates aren't stored yet |
| Photographed book pages | Only the captions may be readable, so little can be answered from them |
| Documents not yet processed | Can't be found. Today two accounts are processed, up to 10 files per document type |

These trade-offs are explained in more detail in [FILTER_DILEMMAS.md](FILTER_DILEMMAS.md).

## 6. When the results can be trusted as exact

- **Exact lists** (document type only, or asset details only) are reliable.
- **Ranked lists** are a strong starting point, not a final answer: always check the matching text.
- For **compliance checks, reports or exports**, review each result, or add a verification step (an extra
  AI check that labels each result "matches" / "unclear").

## 7. Open questions

1. Which uses need exact lists (compliance, reporting), and therefore a verification step?
2. What do real users ask? Collect their questions, especially failed ones, to improve the search.

---

**For developers:** the steps above are implemented in [app/query.py](../app/query.py) (`ask()`), the
question form in [app/planner.py](../app/planner.py), the database filters in
[app/db/database.py](../app/db/database.py) (`candidate_asset_ids()`), and the reply format in
[RESPONSE_CONTRACT.md](RESPONSE_CONTRACT.md).
