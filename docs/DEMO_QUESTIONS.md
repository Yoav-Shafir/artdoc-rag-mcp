# Demo questions

Questions for demoing the Overstone MCP connector (`search_documents`), tested against the deployed
Lambda on account 9. Results can change after re-ingestion or code changes: run each one once
before a demo.

The Giacometti documents are dummy test data made by `uv run poe dummy-pdfs`
(`scripts/make_dummy_pdfs.py`): an invoice (HR-2023-0417) and a provenance report (PR-GIA-0813-4),
both uploaded as `PROVENANCE` on asset 2509, *Trois hommes qui marchent (Grand plateau)*.

## Overstone + public data (main demo)

> How much did we pay for Trois hommes qui marchent, and how does that compare with public auction results for the same bronze?

Tested 4 out of 4 times with the same result (11 October 2026).

- **Overstone:** total paid **£24,587,000** (artwork £24,500,000 plus crating, freight, insurance,
  export and condition-report costs), from the invoice, page 2. The tool also says that auction
  results are not in the documents.
- **Public:** a cast of the same bronze sold at Sotheby's New York on 14 November 2022 for
  **$30.2m** ([Sotheby's](https://www.sothebys.com/en/articles/12-alberto-giacometti)).
- **Expected answer:** £24.5m at the March 2023 exchange rate (about $1.22 to the pound) is roughly
  $30m, in line with the public auction price, so the price matches the market.

Don't bring in the February 2020 Christie's London sale: sources disagree on its estimate and the
result wasn't found.

## Overstone only (tested, working)

| Question | Expected answer |
|---|---|
| Who issued the invoice for Trois hommes qui marchent? | Halden & Rowe Fine Art Ltd |
| How much was paid for transit insurance on the Giacometti? | £19,600 (0.08% of £24.5m) |
| Where was Trois hommes qui marchent shipped to, and why was no Swiss VAT charged? | Geneva Freeport, under bond; VAT is due only if the work is released into Switzerland |
| Which assets were sold at Sotheby's London in 2008? | Trois hommes, lot 34, £8.9m (Riley's *Gaillard* also appears as a weak match) |

## Avoid (tested, failing)

| Question | What goes wrong |
|---|---|
| Ownership chain questions ("who owned it…", "how long did each owner hold it…") | The ownership table on page 2 of the provenance report never ranks in the top 3 chunks |
| Art Loss Register certificate number for the Giacometti | "Art Loss Register" locks the search to `ART_LOST_REGISTER` files, which hold only a Van Gogh letter |
| Which exhibitions has it been shown in? / Each auction price? | 0 assets returned |
| Which dealers appear in the provenance…? | Planned as `list`: only a 200-character snippet reaches the client |
| Which of my assets have a provenance from Christie's? | Misses both Giacomettis |

The causes and planned fixes (category as a preference, more chunks, full evidence in MCP `list`
output, BM25) are not implemented yet.
