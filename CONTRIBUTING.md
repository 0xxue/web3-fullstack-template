# Contributing

## Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # Edit with your config
alembic upgrade head
uvicorn app.main:app --reload
```

## Testing

```bash
cd backend
python -m pytest tests/ -v
```

## Code Style

- Python: ruff (auto-format)
- Commits: Conventional Commits (feat/fix/docs/test/chore)

## Pull Requests

1. Fork and create a branch from `master`
2. Write tests for new features
3. Run tests before submitting
4. Submit PR with clear description
