# Query response contract (version 1)

What the query Lambda (`cdk-rag-query-dev` / prod) returns for one question. The platform server
invokes the Lambda and passes this JSON to the UI. Built by `ask()` in
[app/query.py](../app/query.py); the Lambda handler is [lambda/main.py](../lambda/main.py).

## Request

```json
{ "query": "Who issued the invoice for Half of You?", "accountId": 46 }
```

## Rules

- **Every key is always present.** Keys that don't apply are empty (`null`, `[]`, `{}`, `0`), so
  the UI never has to check whether a key exists.
- **`kind` decides what to render**: `"answer"` → `answer` + `citations`; `"list"` → `assets`.
- **`error` is `null` on success.** On bad input or a failure it holds a message to show the user,
  and the rest of the response is empty (`kind` is `null`).
- **`version`** changes when the shape changes. The UI can refuse or adapt to versions it doesn't
  know.
- **Names are looked up at response time** (asset and file names live in MySQL, not on chunks).
  A name can be `null` if the record was deleted since ingestion.
- **No links in the response.** `fileId` identifies the PDF; the platform server builds the link
  with its own CloudFront signing.

## Full shape

```jsonc
{
  "version": 1,
  "query": "Which assets were bought at a Doyle auction?",   // echo of the request
  "kind": "list",                                            // "answer" | "list" | null (error)

  // How the question was understood: the "reasoning" panel.
  "plan": {
    "filters": { "asset_type": "FURNITURE" },   // SQL filters on asset columns; {} when none
    "document_category": "INSURANCE_POLICY",    // exact document-type filter, or null
    "document_query": "bought at a Doyle auction",   // what was searched in the text, or null
    "explanation": "Lists assets whose documents mention buying at Doyle; no filters named."
  },

  // How it was searched: the "details" panel.
  "trace": {
    "sql_candidates": null,      // assets left after SQL filters; null = no filters used
    "chunks_searched": 312,      // document passages in scope
    "cutoff": { "min_score": 0.2, "max_assets": 10 },   // ranked lists only, else null
    "timings_ms": { "plan": 700, "search": 150, "answer": 600, "total": 1500 }
  },

  // kind = "answer"
  "answer": "The invoice was issued by Esthella Provas & Associates [1] ...",
  "citations": [
    {
      "n": 1,                          // [1] in the answer text
      "assetId": 7441, "assetName": "Half of You",
      "fileId": "8a1f....pdf", "fileName": "YONA0004_2.pdf",
      "page": 1,
      "category": "VALUATION_REPORTS",
      "score": 0.408,                  // similarity, 0..1 (higher = closer)
      "text": "ESTHELLA PROVAS & ASSOCIATES ... INVOICE ..."   // the passage behind [1]
    }
  ],

  // kind = "list"
  "assets": [
    {
      "rank": 1,
      "assetId": 6945, "assetName": "May 15, 2001",
      "score": 0.603,                  // best passage's similarity; null = exact match (see below)
      "evidence": [                    // why it matched: up to 3 passages, best first
        {
          "assetId": 6945,
          "fileId": "....pdf", "fileName": "DAMI0001 Doyle 2022.pdf",
          "page": 1,
          "category": "PROVENANCE",
          "score": 0.603,
          "text": "... DOYLE NEW YORK ... Lot 112 ..."   // the "why"
        }
      ]
    }
  ],
  "total_assets": 10,

  "error": null
}
```

## The three kinds of list

| List | Example | `score` | `evidence` |
|---|---|---|---|
| Ranked by document content | "Which assets were bought at a Doyle auction?" | 0..1 | Up to 3 passages with `text` |
| Document type only | "Which artworks have a certificate of authenticity?" | `null` | One entry per file of that type; `text`, `page`, `score` are `null` (the match is the category itself) |
| Filters only | "Show me all paintings" | `null` | `[]` (the reason is in `plan.filters`) |

Ranked lists are cut to `trace.cutoff` (at most 10 assets scoring ≥ 0.2). Lists of the other two
kinds are exact and not cut, so they can be long (e.g. 153 paintings): page them in the UI.

## Rendering notes

- **Citations:** replace `[n]` in `answer` with a link or chip that opens `citations[n-1]`
  (file name, page, category, passage).
- **Evidence:** show a 1–2 line snippet of `evidence[0].text` under each asset, with the query
  words highlighted; expand to all passages. The passage is a whole chunk (up to ~1000 characters),
  so the relevant sentence is somewhere inside it.
- **Scores:** useful as a relevance bar; don't present them as percentages or confidence.
- **Plan panel:** `plan.explanation` plus the filters and category as chips tells the user how the
  question was read, and makes surprising results understandable.

## Errors

```json
{ "version": 1, "query": "", "kind": null, "error": "'query' (string) is required", ... }
```

| Cause | `error` |
|---|---|
| Missing or non-string `query` | `'query' (string) is required` |
| Missing or non-integer `accountId` | `'accountId' (integer) is required` |
| Any failure while searching (details in CloudWatch) | `The search failed. Please try again.` |

A Lambda timeout or crash outside the handler still returns AWS's own `{"errorMessage": ...}`;
the server should treat that as an error too.
