# Architecture Notes — PS-03 KB Lifecycle Agent

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Demo UI (browser)                        │
│  Overview | Findings | Review Queue | Ask KB | Audit Log        │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP REST
┌───────────────────────────▼─────────────────────────────────────┐
│                     FastAPI (api/main.py)                        │
│  POST /api/orchestrate  GET /api/status/{id}  POST /api/qa      │
│  GET /api/audit-log     GET /api/review-queue                   │
│  POST /api/review/{id}/approve  POST /api/review/{id}/reject    │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                    Orchestrator Agent                            │
│  - Reads goal: "run lifecycle sweep"                            │
│  - Builds 5-step plan                                           │
│  - Dispatches each agent sequentially                           │
│  - Retries once on failure, continues on error                  │
│  - Logs every step to audit log                                 │
└──┬──────────┬──────────┬──────────┬──────────┬─────────────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
Ingestion   Audit    Remediation Verifier   Gap Agent
Agent       Agent    Agent       Agent
   │          │          │          │          │
   │          │          │          │          │
   ▼          ▼          │          │          ▼
ChromaDB   ChromaDB   Review     ChromaDB   GPT-4o
(upsert)   (search)   Queue      (search)   (LLM)
           GPT-4o     GPT-4o     GPT-4o
           (contra-   (draft)    (score)
            dictions)
   │          │          │          │          │
   └──────────┴──────────┴──────────┴──────────┘
                         │
                   Audit Log
               (every action recorded)
```

## Agent Details

### Orchestrator
- Reads: high-level goal
- Builds: ordered plan of steps
- Delegates: calls each agent's `run()` function
- Guards: `MAX_STEP_RETRIES = 1`, continues on failure
- Logs: plan creation + each step start/end

### Ingestion Agent
- Reads: CSV file (300 tickets)
- Cleans: validates via Pydantic
- Embeds: ChromaDB's default ONNX embedder (all-MiniLM-L6-v2 ONNX)
- Stores: upserts to ChromaDB (idempotent)
- Logs: articles_parsed, errors, total_in_store

### Audit Agent
**Rule-based:**
- Duplicates: cosine distance < 0.12 via ChromaDB similarity search
- Stale: sla_met=False AND resolution_time > 96h
- Low quality: resolution notes < 6 words

**LLM-based (GPT-4o):**
- Contradictions: grouped by category+product, LLM decides if two articles contradict
- Returns: `{"contradicts": bool, "reason": str, "confidence": float}`
- Guarded: max 5 groups checked (cost control)

### Remediation Agent
- Input: list of AuditFindings
- For each finding type:
  - duplicate → LLM merge prompt → merged article
  - stale/low_quality → LLM rewrite prompt → improved article
  - contradiction → LLM consolidation prompt → definitive article
- Output: RemediationProposals enqueued for review
- Guarded: `max_proposals = 6` (hackathon cost control)

### Verifier Agent (self-correction)
- Input: all pending proposals
- For each proposal:
  1. Similarity search for ground-truth context (ChromaDB)
  2. LLM scores proposal on: consistency, clarity, no hallucinations
  3. Returns: `{"score": float, "notes": str, "issues": list}`
  4. score < 0.6 → auto-reject (logged as "self-correction")
  5. score ≥ 0.6 → passes to human review queue

### Gap Agent
- Groups articles by (category, product)
- Scores gap severity: repetitive_count + sla_fail_count
- Top-N worst groups → LLM suggests missing article titles
- Returns: gap findings as AuditFindings

### Q&A Agent (RAG)
1. Similarity search (top-5 articles)
2. LLM synthesises answer with article citations
3. Returns: `{"answer": str, "citations": list, "confidence": float, "caveat": str|null}`

## Data Flow

```
CSV → Ingestion → ChromaDB
              ↓
          Audit Agent
         /    |    \
     Dupes  Stale  Contradictions
              ↓
         Remediation → Review Queue
              ↓
           Verifier (self-correct)
              ↓
         Human Review (approve/reject)
              ↓
        [Future: apply to ChromaDB]
```

## Key Design Decisions

1. **Sequential orchestration** — easier to explain to judges, cleaner audit log
2. **ChromaDB embedded** — no external service needed for demo; ONNX embedder is pure Python
3. **JSON file persistence** — audit log and review queue survive server restarts without Firestore setup
4. **Cost guards everywhere** — max_proposals, max_groups, max_iterations prevent runaway LLM spend
5. **Pydantic throughout** — all LLM outputs validated against schemas; bad JSON raises ValueError
6. **Retry via tenacity** — all LLM calls retry up to 3x with exponential backoff
