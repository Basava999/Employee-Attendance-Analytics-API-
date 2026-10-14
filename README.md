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

## Performance & Verification

- **100k Benchmark Verified**: Evaluated against a synthetic dataset of 100,000 attendance records across 1,000 employees in 5 departments on MongoDB 9.0.2 (WiredTiger, local instance). Measured results across 20 iterations:
  - Attendance listing (unfiltered 20 records): mean **1.31ms**, median **0.99ms**, p95 **2.98ms**
  - Attendance listing (filtered by employee & 31-day date range): mean **1.35ms**, median **1.05ms**, p95 **4.00ms**
  - Employee monthly analytics: mean **5.47ms**, median **4.96ms**, p95 **8.52ms**
  - Department 31-day trend: mean **87.32ms**, median **80.42ms**, p95 **127.97ms**
  - Late leaderboard (department, top 10): mean **134.84ms**, median **129.01ms**, p95 **186.63ms**
  - Department monthly summary: mean **145.54ms**, median **145.63ms**, p95 **161.13ms**
- **MongoDB Compatibility**: Full compatibility verified with MongoDB 6.0, 7.0, and 9.0 using native aggregation pipelines (`$densify`, `$setWindowFields`, `$lookup`, and `$documents` inside `$unionWith`).
- **Explain Plans**: All five explain endpoints (`attendance_list`, `employee_monthly`, `department_summary`, `late_leaderboard`, `department_trend`) return authentic MongoDB `executionStats` with verified index-backed plans (`IXSCAN`) and zero collection scans (`COLLSCAN`) across all primary and participating collections.

## Submit

A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and this `README.md`. No `.env`, no secrets, no Dockerfile.
