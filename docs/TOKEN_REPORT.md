# Token report

This report shows what the agents read and write, what changed, and what each kept change saved. Each number says how it was measured. A number marked "estimate" comes from a replay of real sessions and not from a live run.

Rule of this report: a change stays only when the numbers get better, or when Prompt 3 asks for it by name. Each row below says which case it is.

## How the numbers were measured

| Source | What it gives |
|---|---|
| `hermes -p AGENT insights --days 3` | Sessions, messages, tool calls, and tokens of each agent. The total includes tokens that the provider read from its prompt cache. |
| `hermes -p AGENT prompt-size` | The size of the system prompt and of the tool schemas of one fresh request. It runs offline and calls no model. |
| The router analytics (`/api/usage/analytics`, `/api/cache`) | Requests and tokens for each key and each model, and the prompt cache and semantic cache numbers. |
| A replay of the six real research sessions | The size of each tool result, and what a prune and a size cap would have saved. Tokens are characters divided by 4. |
| Small tests on this machine | The RTK and TOON savings, and the semantic cache hit. |
| The usage fields of the Claude Code transcripts | The tokens that the builder used. |

## 1. Before

The window is the 3 days up to 2026-11-15 09:00 CST. The fleet was in its first days of work, so the numbers are small and uneven.

### 1.1 Each agent

| Agent | Sessions | Messages | Tool calls | Input tokens | Output tokens | All tokens | Tokens per message |
|---|---|---|---|---|---|---|---|
| orchestrator | 9 | 186 | 95 | 326,693 | 23,184 | 1,757,141 | 9,446 |
| research | 7 | 576 | 301 | 643,734 | 74,346 | 10,982,400 | 19,066 |
| build | 1 | 0 | 0 | 0 | 0 | 0 | none |
| marketing | 4 | 92 | 44 | 101,919 | 10,351 | 1,567,438 | 17,037 |
| feedback | 15 | 6 | 2 | 11,928 | 492 | 18,756 | 3,126 |
| security | 7 | 140 | 68 | 123,594 | 25,770 | 1,418,676 | 10,133 |
| improver | 1 | 0 | 0 | 0 | 0 | 0 | none |
| all seven | 44 | 1,000 | 510 | 1,207,868 | 134,143 | 15,744,411 | 15,744 |

"All tokens" counts the whole history that each request sent again, including the part that the provider read from its cache. The input column counts only the new part.

The research agent used 70 percent of all tokens. It finished 4 cards in 6 sessions. That is about 2.7 million tokens for each card, of which about 160,000 were new input.

### 1.2 The fixed size of a request

This is what each request carries before the first word of the task. Bytes of the system prompt plus bytes of the tool schemas, on the CLI platform.

| Agent | System prompt | Tool schemas | Tools |
|---|---|---|---|
| orchestrator | 18,460 B | 44,046 B | 26 |
| research | 16,219 B | 19,901 B | 14 |
| build | 15,474 B | 17,970 B | 12 |
| marketing | 16,333 B | 19,901 B | 14 |
| feedback | 13,582 B | 9,662 B | 7 |
| security | 16,095 B | 17,970 B | 12 |
| improver | 14,538 B | 17,970 B | 12 |

The orchestrator has the most tools because the Kanban toolset has 14 tools in one unit. Hermes cannot trim inside a toolset. Four of the toolsets together hold 60 percent of the schema bytes of the research agent: file, skills, terminal, and memory.

### 1.3 The router

| Number | Value |
|---|---|
| Requests in 3 days | 547, of which 497 succeeded (90.9 percent) |
| Tokens | 16,251,693 (prompt 16,063,745, completion 187,948) |
| Prompt cache at the NVIDIA provider | 14,691,744 of 15,651,565 input tokens, or 94 percent |
| Share of the `fleet-worker` combo | 87 percent of the prompt tokens |
| Failed requests | 52: unknown 33, server error 14, rate limited 3, forbidden 2 |

The prompt cache already works. The SOUL files and the tool schemas are stable, and the changing parts sit at the end. So the cost is in the size of the history that each turn sends again, and not in new text.

### 1.4 Where the research tokens went

Tool results are 98 percent of the text in the six research sessions. These tools make the large results.

| Tool | Calls | Characters | Results over 8,000 characters |
|---|---|---|---|
| `web_search` | 169 | 488,502 | 25 (209,215 characters) |
| `terminal` | 76 | 348,205 | 8 (330,040 characters) |
| `kanban_show` | 5 | 43,006 | 3 (30,032 characters) |
| `read_file` | 19 | 41,545 | 0 |
| all other tools | 32 | under 8,000 | 0 |

The size of a tool result: median 386 characters, 90th percentile 8,140, largest 53,815. Five results over 50,000 characters are 28 percent of all tool text.

## 2. What changed

| Change | Kind | Measured result | Kept |
|---|---|---|---|
| `compression.proactive_prune_tokens` is 48000 | Measured | Estimate on the six research sessions: 13.0 percent fewer tokens sent again. It calls no model. | Yes |
| `tool_output.max_bytes` is 16000 | Measured | Estimate on the same sessions: 13.2 percent fewer. Eight terminal results of about 41,000 characters shrink to 16,000. | Yes |
| Both together | Measured | Estimate: 19.6 percent fewer. A harder setting (12,000 and 32,000) gave 21.3 percent. The extra 1.7 points did not justify more loss. | Yes |
| `todo` and `session_search` toolsets off | Measured | 0 calls in 3 days. The fixed request shrinks by 180 bytes for six agents and by 2,357 bytes (10.1 percent) for the feedback agent. 3,443 bytes over all seven agents. | Yes |
| `web.cache_ttl_minutes` is 1440 | Asked for by Prompt 3 | The Hermes extract cache keeps a page on disk for 24 hours. 1,440 minutes is the longest value that Hermes accepts. No gain is measured yet, because no page was read twice in the 3 days. | Yes |
| Compression summary on `fleet-worker` | Asked for by Prompt 3 | No compression ran in the 3 days, so no gain can be measured. Both combos have a window of 262,144 tokens. | Yes |
| MCP result threshold of 16,000 characters | Measured | No MCP result in the 3 days passed 2,200 characters. It would show no gain. | No |
| OmniRoute memory and skills injection | Checked | Memory is off. Skills injection is on, but no skill exists. A one-line request had 23 prompt tokens, so nothing is added. | Left as it is |
| OmniRoute semantic cache for temperature 0 | Checked | It is on and works only for temperature 0. A repeated request was a hit in 0.06 seconds against 1.24 seconds, and 562 tokens were saved. Temperature 0.7 never hits. | Left as it is |
| `prepare_ahead` for the orchestrator's Slack sessions | Checked | Hermes has no setting per platform. A profile setting adds a summary call to every cron and card session. No Slack session exists yet. | No |
| Brief summary at temperature 0 | Not changed | The brief uses temperature 0.2, so the cache cannot help it. The daily input changes each day, so a hit is rare. | No |

## 3. Tools that were tried

| Tool | Result | Rating |
|---|---|---|
| RTK 1.2.0 (installed in Prompt 1) | `rtk git diff` cut a 565 KB patch to 51 KB (91 percent). `rtk tree` 95, `rtk git status` 86, `rtk find` 75, `rtk ls` 67, `rtk git log` 47. It changed nothing for `grep`, `cat`, and a passing `pytest` run. | Adopt |
| TOON | A table of 40 cards: 44 percent fewer characters than compact JSON and 57 percent fewer than pretty JSON. The estimate uses a hand-written encoder. The big tables in this fleet come from Hermes tools that the fleet cannot change. | Watch |
| caveman | Not tried. Kanban comments are already short by rule. | Watch |
| serena | Not tried. The product code is small, and a language server adds memory and a large install. | Watch |

The full list with the other ratings is in `docs/UPGRADES.md`.

## 4. After

### 4.1 The fixed size of a request

Measured with the same command after deploy cycle I5. The system prompt is smaller by about 26 bytes, because the SOUL files lost one line each.

| Agent | Bytes before | Bytes after | Saved | Tools before | Tools after |
|---|---|---|---|---|---|
| orchestrator | 62,506 | 62,323 | 183 | 26 | 26 |
| research | 36,120 | 35,937 | 183 | 14 | 14 |
| build | 33,444 | 33,264 | 180 | 12 | 12 |
| marketing | 36,234 | 36,054 | 180 | 14 | 14 |
| feedback | 23,244 | 20,887 | 2,357 | 7 | 4 |
| security | 34,065 | 33,885 | 180 | 12 | 12 |
| improver | 32,508 | 32,328 | 180 | 12 | 12 |
| all seven | 258,121 | 254,678 | 3,443 (1.3 percent) | | |

The target of 12 tools or fewer is met by build, security, improver, and feedback. It is not met by research and marketing (14 tools), and not by the orchestrator (26 tools). Each remaining tool is used or required, and the Kanban toolset is one unit. The skills toolset (3 tools, 4.7 KB) made no call in 3 days. A later build can preload the skills that matter and drop the toolset.

### 4.2 A run of the fleet

The fleet was resumed on 2026-11-15 at 11:07 America/Chicago, after the token settings and the router fix. The orchestrator got one wake-up card at 11:08. The card finished and made three new cards for project 001: a validation page, a waitlist offer, and a launch message that stays in the dry-run outbox. The marketing agent started the first one.

The session of the finished card:

| Number | Value |
|---|---|
| API calls | 20 |
| New input tokens | 475,394 |
| Tokens read from a cache | 114,688 |
| Output tokens | 31,434 |
| Messages | 107, of which 71 are tool results |
| Characters in all tool results | 79,599 (average 1,121) |

The router log for the first 38 minutes after the resume:

| Number | Value |
|---|---|
| Successful requests | 14, all on `ling-3.2-flash:free` |
| Requests refused with 429 | 53 |
| Prompt tokens of the successful requests | 396,643 (average 28,331 for each request) |
| Average prompt before the changes | 29,800 for each request (16,063,745 tokens over 539 requests) |

What this shows:

1. The tool results of this card were small, 1,121 characters on average, and none was large. So the prune and the output cap had nothing to cut. They act on the large results of long research sessions, like the six that section 1.4 describes. A fair test needs a finished research card.
2. The average prompt is 5 percent smaller than before, from 29,800 to 28,331 tokens. The early part of a session has a short history, so this is not a test of the prune.
3. The free OpenRouter pool is now the limit. It served 14 requests in 38 minutes and refused 53 with 429. A request that fails costs no tokens, but it costs time. Prompt 3 took the NVIDIA and Cohere trial keys out of the fleet combos.
4. Before the router fix, a rate limit made the router try the Groq targets, which answered 413. Hermes read that as a conversation that is too large, and the first orchestrator run failed. See D-064. After the fix the same limit is a short wait, and the card finished.

For a card-level comparison, Prompt 4 should measure one finished research card against the 2.7 million tokens of section 1.1. A Gemini key adds the capacity that makes that quick.

## 5. The builder: Claude Code

The fleet is not the biggest user of tokens. Claude Code, which builds the fleet, is. The totals come from the usage fields of the four project transcripts, one reply counted once.

| Session | API calls | Output tokens | Cache write | Cache read |
|---|---|---|---|---|
| Prompt 1, part 1 | 376 | 765,240 | 1,233,253 | 170,744,489 |
| Prompt 1, part 2 | 449 | 653,926 | 1,552,478 | 201,078,700 |
| Prompt 2 | 134 | 70,542 | 382,056 | 19,473,492 |
| Prompt 3 (this build) | 1,050 | 1,605,178 | 4,177,583 | 502,096,345 |
| All four | 2,009 | 3,094,886 | 7,345,370 | 893,393,026 |

A long context is read again at each call. That is why a cache read of 500 million tokens comes from 1,050 calls. The owner's own use of ccusage is a decision in `docs/BLOCKED.md`.

## 6. What to do next

1. Read the numbers of section 4.2 again after a day of work, and compare them with section 1.1.
2. Add the `markitdown` reader branch, so the research agent can read a PDF.
3. If the skills toolset stays unused, preload the needed skills into the SOUL files and drop it.
4. Decide on `prepare_ahead` after the first Slack sessions.
