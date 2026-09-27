# ARDA 2-Minute Demo Script

Goal: a recruiter watches this and understands what ARDA is in 120 seconds. Record with QuickTime screen recording (or asciinema for terminal-only). One take is fine. Done beats polished.

Every command below was traced against the code on 2026-09-10. The API listens on **port 5000**, not 8000, and there is no `/chat` route.

One precondition before you record: the memory shot at 1:00 needs `USE_MOCK_LLM=false` and a real `ANTHROPIC_API_KEY` in `.env`. `use_mock_llm` defaults to `True` (`core/config.py:34`) and `docker-compose.yml` sets no override, so on a stock clone Sauron answers from the mock client and cannot demonstrate memory. Every other shot runs fine on the mock path.

## Shot list

**0:00 to 0:15, the pitch (terminal, big font)**
Say or caption: "ARDA is a multi-agent system behind one FastAPI entry point. One orchestrator, four specialists, Redis task queue, all Dockerized."

**0:15 to 0:30, boot it**
```bash
docker compose up -d
curl -s localhost:5000/health | jq
```
Shows: one command brings up the whole stack.

**0:30 to 1:00, the executor path (real output on screen)**
One request that fans out into a multi-command workflow and blocks for results:
```bash
curl -s -X POST localhost:5000/execute/wait \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{"message": "system status"}' | jq
```
Shows: one message becomes three queued commands (`uptime`, `df -h`, `free -m`), each picked up by the worker over Redis, with real stdout in the response.

Caption this as the **regex planner** path, not the LangGraph one. `/execute/wait` classifies with `agents/sauron/planner.py` and short-circuits to the queue at `api/routes/tasks.py:171` without ever calling Sauron's graph. It is the fast path, and it is honest to show it as such. The LangGraph shot is next.

**1:00 to 1:30, the LangGraph loop and memory across turns**
`POST /agents/sauron/run` is the route that actually enters the graph, and it is the only one that accepts a `thread_id` (`agents/sauron/agent.py:82`). `/execute/wait` mints a fresh `uuid4` per call, so it could never have shown memory.
```bash
curl -s -X POST localhost:5000/agents/sauron/run \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{"type":"orchestrate","payload":{"message":"check the uptime on this box","thread_id":"demo"}}' | jq

curl -s -X POST localhost:5000/agents/sauron/run \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{"type":"orchestrate","payload":{"message":"what did I just ask you?","thread_id":"demo"}}' | jq
```
Shows: Claude emits a native `tool_use` block for `earendil_execute`, `tool_dispatch` calls the specialist, and the second turn recalls the first from the checkpointer.

Expect the first response to report the task as **queued**, not to print the command output. `dispatch_tool` forwards only the tool input and never sets `wait` (`agents/sauron/tools.py:116`), so Earendil returns `task_ids` rather than stdout. That is why the 0:30 shot exists: it carries the output, this one carries the orchestration and the memory.

**1:30 to 1:50, the test story**
```bash
uv sync --extra dev
uv run pytest tests/ -q
```
Shows: the full suite passes with zero API keys and no network. This is the line that lands with engineers.

**1:50 to 2:00, close**
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
