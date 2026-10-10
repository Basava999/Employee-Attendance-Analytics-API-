# Employee Attendance & Analytics API

## Requirements

- **Python** 3.11+
- **MongoDB** 6.0+ (grader uses 7.0). MongoDB Atlas free tier (M0) or Docker (`docker run -p 27017:27017 mongo:7`) work.

## Installation

```bash
python -m venv .venv
# Linux/Mac:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

```bash
cp .env.example .env
# Edit .env with your MongoDB connection:
#   MONGO_URI=mongodb://localhost:27017
#   MONGO_DB=attendance_db
```

Real environment variables always take priority over `.env` values.

## Seed sample data

```bash
python sample_seed.py
```

This loads the nine sample documents from `sample_data/`. The grader's dataset is much larger.

## Start the API

```bash
uvicorn app.main:app --port 8000
```

Indexes are created automatically at startup. Verify with:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Run tests

```bash
# Install test dependencies
pip install pytest httpx

# Unit tests (no MongoDB needed)
pytest tests/test_time_rules.py -v

# Integration tests (requires MongoDB)
pytest tests/ -v

# With coverage
pytest tests/ -v --tb=short
```

## Project structure

```
app/main.py          — All application code (single file, per assignment rules)
tests/               — Test suite
sample_data/         — Sample MongoDB Extended JSON documents
sample_seed.py       — Seeds sample data into MongoDB
REVIEW.md            — Starter code defect review (33 defects found)
DECISIONS.md         — Design decision answers (5 questions)
requirements.txt     — Python dependencies
.env.example         — Environment variable template
.gitignore           — Git ignore rules
```

## Known limitations

- Performance benchmarks have not been measured against a 100k-record dataset (requires a running MongoDB instance with synthetic data).
- The `$densify` stage in the department trend pipeline requires MongoDB 5.1+. Tested design targets MongoDB 6.0+.
- Explain endpoint results depend on the actual dataset size and query planner behavior at runtime.

## Submit

A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and this `README.md`. No `.env`, no secrets, no Dockerfile.
