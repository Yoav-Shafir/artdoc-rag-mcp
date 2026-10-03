# Filter dilemmas: exact filters vs vector similarity

Why some questions are hard for the RAG query flow, which options we have, and what each
one returns. Code: [app/planner.py](../app/planner.py), [app/query.py](../app/query.py).

## The core problem

Two tools answer questions, and they behave very differently:

| | Exact filter (SQL / Chroma `where`) | Vector similarity |
|---|---|---|
| Works on | Stored fields: asset type, year, file `category`, ... | The text of the chunks |
| Answer | Yes / no per record | A score for **every** chunk, never "no match" |
| Good at | "Which assets **have** X" | "What do the documents **say** about X" |
| Fails when | The field is wrong or missing, or the filter is guessed | The wording overlaps with unrelated text |

Similarity scores measure **wording overlap**, not meaning in the business sense. Real example,
from the query "What is the insured value in the insurance policy?":

```
0.277  Signed Loan Form (EXHIBITION_AND_LITERATURE)  ← mentions insurance in passing
0.243  Certificate of Cover (INSURANCE_POLICY)       ← the actual answer
0.222  Signed Loan Form, page 2
```

The loan form outscores the insurance policy itself.

## Dilemma 1: where to cut a ranked list

A `list` query scores every asset in scope, so without a cutoff it returns the whole account.
Real example, "Which artworks have a certificate of authenticity?", before any fix: **63 assets**.

```
0.429  Silver Shell                      ← real match
0.282  Meyers Manx Corner Painting
0.282  Esso/Sinclair/Castrol Can
0.210  Tocco                             ← loan form, loosely related
0.158  Electra (Olympic Clock Tower)
... 58 more, down to 0.006 (a sofa's valuation report)
```

The right threshold changes per question: 0.25 would keep the noise above and drop the real
insurance policy (0.243) in the insurance example.

| Option | Pros | Cons |
|---|---|---|
| No cutoff (rank everything) | Never misses a match | Returns the whole account; useless in a UI |
| Fixed threshold + max count **(current: 0.2, max 10)** | Simple; removes the noise tail | A heuristic: a fixed number can drop real matches or keep noise |
| Relative cutoff (e.g. stop at a big gap from the top score) | Adapts to each question | Unstable with few results; harder to explain |
| Let the LLM pick the relevant ones from the top N | Judges meaning, not wording | Extra LLM call: slower, costs more, can still be wrong |

## Dilemma 2: when to apply a document-type filter

Every chunk stores its file's `category` (VALUATION_REPORTS, PROVENANCE, ...), so the planner can
turn "certificates" into an exact filter. The risk is a **guessed** category: the category says
what kind of file it is, not which topics appear inside it. Purchase details also appear in
valuation reports, insurance schedules and loan forms.

Example: "Which assets were bought at a Doyle auction?" A loose planner sets
`PROVENANCE` (buying is ownership history). If the only mention of Doyle is in a valuation report
("Acquired at Doyle New York, 2019"), that asset never appears.

| Option | Pros | Cons |
|---|---|---|
| No category filter (similarity only) | Searches everything; no hidden matches | "Which assets have a certificate?" returns a long, noisy ranking |
| Loose: the LLM infers the category from the topic | Precise when the guess is right | Silently hides matches in other document types |
| Strict: only when the user names a document type | Exact for "certificates", "the insurance policy"; safe otherwise | It's an instruction to the LLM, not a guarantee: in testing it still guessed 2 of 8 |
| **Strict + code check (current): keep the category only if one of its type words is in the question** | Guaranteed: no guessing gets through | Keyword lists to maintain (`CATEGORY_WORDS`); an unusual phrasing loses the filter, which falls back to searching everything |
| Soft: boost chunks of the category instead of filtering | Never hides results | Ranking still noisy; needs custom scoring |

## Dilemma 3: facts that exist only inside the documents

"Show me all documents generated in the last year." The document date is written in the PDF
("Valuation as at 12 March 2025"), not stored anywhere we can filter on. Vector search cannot
answer it: "last year" is just text to it.

| Option | Pros | Cons |
|---|---|---|
| Use the upload date (`file.createdAt` in MySQL) | Exists today; exact SQL filter | Wrong when old documents were uploaded recently |
| **Extract the date at ingestion** (LLM reads page 1, stored as `docDate: 20250312` on chunks) | Exact range filter in Chroma; one cheap LLM call per file | Undated documents drop out of date queries; needs a one-time re-ingest |
| Ask the LLM over the top N chunks | No ingestion change | Only sees N chunks, so it cannot list "all" documents |

The same pattern (extract once at ingestion, filter exactly at query time) applies to any fact
users filter on: issuer, valuation amount, auction house.

## Example questions and outputs per setup

"Measured" rows are real outputs from the Lambda or the planner (account 46, 64 files ingested);
the others describe what the setup would return.

**"Which artworks have a certificate of authenticity?"** (3 files of this type)

| Setup | Output |
|---|---|
| Similarity, no cutoff (measured) | 63 assets, real matches mixed with noise |
| Similarity + cutoff 0.2 / max 10 | ~4 assets: the 3 matches plus a loan form |
| Strict category (planner measured: `CERTIFICATE_OF_AUTHENTICITY`, no document query) | Exactly the assets with a certificate, no scores |

**"What does the insurance policy say about transit?"**

| Setup | Output |
|---|---|
| Similarity only | Top chunks may be loan forms that mention insurance |
| Strict category (planner measured: `INSURANCE_POLICY`, query "cover during transit") | Answer drawn only from the policy, with citations |

**"Which assets were bought at a Doyle auction?"**

| Setup | Output |
|---|---|
| Loose category (planner measured: `PROVENANCE`) | Misses assets whose Doyle mention is in another document type |
| Strict category (planner measured: none) + cutoff | All document types searched; top ~10 by score |

**"Which assets were exhibited in museums?"**

| Setup | Output |
|---|---|
| Loose category (planner measured: `EXHIBITION_AND_LITERATURE`) | Only exhibition files; misses a valuation report that lists exhibitions |
| Strict prompt only (planner measured: still `EXHIBITION_AND_LITERATURE`) | Same as loose: the instruction alone was ignored |
| Strict + code check (planner measured: none, "exhibited" is a topic) + cutoff | All document types; top ~10 by score |

**"Show me documents generated in the last year"**

| Setup | Output |
|---|---|
| Today | Cannot be answered: no date field to filter on |
| Upload date | Files uploaded in the last year, whatever their real date |
| Extracted `docDate` | Documents dated in the last year; undated ones excluded |

## Planner test: the three category setups

Real planner outputs for the same 8 questions (`document_category` only).

| Question | Loose | Strict prompt | Strict + code check |
|---|---|---|---|
| Which artworks have a certificate of authenticity? | CERTIFICATE_OF_AUTHENTICITY | CERTIFICATE_OF_AUTHENTICITY | CERTIFICATE_OF_AUTHENTICITY ✓ |
| What does the insurance policy say about transit? | INSURANCE_POLICY | INSURANCE_POLICY | INSURANCE_POLICY ✓ |
| Show me the provenance documents that mention Sotheby's | – | PROVENANCE | PROVENANCE ✓ |
| What is the latest valuation of Silver Shell? | – | VALUATION_REPORTS | VALUATION_REPORTS ✓ |
| Which assets were bought at a Doyle auction? | PROVENANCE ✗ | none | none ✓ |
| Which works were purchased from Christie's? | – | none | none ✓ |
| Which assets were exhibited in museums? | EXHIBITION_AND_LITERATURE ✗ | EXHIBITION_AND_LITERATURE ✗ | none ✓ |
| Which assets are insured for more than 100,000 dollars? | – | INSURANCE_POLICY ✗ | none ✓ |

"–" = not run with that setup.

## Current choices

- `list` results: fixed cutoff, `RETRIEVAL_LIST_MIN_SCORE=0.2`, `RETRIEVAL_LIST_MAX_ASSETS=10`
  (`cut_noise()` in `app/query.py`).
- Document type: strict planner rule plus a code check (`document_category`, `CATEGORY_WORDS`
  and `names_category()` in `app/planner.py`).
- Document dates: not built yet; extraction at ingestion is the recommended next step.
