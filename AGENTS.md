# Repository Guidelines

## Project Structure & Module Organization

AgentForge is an early-stage Python 3.12 service. Application code lives in `development_agents/`. `main.py` is the container entry point, `agents/` contains role-specific agents, and `core/` separates models, orchestration, and infrastructure. PostgreSQL repositories are under `core/infrastructure/repositories/`; schema initialization belongs in `development_agents/scripts/`, and tests in `development_agents/tests/`. Runtime workspaces mount at `/workspaces`; do not commit generated data, logs, caches, or secrets.

## Build, Test, and Development Commands

- `cp .env.example .env` creates local configuration. Replace example passwords before deployment.
- `docker compose build` builds the Python 3.12 application image and installs pinned dependencies.
- `docker compose up --build` starts AgentForge, PostgreSQL, and RabbitMQ with health-check ordering.
- `docker compose logs -f agent-forge` follows application startup and connectivity output.
- `docker compose down` stops the stack while retaining named database and queue volumes.
- `python -m compileall development_agents` performs a quick syntax check without starting services.

Run Python commands from `development_agents/` when relying on imports such as `from core...`, or use the Docker Compose service whose working directory is already `/app`.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation and type-annotated public signatures. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes and dataclasses; and `UPPER_SNAKE_CASE` for constants. Keep domain models free of infrastructure concerns, and place database or broker access behind `core/infrastructure/`. No formatter or linter is configured, so preserve existing import grouping and line wrapping.

## Testing Guidelines

The test directory is currently empty and no test framework or coverage threshold is configured. New behavior should include focused tests named `test_*.py` under `development_agents/tests/`. Prefer unit tests for models and orchestration, with PostgreSQL/RabbitMQ connectivity covered separately as integration tests. If introducing `pytest` or coverage tooling, pin it in `requirements.txt` and document the command in the README.

## Commit & Pull Request Guidelines

Recent history mostly uses short, imperative Conventional Commit prefixes such as `chore:`. Continue with `feat:`, `fix:`, `test:`, `docs:`, or `chore:` and keep each commit scoped. Pull requests should explain the motivation and behavior change, list verification commands, link relevant issues, and call out schema, environment-variable, or service-topology changes. Include screenshots or logs only when they clarify user-visible or operational behavior.

## Security & Configuration

Never commit `.env`, credentials, logs, or database dumps. Add new settings to `.env.example` with safe placeholders, and access configuration through environment variables.
