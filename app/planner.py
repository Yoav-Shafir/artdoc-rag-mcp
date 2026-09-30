"""Query planner: turns a user's prompt into database filters + a document query.

Example:
    "Show me Picasso paintings from 1959-1970 whose provenance includes Doyle"
    ->  filters:        artist="Pablo Picasso", medium="PAINTING", year_from=1959, year_to=1970
        document_query: "provenance includes Doyle"

The LLM only *translates*: it fills in a fixed form (the QueryPlan model below)
and never writes SQL. Our code later turns the filters into SQL and always adds
accountId and deleted = 0 itself.

Run: uv run poe planner   (prints the plan for a few sample prompts)
"""

import difflib
import logging
from enum import Enum
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.happy_path import make_llm

logger = logging.getLogger(__name__)

# --- Fixed value lists (copies of MySQL values) --------------------------------
# The LLM may only pick one of these values. Pydantic rejects anything else,
# so an invented value (e.g. "PAINTINGS") never reaches the SQL.


class AssetType(str, Enum):
    """asset.type in MySQL. The comment after each value is its readable name."""

    FINE_ART = "FINE_ART"  # Fine Art
    WINE = "WINE"  # Wine
    FURNITURE = "FURNITURE"  # Furniture
    DESIGN = "DESIGN"  # Design
    AMERICAN_FOLK_ART = "AMERICAN_FOLK_ART"  # American Folk Art
    MUSICAL_INSTRUMENTS = "MUSICAL_INSTRUMENTS"  # Musical Instruments
    AFRICAN_AND_OCEANIC_ART = "AFRICAN_AND_OCEANIC_ART"  # African & Oceanic Art
    DEC_ART = "DEC_ART"  # Decorative Art
    AUDIO = "AUDIO"  # Audio
    CARPETS = "CARPETS"  # Carpets
    CARS_MOTORCYCLES = "CARS_MOTORCYCLES"  # Cars & Motorcycles
    WATCHES = "WATCHES"  # Watches
    ANTIQUITIES = "ANTIQUITIES"  # Antiquities
    FASHION = "FASHION"  # Fashion
    JEWELLERY = "JEWELLERY"  # Jewellery


class ConditionStatus(str, Enum):
    """asset.conditionStatus in MySQL."""

    VERY_GOOD = "VERY_GOOD"
    GOOD = "GOOD"
    AVERAGE = "AVERAGE"
    POOR = "POOR"
    VERY_POOR = "VERY_POOR"


class Medium(str, Enum):
    """category rows with type = 'medium' (asset.medium stores their id)."""

    PAINTING = "PAINTING"  # category.id 6
    PAPER_WATERCOLOR = "PAPER/WATERCOLOR"  # category.id 7
    THREE_D = "3D"  # category.id 8
    PRINT = "PRINT"  # category.id 9
    PHOTOGRAPH = "PHOTOGRAPH"  # category.id 10


class Period(str, Enum):
    """category rows with type = 'period' (asset.period stores their id)."""

    OLD_MASTERS = "OLD MASTERS (pre-1880)"  # category.id 1
    IMP_MOD = "IMP & MOD (1880-1945)"  # category.id 2
    POST_WAR = "POST-WAR (1945-1970)"  # category.id 3
    CONTEMPORARY = "CONTEMPORARY (1970-2000)"  # category.id 4
    C21 = "21st CENTURY (post-2000)"  # category.id 5


def closest_value(value, enum: type[Enum]):
    """Map an LLM value to the closest allowed enum value, instead of dropping it.

    "POST-WAR" -> "POST-WAR (1945-1970)", "painting" -> "PAINTING".
    Returns None (and logs a warning) when nothing is close, so an unknown value
    is visible in the logs rather than silently ignored.
    """
    if value is None or isinstance(value, enum):
        return value
    allowed = [member.value for member in enum]
    text = str(value).strip().upper()
    # 1. Same text, ignoring case: "painting" -> "PAINTING".
    for option in allowed:
        if text == option.upper():
            return option
    # 2. The value is the start of an allowed name: "POST-WAR" -> "POST-WAR (1945-1970)".
    starts = [option for option in allowed if option.upper().startswith(text)]
    if len(starts) == 1:
        logger.warning(f"Interpreted {value!r} as {starts[0]!r}")
        return starts[0]
    # 3. Similar spelling: "PHOTOGRAPHS" -> "PHOTOGRAPH".
    close = difflib.get_close_matches(text, [o.upper() for o in allowed], n=1, cutoff=0.8)
    if close:
        match = allowed[[o.upper() for o in allowed].index(close[0])]
        logger.warning(f"Interpreted {value!r} as {match!r}")
        return match
    logger.warning(f"Unrecognized {enum.__name__} {value!r}: filter ignored")
    return None


# --- The form the LLM fills in ------------------------------------------------
# Every field is optional (None): the LLM fills in only what the prompt mentions.
# The field descriptions are the LLM's only documentation, so they say exactly
# what each field means and how to fill it.


class AssetFilters(BaseModel):
    """Exact filters on asset columns. Leave a field empty unless the prompt asks for it."""

    asset_type: AssetType | None = Field(
        None,
        description="Broad asset class. Fine Art covers paintings, drawings, prints, "
        "photographs and sculpture; DEC_ART means Decorative Art.",
    )
    artist: str | None = Field(
        None, description="Artist or maker name, as written by the user, e.g. 'Pablo Picasso'."
    )
    year_from: int | None = Field(None, description="Earliest year the work was made.")
    year_to: int | None = Field(None, description="Latest year the work was made.")
    # The descriptions list the words users say for each value (aliases). Enum
    # member comments are NOT sent to the LLM, so the aliases must live here.
    medium: Medium | None = Field(
        None,
        description="Medium of the work. PAINTING = paintings; PAPER/WATERCOLOR = works on "
        "paper, drawings, watercolours; 3D = sculptures, objects, installations; "
        "PRINT = prints, etchings, lithographs; PHOTOGRAPH = photographs.",
    )
    period: Period | None = Field(
        None,
        description="Art-historical period. OLD MASTERS (pre-1880) = old masters, before 1880; "
        "IMP & MOD (1880-1945) = impressionist, modern, early 20th century; "
        "POST-WAR (1945-1970) = post-war, mid-century; "
        "CONTEMPORARY (1970-2000) = contemporary, late 20th century; "
        "21st CENTURY (post-2000) = 21st century, recent, after 2000.",
    )
    condition_status: ConditionStatus | None = Field(
        None, description="Physical condition of the asset."
    )

    # Runs before Pydantic checks the enum: fixes near-misses like "POST-WAR"
    # instead of failing or dropping them. See closest_value() above.
    
    # This is a Pydantic validator that fixes the user's input before validation.
    # Runs on medium and period fields
    # mode="before" means run BEFORE Pydantic validates the enum
    @field_validator("medium", "period", mode="before")
    @classmethod
    def _closest_allowed(cls, value, info: ValidationInfo):
        return closest_value(value, Medium if info.field_name == "medium" else Period)


class QueryPlan(BaseModel):
    """How to handle one user prompt."""

    # The LLM must choose one of the two options
    kind: Literal["answer", "list"] = Field(
        # A description to guide the LLM
        description="'list' when the user wants a list of assets ('show me all ...', "
        "'which assets ...'); 'answer' when they ask a question about the documents."
    )
    filters: AssetFilters = Field(
        # If the LLM doesn't fill it, use default_factory=AssetFilters to create an empty AssetFilters() instance
        default_factory=AssetFilters,
        description="Filters on asset columns. All empty if the prompt names none.",
    )
    document_query: str | None = Field(
        # If the LLM doesn't fill it, default to None
        None,
        description="Everything the prompt asks about the *content of documents* "
        "(provenance, invoices, reports, ...), rewritten as a short search query. "
        "Empty if the prompt only uses asset filters.",
    )


# --- Instructions for the LLM -------------------------------------------------

PLANNER_PROMPT = """You turn a user's request about their art and asset collection into a query plan.
Put anything that matches an asset filter into `filters`; put everything about the
content of documents into `document_query`. Never put the same condition in both.
Only fill in what the user actually asked for.

Examples:
- "from the sixties" -> year_from 1960, year_to 1969
- "paintings" -> medium PAINTING (and asset_type FINE_ART only if the user says fine art)
- "sculptures" -> medium 3D
- "post-war" -> period POST-WAR (1945-1970)
"""


def plan(prompt: str, llm) -> QueryPlan:
    """Ask the LLM to fill in a QueryPlan for one prompt."""
    # with_structured_output sends the QueryPlan schema (including every allowed
    # enum value) to the LLM as a "tool", and parses its reply back into a
    # validated QueryPlan object.

    # tells the LLM: "Your response must be valid JSON with these fields:
    # kind: 'answer' or 'list'
    # filters: (object with artist, year, etc.)
    # document_query: (string) "
    
    planner = llm.with_structured_output(QueryPlan)
    return planner.invoke([SystemMessage(PLANNER_PROMPT), HumanMessage(prompt)])


# Sample prompts covering the four cases: filters + documents, filters only,
# documents only (list), and a plain question.
SAMPLE_PROMPTS = [
    "Show me Picasso paintings from 1959 to 1970 whose provenance includes Doyle",
    "Show me all post-war sculptures in very good condition",
    "Which assets were bought at a Doyle auction?",
    "Who issued the invoice for Half of You?",
]

# uv run poe planner
def main():
    """Print the plan for each sample prompt, to see how the LLM splits it."""
    llm = make_llm()
    for prompt in SAMPLE_PROMPTS:
        result = plan(prompt, llm)
        print(f"\nPROMPT: {prompt}")
        print(f"  kind:           {result.kind}")
        # exclude_none: only show the filters the LLM actually filled in.
        print(f"  filters:        {result.filters.model_dump(exclude_none=True, mode='json')}")
        print(f"  document_query: {result.document_query}")


if __name__ == "__main__":
    main()
