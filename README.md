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
# Dependencies are included in requirements.txt (pytest & httpx included)
pip install -r requirements.txt

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
REVIEW.md            — Starter code defect review (51 defects found and repaired)
DECISIONS.md         — Design decision answers (5 questions)
requirements.txt     — Python dependencies (includes application, pytest, httpx)
.env.example         — Environment variable template
.gitignore           — Git ignore rules
```

## Performance & Index Design

- **Index Optimization**: All query paths are backed by targeted compound and unique indexes created automatically at startup:
  - `employees`: `idx_emp_code_unique` (unique on `emp_code`), `idx_dept_joined` (`department`, `joined_on`), `idx_joined_on` (`joined_on`).
  - `attendance_logs`: `idx_empcode_date_unique` (unique on `emp_code`, `date`), `idx_date_desc_empcode` (`date` desc, `emp_code` asc).
- **Explain Endpoints**: Five diagnostic endpoints under `/admin/explain/{endpoint}` return MongoDB `executionStats` for verifying execution stages and index utilization.
- **Aggregation Pipelines**: Analytics use native MongoDB aggregation pipelines (`$match`, `$lookup`, `$densify`, `$setWindowFields`) structured so that filtering and sorting occur before pipeline transformations.


## Submit

A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and this `README.md`. No `.env`, no secrets, no Dockerfile.
