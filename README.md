# KB Lifecycle Agent — PS-03

**atomcamp National AI Hackathon Finale 2026 · Team submission**

An autonomous, multi-agent AI system that manages the full lifecycle of an organization's knowledge base — from ingestion through audit, remediation, gap detection, and Q&A.

---

## What it does

Knowledge bases decay: articles go stale, duplicate each other, contradict one another, and leave gaps. This system continuously maintains the KB instead of just answering questions about it.

| Stage | Agent | What it does |
|---|---|---|
| Ingest | **Ingestion Agent** | Parses CSV tickets, builds rich text docs, embeds into ChromaDB |
| Audit | **Audit Agent** | Finds duplicates (cosine similarity), stale content (SLA+time), low quality, contradictions (LLM judge) |
| Remediate | **Remediation Agent** | Drafts merge/rewrite/consolidation proposals via GPT-4o |
| Verify | **Verifier Agent** | Self-correction loop — scores proposals against ground-truth, auto-rejects weak ones |
| Gap | **Gap Agent** | Finds topic/product combos with high failure rates → suggests missing articles |
| Q&A | **Q&A Agent** | RAG: retrieves relevant articles, returns cited, confidence-scored answers |
| Plan | **Orchestrator** | Builds a plan, dispatches agents, retries, logs every action |

---

## Architecture

```
User / Demo UI (HTML+JS)
        │
        ▼
FastAPI API (api/main.py)
        │
        ▼
Orchestrator Agent
   ├── Ingestion Agent ──► ChromaDB (vector store)
   ├── Audit Agent     ──► ChromaDB + GPT-4o (contradiction check)
   ├── Remediation     ──► GPT-4o + Review Queue
   ├── Verifier        ──► GPT-4o (self-correction)
   ├── Gap Agent       ──► GPT-4o + ChromaDB
   └── Q&A Agent       ──► ChromaDB + GPT-4o (RAG)
        │
        ▼
Audit Log (JSON / Firestore in production)
```

---

## How to run locally

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Set up environment
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# 3. Run the API
uvicorn api.main:app --reload --port 8080

# 4. Open the UI
# Navigate to http://localhost:8080
# Click "Run Full Sweep" to start the lifecycle pipeline
```

---

## GCP Cloud Run deploy

```bash
gcloud run deploy kb-lifecycle-agent \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars "OPENAI_API_KEY=sk-..." \
  --memory 2Gi
```

---

## Dataset

`data/ps03_support_tickets.csv` — 300 support tickets across 8 categories, 5 products.
- 195 repetitive tickets (65%) → duplicate/gap signal
- 149 SLA failures → stale content signal

---

## Project structure

```
agents/          # One module per agent
  orchestrator.py
  ingestion_agent.py
  audit_agent.py
  remediation_agent.py
  verifier_agent.py
  gap_agent.py
  qa_agent.py
tools/           # Reusable tool functions
  vector_store.py   (ChromaDB wrapper)
  audit_log.py      (append-only action log)
  review_queue.py   (human-in-the-loop queue)
api/main.py      # FastAPI app + all routes
ui/              # Demo frontend (HTML/CSS/JS)
data/            # Sample dataset (read-only)
config.py        # Settings from env vars
models.py        # Shared Pydantic models
llm_client.py    # LLM wrapper with retry/backoff
Dockerfile       # Cloud Run ready
```
