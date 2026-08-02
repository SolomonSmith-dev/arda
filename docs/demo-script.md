# ARDA 2-Minute Demo Script

Goal: a recruiter watches this and understands what ARDA is in 120 seconds. Record with QuickTime screen recording (or asciinema for terminal-only). One take is fine. Done beats polished.

## Shot list

**0:00–0:15 — The pitch (terminal, big font)**
Say or caption: "ARDA is a multi-agent system behind one FastAPI entry point. One orchestrator, four specialists, Redis task queue, all Dockerized."

**0:15–0:30 — Boot it**
```bash
docker compose up -d
curl -s localhost:8000/health | jq
```
Shows: one command brings up the whole stack.

**0:30–1:00 — Orchestration in action**
Send one request that forces Sauron to dispatch a specialist:
```bash
curl -s -X POST localhost:8000/chat \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "run `uname -a` and tell me what kernel this is", "thread_id": "demo"}' | jq
```
Shows: LangGraph tool_use loop → Earendil → Redis queue → worker → response.

**1:00–1:30 — Memory across turns**
Second request on the same thread_id referencing the first answer. Shows: checkpointer-backed cross-turn memory.

**1:30–1:50 — The test story**
```bash
USE_MOCK_LLM=true uv run pytest -q
```
Shows: full suite passes with zero API keys. This is the line that lands with engineers.

**1:50–2:00 — Close**
Repo layout on screen, caption: "FastAPI + LangGraph + Redis + Docker. Repo: github.com/SolomonSmith-dev/arda"

## After recording

1. Export as MP4, or convert key moments to a GIF (`ffmpeg -i demo.mp4 -vf "fps=10,scale=900:-1" demo.gif`).
2. Add to README directly under the opening paragraph:

```markdown
## Demo

![ARDA demo](docs/demo.gif)

*One request: Sauron classifies intent, dispatches Earendil through the Redis queue, returns the result. Full 2-min walkthrough: [demo.mp4](docs/demo.mp4)*
```

3. Pin the repo on GitHub.

That's the entire remaining definition of done for ARDA.
