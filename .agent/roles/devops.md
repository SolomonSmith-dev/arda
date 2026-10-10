# Role: devops

- **Purpose:** Dev setup, build, CI, Docker, deploy-host scripts.
- **Owns (write):** `scripts/`, `.github/`, `tests/scripts/`, `pyproject.toml`, `uv.lock`, `Dockerfile`
- **Reads:** everything
- **Validation:** `scripts/run-checks.sh`
- **Never:** add secrets to CI; push tags or deploy; widen permissions without a decision-log entry; unpin `numpy<2`

