# Game Leaderboard

**Language:** Python (FastAPI) &nbsp;|&nbsp; **Needs:** Postgres + Redis

This is a **starter**. The application already works. Your job is everything
that gets it building, tested and running in CI.

---

## You do not need Python installed

You will build this into a container, and the container brings its own
Python 3.12. You are not being asked to extend the app — you are being asked
to ship it.

---

## 1. What this app needs

| | |
|---|---|
| **Runtime** | Python 3.12 |
| **Install dependencies** | `pip install -r requirements.txt` |
| **Start the app** | `uvicorn app.main:app --host 0.0.0.0 --port 8080` |
| **Listens on** | port 8080, bound to `0.0.0.0` |
| **Environment variables** | `DATABASE_URL`, `REDIS_URL` |
| **Needs running first** | Postgres, Redis, and the migrations applied |

### What it does

Players submit the score from a run. The app keeps a global top 100, a board per region, and a weekly board that rolls over every Monday. It will also tell a player their own rank and show them their neighbours - which has to stay fast when that rank is 40,000 out of 50,000.

### Endpoints

```
GET  /health
POST /scores                      {"player":"asha","region":"north","score":48000,
                                   "run_id":"r-991","seconds":180}
GET  /leaderboard?scope=global&page=1&size=20     scope: global | region | week
GET  /players/{name}              their rank, their neighbours, their best runs
GET  /regions/{region}/podium     top 10, with ties sharing a rank
POST /admin/rebuild               rebuild the Redis boards from Postgres
```

`/health` reports Postgres and Redis **separately**. If it says
`postgres: false` the app started fine and your compose wiring is wrong —
do not go looking in the application code.

### Migrations

`migrations/` holds `.sql` files applied **in filename order** before the app
starts. They create the tables and insert sample data. A container running
`psql` over them in order is enough; you do not need a migration tool.

---

## 2. What you must write

| File | What it has to do |
|---|---|
| `Dockerfile` | Install dependencies **before** copying source, pin the base image, do not run as root. |
| `docker-compose.yml` | App + Postgres + Redis + a migration step, one `docker compose up`. |
| `.circleci/config.yml` | lint → unit tests → integration tests → secret scan → image build |
| Unit tests | For `app/scoring.py`. No database, no network. |
| Integration tests | Against a real Postgres and Redis as CircleCI service containers. |

Then push your image to **your own Docker Hub account**, tagged `:1.0`.

### When it works

```bash
docker compose up --build
curl localhost:8080/health
```

```json
{"status":"ok","postgres":true,"redis":true}
```

---

## Where the marks are

`app/scoring.py` is **pure logic** — plain functions over plain data, no
database and no HTTP. Start your tests there. Use pytest:
`pytest --cov=app --cov-report=term-missing`. Minimum 70%.

Start with `encode` and `decode`: generate a few thousand random (score, time) pairs and assert the round trip. Then assert the ordering property directly - a higher score always encodes higher, and on equal scores the earlier run encodes higher. After that, `validate_run` has seven distinct rejection reasons and each one is a test.

## Why Redis is here

Sorted sets. Getting one player's rank out of fifty thousand is a single ZREVRANK; the same question in SQL is a window function over the whole table on every page load. Submitting a score is ZADD with the GT flag, so a worse run never drags a player down the board. The weekly board is its own key with an EXPIRE, so it cleans itself up instead of needing a cron job.

Redis starts empty and the seed only fills Postgres, so the boards are rebuilt from the database the first time anyone reads them. Losing Redis costs you speed, not data.

## The hard part

**Ties. Two players on the same score need a stable, defensible order.**

A sorted set holds one number per player, and when two numbers are equal Redis falls back to ordering by member name - so the tie goes to whoever's username sorts first. Decide what the rule should be instead, make the board obey it, and work out the largest score your scheme still handles exactly.

Write your answer in your README. It is worth more marks than the feature.

---

## Getting unstuck

| Symptom | Almost always |
|---|---|
| `/health` says `postgres: false` | Wrong hostname. In compose the host is the **service name**, not `localhost`. |
| Page will not load, logs fine | No `ports:` mapping, or bound to `127.0.0.1` not `0.0.0.0`. |
| `relation "..." does not exist` | Migrations did not run, or the app started before they finished. |
| Build takes minutes each time | `COPY . .` is above your dependency install. |
| CI cannot reach the database | In CircleCI service containers the host **is** `localhost` — opposite of compose. |
