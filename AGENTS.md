# AGENTS.md — JukeBox101

## 1. Project identity and scope

JukeBox101 is a Python Discord music-exchange bot. Users submit a YouTube or Spotify song link to earn one exchange credit, then spend credits to receive a YouTube song link recommended by another user. Users can exchange immediately or opt into a daily direct-message delivery. Build locally first and keep the application deployable on a low-cost/free Linux server with MySQL.

- Python 3.12, `discord.py`, MySQL 8.0+, `PyMySQL`, native parameterized SQL, and `pytest`.
- Do not introduce an ORM, a different database, paid services, AI models, or new runtime dependencies without explicit user approval.
- Use Traditional Chinese for user-facing development reports. Keep code identifiers and technical docstrings consistent and readable in English.
- Implement only the currently approved phase; do not assume this document authorizes the entire roadmap.

## 2. MVP behavior

- `/share <url>` accepts a valid YouTube video URL or Spotify track URL. Reject unsupported or ambiguous inputs with a clear message.
- A *new, eligible song* earns one exchange credit after successful persistence. Re-submitting an already known song does not earn credits in the MVP, regardless of who submits it.
- `/exchange` consumes one credit only when a previously unseen, eligible song from another user is successfully reserved for delivery.
- `/balance` shows available credits; `/daily on` and `/daily off` control opt-in daily delivery; `/history` lists recent deliveries.
- Daily delivery uses Discord DMs, at most one automatic delivery per user per configured local calendar day, and only when credits and eligible songs exist. Define timezone explicitly; do not infer it from the server.
- Every delivered recommendation must be a verified YouTube URL. Do not deliver a Spotify URL as the exchange result.
- If no eligible unseen song exists, preserve the credit and explain why.
- For MVP, treat the song catalog and duplicate rules as global across all participating servers; any later server isolation needs explicit approval.

## 3. Song identity, cross-platform matching, and YouTube resolution

- Parse and normalize supported URLs; remove tracking parameters, validate hostnames, and extract platform-specific IDs. Never rely on raw URL equality.
- Enforce uniqueness for `(platform, platform_track_id)` at the database level. Use a canonical internal `song_id` for identity and separate platform links/identifiers from that identity.
- A YouTube video ID and a Spotify track ID do **not** prove two links represent different songs. Conversely, matching titles alone does **not** prove they are the same recording.
- Cross-platform matching is a separate, evidence-based process. Prefer reliable identifiers and approved metadata sources; distinguish original recordings, live versions, remixes, covers, and alternate releases when evidence permits.
- Do not silently guess a YouTube equivalent for Spotify submissions. If a reliable, permitted resolution method is not yet implemented or confidence is insufficient, mark the submission as pending/unresolved and **do not award credits or add it to the exchange pool** until a valid YouTube target is verified.
- A valid YouTube URL is not by itself proof that a video is a song. Specify and test an appropriate eligibility/verification policy before accepting submissions for credit.
- Do not scrape platforms, bypass access controls, download copyrighted audio, or assume API quotas/terms. Obtain approval before adding external APIs or credentials.
- Preserve source URL, canonical identity, YouTube delivery URL, resolution status, and provenance of any match. Never fabricate metadata.

## 4. Recommendation and fairness

- Maintain durable per-user recommendation history, including successful deliveries.
- Eligible candidates exclude songs submitted by the recipient and songs previously delivered to that recipient; select randomly from the remaining pool.
- Never reset or discard recommendation history merely because the candidate pool is exhausted. New songs can become eligible later.
- Prevent concurrent `/exchange` requests and daily jobs from selecting the same song twice for one user or spending the same credit twice.
- Model credits as auditable transactions (e.g., a credit ledger) and use MySQL transactions, row locks, and unique constraints as needed.
- Design explicit states for reservation, sending, success, and failure. Handle Discord DM failures and process restarts without silently losing credits or incorrectly claiming delivery. Document the unavoidable uncertainty if Discord accepts a message but the database update fails; do not promise exactly-once external delivery without evidence.
- Ensure scheduled jobs are idempotent for each user and calendar day. A restart must not automatically resend a day's recommendation.

## 5. Database and async boundaries

- Use native SQL through PyMySQL with `%s` placeholders; never interpolate user input into SQL identifiers or values.
- Keep SQL in a database/repository layer, business rules in services, and Discord interactions in command handlers/cogs.
- PyMySQL is synchronous. Execute blocking database operations outside the Discord event loop (for example with `asyncio.to_thread`); create/use/close each connection in its worker context, never share a connection concurrently across threads.
- Close connections and transactions deterministically. Handle commit/rollback correctly and keep transactions short.
- Use migrations or reviewed versioned SQL scripts for schema changes. Never delete production data or run destructive migrations without explicit approval.
- Keep MySQL credentials, Discord token, and optional API keys in environment variables; commit only a sanitized `.env.example`.
- Do not rely on persistent local disk in cloud deployment. Keep Linux compatibility and free-tier resource constraints in mind.

## 6. Rule One — Strict complexity controls

1. **Function length:** A function's implementation must not exceed **25 non-comment, non-blank lines**. Extract single-purpose private helpers when necessary. Keep documentation outside the implementation line count; do not compress statements onto one line to evade the rule.
2. **Nesting:** Do not exceed **3 levels** of nested `if`, loops, or similar control structures. Prefer guard clauses and early returns.
3. **No reinvention:** Before implementing logic, inspect existing project helpers, framework facilities, shared constants, and the Python standard library. Reuse established functionality rather than duplicating date formatting, URL parsing, validation, or other utilities.
4. **Exception boundaries:** Do not scatter repetitive `try/except` blocks or redundant `None` checks. Trust validated contracts where appropriate; handle exceptions at meaningful boundaries (Discord commands, scheduled jobs, transaction units). Catch specific exceptions, preserve actionable logs, and never suppress failures silently.
5. **Cyclomatic complexity:** In addition to the limits above, target a cyclomatic complexity of **10 or less per function**. If tooling is introduced to enforce this, request approval before adding a dependency.

## 7. Rule Two — Self-documenting names

- Use names that communicate domain intent and business meaning, even when longer.
- Avoid vague names such as `temp`, `data`, `flag`, `res`, `chk`, or single-letter variables except conventional loop counters.
- Prefer `recipient_discord_user_id`, `available_exchange_credits`, `canonical_song_id`, and `delivery_attempt_status` over generic alternatives.
- Replace magic numbers with named constants or configuration, e.g. `MAX_DAILY_DELIVERIES_PER_USER = 1`.
- Follow Python naming conventions: `snake_case` for variables/functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.

## 8. Rule Three — Standardized self-explaining documentation

- **Module-level docstring:** Every Python source module begins with a 2–3 sentence plain-language docstring describing its core responsibility, essential inputs/dependencies, and affected outputs or subsystem.
- **Class documentation:** Every nontrivial class has a docstring describing its responsibility and its relevant collaborators or state.
- **Function documentation:** Every function, including private helpers, has a Python docstring with the following labeled sections:
  - `@description:` One sentence explaining **why the function exists**, not merely restating its name.
  - `@param <name>:` The business meaning of each parameter; use `@param: none` if there are no parameters.
  - `@return:` The business meaning of the returned value, or `None` if no value is returned.
- **Core variables:** At the first declaration of each core state/business variable, include a useful Python inline comment on that line or directly above it, in the form `# [purpose]: explanation of what the variable tracks, stores, or controls`. Do not annotate every trivial temporary expression.
- Use Python `#` comments, not JavaScript `//` comments. Documentation should add intent and invariants, not repeat obvious syntax.

Example:

```python
"""Resolve a user's remaining exchange credits.

Reads the approved credit ledger and returns the spendable balance to the service layer.
"""


def calculate_available_credits(earned_credits: int, spent_credits: int) -> int:
    """@description: Keep credit accounting consistent across exchange entry points.
    @param earned_credits: Credits awarded for accepted unique songs.
    @param spent_credits: Credits committed to successful exchanges.
    @return: Number of credits the user may still spend.
    """
    # [available exchange credits]: Credits that remain after committed exchanges.
    available_exchange_credits = earned_credits - spent_credits
    return available_exchange_credits
```

## 9. Testing and acceptance

- Use `pytest` for pure service logic and integration tests against a disposable MySQL database where available.
- Test URL parsing and tracking parameters, duplicates, cross-platform unresolved states, song exclusion, credit integrity, concurrency, and daily-job idempotency.
- Mock Discord interactions in unit tests; do not claim live Discord behavior has been verified without a real test.
- Report passed, failed, and skipped tests separately; distinguish automated results from manual acceptance.
- For every code change, report modified files, tests run, known limitations, and next steps.

## 10. Security, Git, and change control

- Request only necessary Discord intents and permissions; avoid logging private user content, tokens, or credentials.
- Never commit `.env`, secrets, database dumps, local virtual environments, or generated runtime files.
- Do not force-push, rewrite Git history, merge branches, or deploy without explicit approval.
- Do not install packages, call paid APIs, add AI models, change the database engine, or expand the current phase without explicit approval.
- Keep `README.md` and roadmap status accurate; do not claim a feature is complete while known blocking issues remain.

## 11. Initial implementation order

1. Create a minimal Python project, configuration, database connection boundary, tests, and documented MySQL schema.
2. Implement strict YouTube/Spotify URL parsing and a clearly defined pending/verified song resolution workflow.
3. Implement `/share` with canonical identity, unique constraints, and transactional credit award.
4. Implement `/exchange`, durable delivery history, and concurrency-safe credit handling.
5. Implement `/balance`, `/history`, and opt-in daily DM scheduling with restart safety.
6. Test locally before preparing an approved free-tier Linux deployment plan.

Do not automatically execute all steps. Implement only the step explicitly requested by the user.
