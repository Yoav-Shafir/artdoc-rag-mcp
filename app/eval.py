"""Evaluate search and answers against a fixed set of questions.

Uses the chunks already stored by the pipeline (run `uv run poe pipeline` first).
Run: uv run poe eval
"""

import json

from app.config import config
from app.db import Database
from app.happy_path import (
    ACCOUNT_ID,
    answer,
    make_collection,
    make_embeddings,
    make_llm,
    search,
)

QUESTIONS_FILE = config.project_root / "evals" / "questions.json"


def main():
    """Ask every eval question, score search and answer, print a summary."""
    questions = json.loads(QUESTIONS_FILE.read_text())
    db, embeddings, collection, llm = Database(), make_embeddings(), make_collection(), make_llm()

    hit1 = hit3 = retrieval_total = answers_ok = 0
    for number, case in enumerate(questions, start=1):
        # The same two calls the pipeline makes, so we measure the real thing.
        hits = search(db, collection, embeddings, case["question"], ACCOUNT_ID)
        answer_text = answer(llm, case["question"], hits)

        # Retrieval: was the expected file ranked first, or anywhere in the top 3?
        # Skipped when no file contains the answer (expected_file is null).
        found_files = [hit["fileName"] for hit in hits]
        expected = case["expected_file"]
        if expected is not None:
            retrieval_total += 1
            hit1 += found_files[0] == expected
            hit3 += expected in found_files

        # Answer: must contain every expected phrase (case-insensitive, exact spelling).
        missing = [p for p in case["must_contain"] if p.lower() not in answer_text.lower()]
        answers_ok += not missing

        # One line per question, plus details when the answer failed.
        rank = "n/a " if expected is None else ("hit1" if found_files[0] == expected
                                               else "hit3" if expected in found_files else "MISS")
        print(f"{number}. [{rank}] [{'PASS' if not missing else 'FAIL'}] {case['question']}")
        if missing:
            print(f"     missing: {missing}")
            print(f"     answer:  {' '.join(answer_text.split())[:200]}")

    print(f"\nRetrieval: hit@1 {hit1}/{retrieval_total}, hit@3 {hit3}/{retrieval_total}")
    print(f"Answers:   {answers_ok}/{len(questions)} contain all expected facts")


if __name__ == "__main__":
    main()
