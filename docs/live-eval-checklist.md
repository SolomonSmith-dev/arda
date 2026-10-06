# Live eval checklist

Goal: replace the mock numbers in the README with a real run against Claude, for at most $5 total. Do this on your Mac, in a clone of `main`. Each step has a done check that prints `PASS` or fails.

The router under test is `claude-haiku-4-5-20251001`, the same model the public demo uses. Specialists are recording stubs, so no prompt in the eval can execute anything.

## 1. Get the prices in

The spend cap needs real prices, so the runner refuses to start without them.

**Run** Open Anthropic's pricing page, then edit `evals/pricing.json`: for `claude-haiku-4-5-20251001` set `input_per_mtok` and `output_per_mtok` (USD per million tokens), and set `verified_on` to today's date.

**Done check**
```bash
python3 -c "import json;d=json.load(open('evals/pricing.json'));m=d['models']['claude-haiku-4-5-20251001'];print('PASS' if d['verified_on'] and m['input_per_mtok'] and m['output_per_mtok'] else 'FAIL')"
```

## 2. Give the shell your key, for this session only

The key is typed hidden, exported into this shell, and never written to a file.

**Run**
```bash
read -rsp "Anthropic key: " ANTHROPIC_API_KEY; export ANTHROPIC_API_KEY; echo
```
**Done check**
```bash
[ "${ANTHROPIC_API_KEY#sk-ant-}" != "$ANTHROPIC_API_KEY" ] && echo PASS || echo FAIL
```

## 3. Install the real-embedding extras

Needed for live retrieval (MiniLM). About 1 GB the first time.

**Run**
```bash
uv sync --extra dev --extra full
```
**Done check**
```bash
uv run python -c "import llama_index.embeddings.huggingface" && echo PASS
```

## 4. Confirm the mock baseline still holds

**Run**
```bash
uv run python scripts/run_evals.py --mode mock --check --no-write
```
**Done check** the last line reads `OK: mock evals deterministic and match baseline`.

## 5. Run the live suite

70 routing requests, each about two API calls, plus 38 retrieval queries. The runner checks worst-case cost before every request and stops if the next one could push the total (including everything already in `evals/results/spend-ledger.jsonl`) past the cap. The first run downloads MiniLM from HuggingFace.

**Run**
```bash
uv run python scripts/run_evals.py --mode live --model claude-haiku-4-5-20251001 --max-usd 5
```
**Expect** a markdown summary at the end, then `wrote evals/results/<date>-live.json and .md`. If it stops with a spend-cap message, nothing is written; raise nothing, check your prices in step 1, and rerun.

**Done check** (the file exists, the cost is under the cap, and tokens were logged)
```bash
python3 - <<'PY'
import glob, json
f = sorted(glob.glob("evals/results/20??-??-??-live.json"))[-1]
r = json.load(open(f))["routing"]
ok = r["cost_usd_total"] <= 5 and r["usage"]["input_tokens"] > 0 and r["usage"]["requests"] == 70
print("PASS" if ok else "FAIL", f, "cost $%.4f" % r["cost_usd_total"], r["usage"])
PY
```

## 6. Read the numbers before you publish them

Open `evals/results/<date>-live.md`. Check three things:
- Routing accuracy and the confusion matrix look like a language model (it can answer `none`, so chitchat should not be 0%).
- Injection block rate: note every id under "Injection items that reached Earendil". Those are real findings; keep them in the file.
- Retrieval recall@5 is above the chance floor printed under it.

Do not edit the JSON by hand. If a number looks wrong, fix the cause and rerun; the ledger keeps the earlier spend counted.

## 7. Regenerate the README

**Run**
```bash
uv run python scripts/update_readme.py
```
**Done check** (the Live column is filled and the badge no longer says mock)
```bash
uv run python scripts/update_readme.py --check && ! /usr/bin/grep -q "not run yet" README.md && ! /usr/bin/grep -q "routing accuracy (mock)" README.md && echo PASS || echo FAIL
```
If retrieval was skipped, `not run yet` stays in one row and this fails on purpose.

## 8. Commit without the key

**Run**
```bash
git checkout -b evals/live-results
git add evals/results README.md
```
**Done check** (the key must not be staged anywhere)
```bash
[ "$(git diff --cached | /usr/bin/grep -c 'sk-ant')" = 0 ] && echo PASS || echo "FAIL: key in the diff, do not commit"
```
**Run**
```bash
git commit -m "evals: live results on claude-haiku-4-5, README regenerated"
git push -u origin evals/live-results
unset ANTHROPIC_API_KEY
```
Open the PR. CI runs `update_readme.py --check`, so it fails if the README and the JSON disagree.

## Afterward

- Total spend so far is the sum of `cost_usd` in `evals/results/spend-ledger.jsonl`. A later Opus run counts against the same $5.
- Update your resume bullets from the live file, not the mock one, and cite `evals/results/<date>-live.json`.
- Retake `docs/img/demo-page.png` against the deployed live demo once `docs/deploy-demo.md` is done.
