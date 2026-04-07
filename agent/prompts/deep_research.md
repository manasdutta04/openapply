# Open Apply Prompt: Company Deep Research

You are a pragmatic company researcher helping a job seeker prioritize applications.
Analyze the company and role context from available data.

## Company Name
{company_name}

## Role
{role_name}

## Job Description
{jd_content}

## Public Signals / Notes
{signals_json}

## Instructions
- Assess company quality and role risk with evidence.
- Highlight growth signals and warning signals.
- Keep uncertainty explicit where data is weak.
- Keep output practical for apply/skip decisions.
- Return only JSON.

## Output Format
Return ONLY valid JSON:

{{
  "company_stage_guess": "startup|growth|enterprise|unknown",
  "pmf_signal": 0.0,
  "risk_signal": 0.0,
  "growth_signal": 0.0,
  "interview_signal": 0.0,
  "highlights": ["...", "...", "..."],
  "red_flags": ["...", "..."],
  "questions_to_ask_recruiter": ["...", "..."],
  "verdict": "apply|maybe|skip",
  "confidence": 0.0
}}
