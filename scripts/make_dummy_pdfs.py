"""Make dummy test PDFs (an invoice and a provenance report) for Giacometti's
"Trois hommes qui marchent (Grand plateau)", to upload to the platform and query.

Early history is based on public records; dealers, buyers, prices and numbers after
2008 are made up. Every page is marked as sample data.

    uv run poe dummy-pdfs [output folder]   (default: dummy_pdfs/)
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

TITLE = "Trois hommes qui marchent (Grand plateau)"

styles = getSampleStyleSheet()
H1 = styles["Title"]
H2 = styles["Heading2"]
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=colors.grey)

# The artwork, shared by both documents
ARTWORK = [
    ["Artist", "Alberto Giacometti (Swiss, 1901-1966)"],
    ["Title", TITLE],
    ["Also known as", "Three Men Walking (Large Plateau)"],
    ["Date", "Conceived in 1948; this example cast in 1950"],
    ["Medium", "Bronze with dark brown patina"],
    ["Edition", "Cast number 4 from an edition of 6, plus artist's proofs"],
    ["Dimensions", "72 x 43 x 41.5 cm (28 3/8 x 16 7/8 x 16 3/8 in.)"],
    ["Weight", "approx. 31 kg"],
    ["Inscriptions", "Signed 'A. Giacometti' and numbered '4/6' on the top of the base"],
    ["Foundry mark", "Stamped 'Alexis Rudier Fondeur Paris' on the back of the base"],
    ["Catalogue raisonne", "Fondation Giacometti database, AGD 813"],
    ["Condition", "Very good. Minor wear to the patina on the base edge; no repairs or restorations"],
]


def table(rows, widths=(45 * mm, 125 * mm), header=False):
    """A plain two-tone table; text wraps inside cells."""
    data = [[Paragraph(str(cell), BODY) for cell in row] for row in rows]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")))
    else:
        style.append(("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2")))
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle(style))
    return t


def footer(canvas, doc):
    """Sample-data mark and page number on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(colors.grey)
    canvas.drawString(20 * mm, 10 * mm, "SAMPLE - test data for Overstone, not a real document")
    canvas.drawRightString(190 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build(path, story):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def invoice(path):
    p = lambda text, style=BODY: Paragraph(text, style)
    story = [
        p("HALDEN &amp; ROWE FINE ART LTD", H1),
        p("14 Bury Street, St James's, London SW1Y 6AL, United Kingdom<br/>"
          "Tel +44 20 7946 0321 | accounts@haldenrowe.example | VAT GB 284 6619 03 | Company no. 08817342", SMALL),
        Spacer(1, 6 * mm),
        p("INVOICE", H2),
        table([
            ["Invoice number", "HR-2023-0417"],
            ["Invoice date", "14 March 2023"],
            ["Payment due", "13 April 2023 (30 days)"],
            ["Sale reference", "Private treaty sale, agreement dated 2 March 2023"],
            ["Salesperson", "Eleanor Whitcombe, Director, Modern Sculpture"],
        ]),
        Spacer(1, 5 * mm),
        p("Billed to", H2),
        p("Meridian Collection Trust<br/>c/o Aldous Fiduciaire SA, Rue du Rhone 48, 1204 Geneva, Switzerland<br/>"
          "Client account: MCT-0092 | Contact: Dr. Lukas Brenner, Trustee"),
        p("Ship to", H2),
        p("Ports Francs et Entrepots de Geneve (Geneva Freeport), Route du Grand-Lancy 6, 1227 Les Acacias, "
          "Switzerland, held under bond for the account of Meridian Collection Trust"),
        Spacer(1, 4 * mm),
        p("Artwork", H2),
        table(ARTWORK),
        PageBreak(),
        p("Charges", H2),
        table([
            ["Item", "Description", "Amount (GBP)"],
            ["1", f"Alberto Giacometti, {TITLE}, bronze, 4/6, cast 1950", "24,500,000.00"],
            ["2", "Seller's commission (included in the price above, shown for information)", "0.00"],
            ["3", "Crating in a specialist crate, climate-controlled, shock sensors", "18,750.00"],
            ["4", "Air freight London Heathrow to Geneva, with courier escort (2 people)", "42,300.00"],
            ["5", "Transit insurance, nail to nail, at 0.08% of 24,500,000", "19,600.00"],
            ["6", "UK export licence application and customs clearance", "3,950.00"],
            ["7", "Condition report on arrival by independent conservator", "2,400.00"],
            ["", "Subtotal", "24,587,000.00"],
            ["", "UK VAT: zero-rated, export outside the UK (VAT Act 1994 s.30(6))", "0.00"],
            ["", "Total due", "24,587,000.00"],
        ], widths=(14 * mm, 116 * mm, 40 * mm), header=True),
        Spacer(1, 3 * mm),
        p("Swiss import VAT (8.1%) is not charged on this invoice: the work enters the Geneva Freeport under "
          "bond. VAT becomes due to the Swiss Federal Office for Customs if the work is released for free "
          "circulation in Switzerland."),
        p("Payment", H2),
        table([
            ["Method", "Bank transfer in GBP only. Cheques and cash are not accepted."],
            ["Bank", "Example Private Bank plc, 1 Example Lane, London EC2V 0XX"],
            ["Account name", "Halden &amp; Rowe Fine Art Ltd - Client Account"],
            ["Sort code / account", "00-00-00 / 00000000 (dummy)"],
            ["IBAN", "GB00 EXMP 0000 0000 0000 00 (dummy)"],
            ["SWIFT/BIC", "EXMPGB2L (dummy)"],
            ["Reference", "HR-2023-0417"],
            ["Deposit received", "GBP 2,450,000.00 on 6 March 2023 (10%)"],
            ["Balance due", "GBP 22,137,000.00"],
        ]),
        p("Documents and compliance", H2),
        table([
            ["UK export licence", "Arts Council England licence no. ACE-EL-2023-01188, issued 9 March 2023"],
            ["Art Loss Register", "Search certificate S00241867, dated 1 March 2023: no match found"],
            ["Anti-money laundering", "Buyer identity and ultimate beneficial owners verified on 28 February 2023 "
                                      "under the Money Laundering Regulations 2017 (file AML-23-071)"],
            ["Authenticity", "Work recorded in the Fondation Giacometti database as AGD 813; "
                             "authenticity committee letter ref. FG-AUTH-2022-356 dated 18 November 2022"],
            ["Artist's resale right", "Not applicable: private sale between non-UK resident parties outside "
                                      "the scope of the Artist's Resale Right Regulations 2006"],
            ["Documents enclosed", "Provenance report (ref. PR-GIA-0813-4), condition report dated "
                                   "1 March 2023, copy of export licence, Art Loss Register certificate"],
        ]),
        p("Terms", H2),
        p("Title to the work passes to the buyer only when the full amount has been received in cleared funds. "
          "Risk passes on collection by the buyer's shipper. Interest of 1.5% per month is charged on late "
          "payments. This sale is governed by the laws of England and Wales; the courts of London have "
          "exclusive jurisdiction. Halden &amp; Rowe warrants that it has good title to sell the work free of "
          "liens and that, to the best of its knowledge, the description above is accurate."),
        Spacer(1, 8 * mm),
        p("For Halden &amp; Rowe Fine Art Ltd<br/><br/>Eleanor Whitcombe, Director"),
    ]
    build(path, story)


def provenance(path):
    p = lambda text, style=BODY: Paragraph(text, style)
    story = [
        p("PROVENANCE REPORT", H1),
        p(f"Alberto Giacometti, <i>{TITLE}</i>, bronze, cast 4/6", H2),
        table([
            ["Report reference", "PR-GIA-0813-4"],
            ["Prepared by", "Northbridge Provenance Research LLP, 22 Pemberton Row, London EC4A 3BA"],
            ["Researcher", "Dr. Sophie Lindqvist, Senior Provenance Researcher"],
            ["Prepared for", "Halden &amp; Rowe Fine Art Ltd, for the sale to Meridian Collection Trust"],
            ["Date", "27 February 2023"],
        ]),
        Spacer(1, 4 * mm),
        p("Artwork", H2),
        table(ARTWORK),
        p("Summary", H2),
        p("The chain of ownership is complete from the artist to the present owner. There are no gaps during "
          "1933-1945 (the work was made after 1945). No claims, losses or thefts are recorded. The early "
          "history follows the pattern of the other casts of this edition, sold by the artist through "
          "Pierre Matisse Gallery in New York."),
        PageBreak(),
        p("Chain of ownership", H2),
        table([
            ["Dates", "Owner", "How acquired / evidence"],
            ["1948-1950", "The artist, Paris", "Conceived in plaster in the studio at 46 rue Hippolyte-Maindron; "
                                               "cast by Alexis Rudier in 1950"],
            ["Feb 1950", "Pierre Matisse Gallery, New York", "Bought from the artist; gallery stock book, "
                                                             "Pierpont Morgan Library archive, stock no. 4987"],
            ["Apr 1951", "Joseph Cantor, Indianapolis", "Bought from Pierre Matisse Gallery; invoice dated "
                                                        "12 April 1951, price USD 1,800"],
            ["1951-1982", "By descent in the Cantor family", "Lent to exhibitions in 1961 and 1974 (see below)"],
            ["May 1982", "Sidney Janis Gallery, New York", "Bought from the Cantor estate; stock no. SJ-82-114"],
            ["Nov 1982", "Private collection, Chicago", "Bought from Sidney Janis Gallery, USD 410,000"],
            ["May 1994", "Christie's New York, sale of 10 May 1994, lot 63", "Sold by the Chicago collector; "
                                                                           "hammer price USD 2,200,000"],
            ["1994-2008", "Private collection, Zurich", "Bought at the 1994 sale"],
            ["Jun 2008", "Sotheby's London, sale of 24 June 2008, lot 34", "Sold by the Zurich collector; "
                                                                           "hammer price GBP 8,900,000"],
            ["2008-2023", "Private collection, Hong Kong (the seller)", "Bought at the 2008 sale; consigned to "
                                                                       "Halden &amp; Rowe in January 2023"],
            ["Mar 2023", "Meridian Collection Trust, Geneva", "Private treaty sale through Halden &amp; Rowe, "
                                                             "GBP 24,500,000 (invoice HR-2023-0417)"],
        ], widths=(24 * mm, 56 * mm, 90 * mm), header=True),
        p("Exhibitions", H2),
        table([
            ["Year", "Exhibition", "Venue"],
            ["1950", "Alberto Giacometti", "Pierre Matisse Gallery, New York, 7-30 November"],
            ["1961", "Sculpture of the Twentieth Century (lent by Joseph Cantor)", "Herron Museum of Art, Indianapolis"],
            ["1974", "Giacometti: Sculpture and Drawings (lent by the Cantor family)", "Indianapolis Museum of Art"],
            ["1998", "Giacometti and Paris", "Kunsthalle Bremen (lent by the Zurich collector)"],
            ["2017", "Giacometti", "Tate Modern, London, 10 May-10 September (lent anonymously)"],
        ], widths=(16 * mm, 84 * mm, 70 * mm), header=True),
        p("Literature", H2),
        p("- J. Dupin, <i>Alberto Giacometti</i>, Paris, 1962, p. 211 (another cast illustrated).<br/>"
          "- R. Hohl, <i>Alberto Giacometti</i>, Stuttgart, 1971, p. 291 (another cast illustrated).<br/>"
          "- Christie's New York, <i>Impressionist and Modern Art</i>, 10 May 1994, lot 63 (this cast illustrated in colour).<br/>"
          "- Sotheby's London, <i>Impressionist and Modern Art Evening Sale</i>, 24 June 2008, lot 34 (this cast illustrated).<br/>"
          "- Fondation Giacometti online database, AGD 813."),
        p("Checks carried out", H2),
        table([
            ["Art Loss Register", "Certificate S00241867, 1 March 2023: no match on the stolen art database"],
            ["Interpol stolen works of art", "Searched 24 February 2023: no record"],
            ["German Lost Art Foundation", "lostart.de searched 24 February 2023: no record"],
            ["Commission for Looted Art in Europe", "Searched 24 February 2023: no record"],
            ["Fondation Giacometti", "Cast 4/6 confirmed in the database; authenticity letter FG-AUTH-2022-356"],
            ["Foundry records", "Rudier casting confirmed by the foundry mark; no other cast 4/6 recorded"],
        ]),
        p("Conclusion", H2),
        p("In our opinion the provenance of this bronze is complete and clean. We found no evidence of theft, "
          "looting, forced sale or any outstanding claim. This report reflects the records available on "
          "27 February 2023 and should be renewed before any future sale."),
        Spacer(1, 8 * mm),
        p("Dr. Sophie Lindqvist<br/>Northbridge Provenance Research LLP"),
    ]
    build(path, story)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "dummy_pdfs")
    out.mkdir(parents=True, exist_ok=True)
    invoice(out / "Giacometti Trois hommes Invoice HR-2023-0417.pdf")
    provenance(out / "Giacometti Trois hommes Provenance PR-GIA-0813-4.pdf")
    print(f"Wrote 2 PDFs to {out}/")


if __name__ == "__main__":
    main()
