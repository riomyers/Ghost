# Usage-Aware Brain PRD

## Problem Statement
**Current State:** Ghost tracks API/token usage in `token_budget` but never evaluates it against any threshold. The log sensor (`sensors/logs.py`) uses broad regex (`error|ERROR`, `timeout|Timeout`) that matches Ghost's own operational log lines. When 3+ false matches accumulate, Gemma analyzes them and hallucinates problems like "high API usage." These hallucinated observations feed into the kernel's think cycle, where Gemma picks `send_notification` — the only tool remotely relevant. Rio gets pinged about non-existent problems. Ghost has no ability to actually throttle, adjust, or self-regulate.

**Users:** Rio (sole operator, receives all notifications)

**Pain Points:**
1. False positive notifications from hallucinated API usage warnings
2. No actual usage thresholds — tracking without enforcement is just noise
3. No self-regulation — Ghost can't reduce its own cycle frequency, skip low-priority work, or extend intervals
4. Log sensor matches its own operational output, creating a feedback loop

**Why Now:** Ghost runs 24/7 on local Ollama. Without self-regulation, it burns cycles pointlessly and cries wolf until notifications get muted entirely — defeating the purpose of monitoring.

## Objective & Scope
**Objective:** Make Ghost usage-aware — it should know when it's burning too many cycles, automatically throttle itself, and only alert on real problems.

**Success Criteria:**
- Zero false "high API usage" notifications
- Ghost auto-throttles when daily Ollama calls exceed configurable limit
- Log sensor stops matching its own operational output
- Self-diagnosis includes actual usage budget checks with real thresholds

### In Scope
- T1: Fix log sensor false positives (severity-aware filtering, self-log exclusion)
- T2: Add usage budget thresholds to kernel with auto-throttle behavior
- T3: Add `adjust_cycle` tool so Ghost can self-regulate think interval
- T4: Wire usage budget into self-diagnosis with real thresholds

### Out of Scope
- Dashboard UI changes for usage display (already works)
- Nexus/cloud usage limits (already minimal post-migration)
- Historical usage analytics or trending

## Requirements
### Critical User Journeys
1. **Normal operation:** Ghost runs think cycles → usage stays under threshold → no throttling → no notifications
2. **High usage day:** Ollama calls exceed daily limit → Ghost extends think interval → logs the throttle → resumes normal next day
3. **Real problem:** Service down → log sensor detects genuine error → kernel acts → Rio gets notified

### Functional Requirements
| Priority | Requirement | Acceptance Criteria |
|----------|-------------|---------------------|
| P0 | Log sensor excludes Ghost's own operational log lines | No false positives from lines containing `ollama_calls_today`, `Think cycle`, `DIAG:`, `Cycle N:` |
| P0 | Log sensor uses structured severity not raw regex for Ghost logs | Only matches genuine errors, not informational lines containing the word "error" |
| P0 | Kernel checks daily usage against configurable threshold | `OLLAMA_DAILY_LIMIT` env var (default 200), checked in `run_planner()` |
| P1 | Auto-throttle: when usage > limit, extend THINK_INTERVAL to 30 | Think cycle frequency drops, logged clearly |
| P1 | Auto-throttle resets at midnight local time | `get_daily_token_usage()` already resets at local midnight |
| P1 | Self-diagnosis reports actual usage vs budget | "Usage: 150/200 (75%)" not hallucinated warnings |
| P2 | `adjust_cycle` tool available to kernel | Ghost can extend/reduce think interval within bounds (5-60 min) |

### Technical Constraints
- All changes in `agent/` directory (Python, runs on Hetzner VPS)
- Ollama is local — no rate limits, but CPU/thermal impact from excessive calls
- `token_budget` table already tracks per-call usage — no schema changes needed
- Must not break existing notification suppression or confidence gating

### Existing Code Context
- `agent/sensors/logs.py` — log anomaly sensor, lines 24-30 have the broad patterns
- `agent/kernel.py` — main loop, `run_planner()` (line 218), `self_diagnose()` (line 425), `THINK_INTERVAL=10` (line 20)
- `agent/database.py` — `get_daily_token_usage()` (line 306), `record_token_usage()` (line 288)
- `agent/kernel.py:188` — system prompt already tries to prevent hallucination (insufficient)

## Risks & Mitigations
| Risk | Impact | Mitigation |
|------|--------|------------|
| Throttle too aggressive → misses real issues | High | Only throttle think cycles, not sensors or task execution. Critical observations still trigger urgent flow. |
| Legitimate errors contain "error" word | Medium | Use context-aware pattern matching: require error at line start or after timestamp bracket, not mid-sentence |
| Default limit too low/high | Low | Env var `OLLAMA_DAILY_LIMIT`, easy to tune. Default 200 based on ~1 call/min for ~3.3 hrs of active thinking |
