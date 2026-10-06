# ARDA 2-Minute Demo Script

Goal: a recruiter watches this and understands what ARDA is in 120 seconds. Record with QuickTime screen recording (or asciinema for terminal-only). One take is fine. Done beats polished.

Every command below was traced against the code on 2026-09-10. The API listens on **port 5000**, not 8000, and there is no `/chat` route.

The web-page shot was added on 2026-10-05 and traced against `api/demo.py`.

Re-run end to end on 2026-09-26 against a fresh worktree (mock path). Two fresh-clone blockers turned up and are fixed below: compose refuses to start without a `.env`, and on macOS AirPlay Receiver owns port 5000.

## Before you record

```bash
cp .env.example .env
sed -i '' "s/^ARDA_API_KEY=.*/ARDA_API_KEY=$(openssl rand -hex 32)/" .env
export ARDA_API_KEY=$(grep ^ARDA_API_KEY= .env | cut -d= -f2)
```

`docker-compose.yml` has `env_file: .env`, so without that `cp` the very first command fails. Commands below also need `jq`.

**Port 5000 on a Mac.** AirPlay Receiver (ControlCenter) listens on 5000, and `docker compose up` fails with `bind: address already in use`. Either turn it off (System Settings > General > AirDrop & Handoff > AirPlay Receiver) for the recording, or run `ARDA_PORT=5050 docker compose up -d` and swap `5000` for `5050` below. Turning it off keeps the commands identical to the README, so that is the better take.

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

**0:30 to 0:55, the executor path (real output on screen)**
One request that fans out into a multi-command workflow and blocks for results:
```bash
curl -s -X POST localhost:5000/execute/wait \
  -H "x-api-key: $ARDA_API_KEY" \
  -H "content-type: application/json" \
  -d '{"message": "system status"}' | jq
```
Shows: one message becomes three queued commands (`uptime`, `df -h`, `free -m`), each picked up by the worker over Redis, with real stdout in the response.

Caption this as the **regex planner** path, not the LangGraph one. `/execute/wait` classifies with `agents/sauron/planner.py` and short-circuits to the queue at `api/routes/tasks.py:171` without ever calling Sauron's graph. It is the fast path, and it is honest to show it as such. The LangGraph shot is next.

**0:55 to 1:20, the LangGraph loop and memory across turns**
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

**1:20 to 1:45, the safe public demo page (web)**
Start demo mode on 5050 (clear of both AirPlay and the compose stack). It needs no Redis and no key (mock mode), and it never shells out:
```bash
DEMO_MODE=true ARDA_API_KEY=$(openssl rand -hex 32) uv run uvicorn api.main:app --port 5050
```
Open `http://localhost:5050`. Click the first example chip ("What is Finrod's default vector store?"): the answer appears with a trace panel showing the specialist (`finrod`), the tool call and its latency, the documents it retrieved from, and token counts. Then click the third chip, the prompt injection that asks the executor to `cat /etc/passwd`: the trace shows `earendil_execute` with status `refused` and the answer says shell execution is disabled.

Shows: the same routing as the API path, with every step visible, and a refusal instead of a command. Without a key the badge reads `mock: keyword router, not an LLM`; say that on camera. For the live badge, add `USE_MOCK_LLM=false ANTHROPIC_API_KEY=...` to that command (and `USE_MOCK_EMBEDDER=true` to skip torch). The deployed version lives at the URL in the README once `docs/deploy-demo.md` is done.

To prove the refusal on the command line: `curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:5050/execute -d '{}'` prints `403`.

**1:45 to 1:55, the test story**
```bash
uv sync --extra dev
uv run pytest tests/ -q
```
Shows: the full suite passes with zero API keys and no network. This is the line that lands with engineers.

**1:55 to 2:00, close**
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
