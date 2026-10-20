# GEMINI.md: Project Context & Rules for Antigravity

## Project
**PS-03: AI-Powered Knowledge Base Lifecycle Agent** (atomcamp National AI Hackathon Finale, Oct 20, 2026, NIC Islamabad)

Build an autonomous, multi-agent system that manages the full lifecycle of an organization's knowledge base: ingesting content, keeping it accurate, and retiring what is outdated. It must work end to end on the provided sample dataset.

## Hackathon rules (NON-NEGOTIABLE)
- All code is written **today, after the problem reveal**. No pre-existing templates, no copied projects.
- It must be **Agentic AI**: agents that plan, use tools, and take actions. A chatbot or single prompt-response app does NOT qualify.
- Deploy on **Google Cloud Run** (GCP). Develop in Antigravity.
- The demo must be **live and real**. Never hardcode outputs, fake results, or show features that don't exist.
- Commit early and often with meaningful messages (judges may check history).
- Every part of the code must be explainable by both team members. Prefer simple, readable code over clever code. Add short comments on the "why" for agent logic.
- **Never commit secrets.** API keys go in environment variables (`.env` is git-ignored). Never print keys in logs.
- Watch credit usage. Avoid unbounded loops and unnecessary LLM calls (cap agent iterations, cache where sensible).

## Problem we are solving
Knowledge bases decay. Articles go stale, contradict each other, duplicate one another, and leave gaps, and nobody notices until users are misled. Our agent system continuously maintains the KB instead of just answering questions about it.

**Lifecycle stages the system covers:**
1. **Ingest:** load documents, clean, chunk, extract metadata (topic, owner, date, source).
2. **Classify and tag:** assign category and topics, and link related articles.
3. **Audit:** detect stale content, duplicates, contradictions between articles, and quality problems (vague, incomplete, broken references).
4. **Gap detection:** find questions or topics the KB cannot answer.
5. **Remediate:** propose or draft updates, merges, or new articles. Archive or retire obsolete ones.
6. **Review and approve:** human-in-the-loop approval for risky changes, with an audit log of every action.
7. **Serve:** cited, confidence-scored answers from the maintained KB.

> Adjust this list to match the sample dataset and the exact PS-03 text once the data is inspected. Do not assume the dataset's contents. Inspect it first.

## Agent architecture (multi-agent)
Orchestrator-driven. Agents plan, call tools, check their own output, and hand off work.

| Agent | Responsibility | Example tools |
|---|---|---|
| **Orchestrator / Planner** | Reads the goal, builds a plan, delegates, retries or re-plans on failure | agent dispatch, task state |
| **Ingestion Agent** | Parse, clean, chunk, embed, and store documents | file parser, embedder, store writer |
| **Audit Agent** | Find stale, duplicate, conflicting, or low-quality content | similarity search, date checker, LLM judge |
| **Gap Agent** | Identify missing topics and unanswered queries | query log, retrieval-miss analysis |
| **Remediation Agent** | Draft fixes: rewrite, merge, create, retire | doc editor, diff generator |
| **Verifier (self-correction)** | Checks proposed changes against sources, rejects or revises weak ones | retrieval, fact check |
| **Q&A Agent** | Answers questions with citations and confidence | retrieval, citation builder |

Required behaviors to demonstrate: **autonomous decisions, tool use, self-correction (verifier loop), multi-step planning.**

## Tech stack (defaults, change only with reason)
- **Language:** Python 3.11+
- **Agent framework:** Google ADK (allowed). LangGraph is the fallback.
- **API layer:** FastAPI, simple web UI (lightweight) for the live demo
- **LLM:** configurable via env var (`LLM_PROVIDER`, `LLM_MODEL`). Use the hackathon-provided OpenAI key and/or Gemini via Vertex AI. Keep the model swappable.
- **Storage:** Firestore (metadata, audit log, review queue) plus a simple vector index (embedded or Vertex AI Vector Search if time allows)
- **Deployment:** Cloud Run (`gcloud run deploy --source .`), Cloud Build, Artifact Registry. Secrets via env vars or Secret Manager.
- **Dependencies:** pin versions in `requirements.txt`.

## Suggested repo layout
```
/agents        # one module per agent
/tools         # tool functions agents can call (parse, search, diff, etc.)
/api           # FastAPI app and routes
/ui            # minimal demo front end
/data          # sample dataset (read-only, do not alter originals)
/docs          # architecture diagram, notes
README.md      # short: what it does, how to run, architecture, GCP deploy
Dockerfile
.env.example   # variable names only, no real values
.gitignore     # includes .env
```

## Coding standards
- Production-quality but pragmatic: type hints, small functions, clear names, error handling around every tool and LLM call, structured logging.
- Every agent action is written to an **audit log** (what, why, which sources, outcome). This is also the demo's proof of autonomy.
- Use structured (JSON/Pydantic) outputs from LLMs and validate them.
- Add a simple retry/backoff and a max-iteration guard to every agent loop.
- No dead code, no unexplained copy-pasted blocks.

## Build order (time-boxed)
1. Inspect the sample dataset. Confirm the problem scope.
2. Skeleton FastAPI app plus Dockerfile, **deploy to Cloud Run immediately**, then redeploy continuously.
3. Ingestion plus storage working on the sample data.
4. Audit Agent (stale, duplicate, conflict detection), the core of the demo.
5. Orchestrator with planning plus Verifier self-correction loop.
6. Remediation and review queue. Then Gap Agent and Q&A Agent if time allows.
7. Polish the demo flow, README, architecture diagram, slides. Record a backup screen capture.

## Demo plan (3-minute live segment)
Show: upload or point at the sample KB, then the agent plans and runs a lifecycle sweep, then it flags stale, duplicate, and conflicting articles, then the verifier catches or corrects a weak proposal, then a human approves a change, then the KB updates and a question gets answered from the fixed content. Show the audit log.

## Submission checklist
- [ ] Live Cloud Run URL
- [ ] GitHub repo (public or shared) with README
- [ ] Slides (PDF or link)
- [ ] Architecture diagram (agents and interactions)
- [ ] Team name and all member names

## Scoring focus (PS-03)
**Agentic behavior** (autonomy, tool use, self-correction, planning), **Technical depth** (architecture, GCP usage, code quality), **MVP completeness** (end to end on the sample data), **Scalability** (production path), **Innovation**.

## Open items to fill in
- Sample dataset format and size: _TBD after inspection_
- Exact definition of "lifecycle" stages expected in PS-03: _TBD, confirm with the full problem text_
- Final model and provider choice: _TBD_
