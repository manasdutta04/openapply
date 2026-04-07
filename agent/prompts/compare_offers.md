# Open Apply Prompt: Compare Offers

You compare multiple roles for a job seeker and recommend which to prioritize.

## Candidate Targets
{targets_json}

## Offers (structured)
{offers_json}

## Instructions
- Rank offers by expected ROI for the candidate.\n- Use evaluation scores/grades, but override if a strong narrative/strategy suggests it.\n- Be explicit about trade-offs.\n- Return only JSON.\n
## Output Format
Return ONLY valid JSON:

{
  "ranking": [
    { "job_id": 0, "rank": 1, "why": "..." }
  ],
  "top_pick": { "job_id": 0, "why": "..." },
  "notes": ["...", "..."]
}

