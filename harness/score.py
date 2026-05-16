"""harness/score.py — score a task result against its battery criteria.

Scoring has two tiers:
  Structural — keyword presence/absence checks run locally (instant, free).
  Outcome    — workspace diff checks: did the agent actually change the right things?
  LLM        — reserved for EvalJudge; set to None here, filled in by the judge agent.

Each criterion in battery task JSON:
  {
    "type": "keyword" | "keyword_absent" | "diff_contains" | "diff_absent" | "llm",
    "dimension": "name_of_dimension",
    "terms": ["word1", "word2"],   // for keyword/diff types
    "criterion": "human text",     // for llm type (EvalJudge reads this)
  }

score() returns:
  {
    "dimensions": {"dim_name": true | false | null},   // null = LLM (pending)
    "total": int,
    "max_score": int,
  }
"""
from __future__ import annotations


def _keyword_hit(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(t.lower() in lower for t in terms)


def score(task: dict, result: dict) -> dict:
    """
    Score a task result against the battery's scoring criteria.

    task   — the task dict from battery JSON (has "scoring_criteria" list)
    result — the dict returned by invoke.run_task()
    """
    criteria: list[dict] = task.get("scoring_criteria", [])
    response_text = result.get("final_response", "")
    workspace_diff = result.get("workspace_diff", "")

    dimensions: dict[str, bool | None] = {}
    total = 0
    max_score = 0

    for criterion in criteria:
        dim = criterion["dimension"]
        kind = criterion["type"]
        terms: list[str] = criterion.get("terms", [])

        max_score += 1

        if kind == "keyword":
            passed = _keyword_hit(response_text, terms)
            dimensions[dim] = passed
            if passed:
                total += 1

        elif kind == "keyword_absent":
            # Passes when NONE of the terms appear
            passed = not _keyword_hit(response_text, terms)
            dimensions[dim] = passed
            if passed:
                total += 1

        elif kind == "diff_contains":
            # Workspace diff must contain all terms
            passed = all(t in workspace_diff for t in terms)
            dimensions[dim] = passed
            if passed:
                total += 1

        elif kind == "diff_absent":
            # Workspace diff must NOT contain any of the terms
            passed = not any(t in workspace_diff for t in terms)
            dimensions[dim] = passed
            if passed:
                total += 1

        elif kind == "llm":
            # Deferred to EvalJudge — mark as None (pending)
            dimensions[dim] = None
            # max_score still counts; EvalJudge will fill in the point

        else:
            # Unknown type — skip without penalising
            max_score -= 1

    return {
        "dimensions": dimensions,
        "total": total,
        "max_score": max_score,
    }
