# Local-First Brain PRD

## Problem Statement

**Current State:** Ghost routes ALL AI reasoning through Nexus Gateway (cloud API) — kernel think cycles, AOR reflections, log analysis, digests, research summarization, voice, dashboard chat. Every background think cycle burns a Haiku call. The kernel thinks every 10 minutes (up to 3 goals/cycle), reflections fire on every failed task, log analysis triggers on anomalies. A `DAILY_CALL_LIMIT` of 150 throttles Ghost after ~80% usage, making it effectively "dumber" as the day progresses.

**Users:** Ghost (autonomous agent), Rio (owner/operator)

**Pain Points:**
- **Cost:** ~100-150 Nexus API calls/day for mostly trivial JSON tool selection and simple text analysis
- **Rate limiting:** Ghost self-throttles at 120 calls/day, reducing think frequency from every 10min to every 30min — it gets lazier as it works harder
- **Always-on anxiety:** Ghost can't truly run 24/7 without budget concerns
- **Artificial ceiling:** The daily call limit is an architectural constraint, not a quality one — most calls don't need cloud-grade intelligence

**Why Now:** Ollama with gemma3:12b is already running on the Mac LAN. Ghost has a working Ollama fallback path in brain.py. The infrastructure exists — it's just not being used as the primary brain for background tasks.

## Objective & Scope

**Objective:** Make Ghost local-first — all background autonomous processing runs on local Ollama, cloud API reserved exclusively for user-facing interactions where quality/personality matters.

**Success Criteria:**
- Background Nexus API calls drop to near-zero (only user-initiated interactions)
- Ghost runs 24/7 with no daily call limit or throttling
- Kernel think quality remains equivalent (tool selection accuracy doesn't regress)
- Voice and dashboard direct chat maintain current Sonnet/Haiku quality
- Clean fallback: if Ollama is down, gracefully degrade (don't crash, queue or skip)

### In Scope
1. **Tiered brain routing** — local model for background, cloud for interactive
2. **Kernel migration** — think cycles use Ollama directly
3. **AOR migration** — reflections and SOP generation use Ollama
4. **Sensor migration** — log analysis uses Ollama
5. **Scheduler migration** — EOD digest uses Ollama
6. **Research actuator migration** — summarization uses Ollama
7. **Remove daily call limit** — no longer needed when background is free
8. **Remove throttling logic** — no longer needed
9. **Ollama health monitoring** — ensure local model is responsive

### Out of Scope
- Voice brain changes (already uses Nexus Sonnet for quality — keep as-is for now)
- Dashboard command bar / text reply / explain (user-facing — keep cloud)
- Ollama model selection/fine-tuning optimization
- Migrating off Nexus entirely (Nexus still serves user-facing calls)
- PR reviewer changes (already rare, user-triggered)

## Architecture

### Current Flow
```
ALL paths → nexus_client.chat() → Nexus Gateway → Claude API → $$$
```

### Proposed Flow
```
Background (kernel, AOR, sensors, digest, research):
  → ollama_client.chat() → Local Ollama → gemma3:12b → FREE

User-facing (voice, dashboard chat, dashboard explain):
  → nexus_client.chat() → Nexus Gateway → Sonnet/Haiku → $$$ (but rare)
```

### New Module: `agent/ollama_client.py`

Unified local model client matching `nexus_client.chat()` signature:
```python
def chat(prompt, model=None, system_prompt=None, json_schema=None,
         timeout=60, priority="normal") -> tuple[str, str, str]:
    """Call local Ollama. Returns (result, model, "ollama").

    json_schema: If provided, append schema instruction to system prompt
                 (Ollama doesn't have native JSON schema, but we can instruct)
    """
```

Key behaviors:
- Same return signature as `nexus_client.chat()` for drop-in replacement
- `json_schema` handling via system prompt injection ("respond with ONLY valid JSON matching this schema")
- Configurable model via `GHOST_OLLAMA_MODEL` env var (default: `gemma3:12b`)
- Configurable URL via `OLLAMA_URL` env var
- Timeout handling (Ollama can be slow on first inference)
- Health check method for monitoring

### Migration Pattern (per file)

Replace:
```python
import nexus_client
result, _, _ = nexus_client.chat(prompt, model='haiku', ...)
database.record_token_usage('nexus', 1)
```

With:
```python
import ollama_client
result, _, _ = ollama_client.chat(prompt, ...)
database.record_token_usage('ollama', 1)  # local tracking, no cost
```

## Requirements

### Functional Requirements

| Priority | Requirement | Acceptance Criteria |
|----------|-------------|---------------------|
| P0 | `ollama_client.py` with same interface as `nexus_client.py` | Drop-in replacement, same return types |
| P0 | Kernel think cycles use Ollama | `think_with_nexus` → `think_with_ollama`, tool selection JSON still parses correctly |
| P0 | Graceful Ollama failure handling | If Ollama is down, skip think cycle (don't crash, don't fall back to Nexus for background) |
| P0 | Remove `DAILY_CALL_LIMIT` and throttling from kernel | No more self-throttle, no more `THINK_INTERVAL_THROTTLED` |
| P1 | AOR reflections use Ollama | `reflect_on_action()` and `_check_sop_trigger()` call ollama_client |
| P1 | Log sensor analysis uses Ollama | `sensors/logs.py` Nexus call → Ollama |
| P1 | EOD digest uses Ollama | `scheduler.py:end_of_day_digest()` → Ollama |
| P1 | Research summarization uses Ollama | `actuators/research.py` → Ollama |
| P2 | Token usage tracking distinguishes local vs cloud | `database.record_token_usage('ollama', 1)` vs `('nexus', 1)` |
| P2 | Dashboard stats show local vs cloud call breakdown | Existing dashboard can surface Ollama vs Nexus usage |

### Technical Constraints
- Ollama runs on Mac LAN at `http://192.168.1.6:11434` (configurable via `OLLAMA_URL`)
- Model: `gemma3:12b` (configurable via `GHOST_OLLAMA_MODEL`)
- Ghost server runs on Hetzner VPS — calls Ollama over LAN/tunnel
- Ollama doesn't support JSON Schema natively — must use prompt engineering
- Ollama inference is slower (~2-5s vs ~1s for Haiku) — acceptable for background tasks
- Must handle Ollama cold starts (first inference after idle can take 10-15s)

### Existing Code Context

**Files to modify:**
| File | Change | API Calls Eliminated |
|------|--------|---------------------|
| `agent/ollama_client.py` | NEW — unified Ollama client | N/A |
| `agent/kernel.py` | `think_with_nexus` → `think_with_ollama`, remove daily limit/throttle | ~100-150/day |
| `agent/aor.py` | `nexus_client` → `ollama_client` in reflect + SOP | ~5-20/day |
| `agent/sensors/logs.py` | `nexus_client` → `ollama_client` | ~0-10/day |
| `agent/scheduler.py` | EOD digest → `ollama_client` | 1/day |
| `agent/actuators/research.py` | Summarization → `ollama_client` | ~0-5/day |

**Files that stay on Nexus (user-facing):**
| File | Reason |
|------|--------|
| `voice/lib/brain.py` | Voice quality needs Sonnet |
| `agent/dashboard.py` | Direct user interaction |
| `agent/pr_reviewer.py` | Rare, user-triggered |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Ollama produces worse tool selection JSON | Kernel picks wrong tools, takes bad actions | Validate JSON parsing strictly; existing `no_action` skip logic catches bad outputs; confidence gating still works |
| Ollama is unreachable (Mac offline/asleep) | Ghost can't think at all | Skip think cycle gracefully, log warning. Ghost still runs sensors/tasks. No crash. |
| Ollama is slow (cold start, large prompt) | Think cycles take 10-15s instead of 1-2s | Background tasks — latency doesn't matter. Set generous timeout (60s). |
| gemma3:12b quality insufficient for reflections/SOPs | Lower quality lessons, worse SOPs | Reflections and SOPs are self-improving — bad ones get overwritten. Acceptable quality floor. |
| Network latency VPS → Mac LAN | Timeout on inference | Already have tunnel infrastructure. Increase timeout for Ollama calls. |

## Estimated Impact

**Before:**
- ~100-150 Nexus API calls/day (background)
- ~10-30 Nexus API calls/day (user-facing)
- Daily limit: 150 calls → throttles after ~120
- Ghost gets "dumber" as the day goes on

**After:**
- ~0 Nexus API calls/day (background)
- ~10-30 Nexus API calls/day (user-facing, unchanged)
- No daily limit — Ghost thinks as much as it needs to
- Can increase think frequency if desired (every 5 min instead of 10)
- Truly always-on, always at full capacity

## File Count & Size Estimate

- **1 new file** (`ollama_client.py`, ~60 lines)
- **5 modified files** (kernel, aor, sensors/logs, scheduler, research)
- **~150 lines changed** across modified files (mostly import swaps + removing throttle logic)
- **Size: STANDARD** (5 tickets, straightforward migration)
