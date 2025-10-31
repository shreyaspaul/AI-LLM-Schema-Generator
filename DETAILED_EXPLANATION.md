# Detailed Explanation: n8n Integration with Progress Tracking

## Table of Contents
1. [What is Polling?](#what-is-polling)
2. [What I've Built](#what-ive-built)
3. [Technical Changes Made](#technical-changes-made)
4. [Data Flow Explained](#data-flow-explained)
5. [How to Test](#how-to-test)
6. [API Endpoints Deep Dive](#api-endpoints-deep-dive)

---

## What is Polling?

**Polling** is a technique where a client repeatedly asks a server "Are you done yet?" at regular intervals, instead of waiting for the server to tell them when it's ready.

### Real-World Analogy
Imagine you order food delivery:
- **Blocking/Waiting**: You stand at the door waiting until the food arrives (wasteful, can't do other things)
- **Polling**: You check the door every 2 minutes to see if food is there (can do other things between checks)
- **Push Notification**: You get a text when food arrives (best, but requires infrastructure)

### In Our Context

**Before (Blocking `/crawl` endpoint):**
```
n8n → POST /crawl → [Wait 5 minutes] → Get ZIP file
     ↑                                    ↓
     └───────────── Nothing happens ──────┘
     (n8n connection stays open, can timeout)
```

**After (Async with Polling `/crawl/async`):**
```
n8n → POST /crawl/async → Get job_id immediately (takes 0.1 seconds)
     ↓
n8n → GET /crawl/status/job_id → {"status": "running", "progress": [...]}
     ↓ (wait 5 seconds, can do other things)
n8n → GET /crawl/status/job_id → {"status": "running", "progress": [...]}
     ↓ (wait 5 seconds)
n8n → GET /crawl/status/job_id → {"status": "completed", "progress": [...]}
     ↓
n8n → GET /crawl/result/job_id → Download ZIP file
```

**Benefits of Polling:**
- ✅ No long timeouts needed
- ✅ Can see progress in real-time
- ✅ n8n can handle multiple requests easily
- ✅ Better error handling
- ✅ Can cancel/check status independently

---

## What I've Built

### 1. **Progress Callback System**
Modified the crawler to emit progress events that can be captured by external systems.

### 2. **Async Job System**
Created a job queue system where each crawl gets a unique ID, runs in background, and can be queried.

### 3. **Three API Endpoints for Different Use Cases**

#### A. `/crawl` - Simple Blocking (Original)
- Send request → Wait → Get ZIP
- Good for: Quick crawls, simple scripts
- Bad for: n8n (long timeouts needed)

#### B. `/crawl/async` - Async with Polling ⭐ **New**
- Send request → Get job_id immediately → Poll for status → Get ZIP when done
- Good for: n8n, long-running tasks, progress tracking
- Perfect for: Production workflows

#### C. `/crawl/stream` - Server-Sent Events
- Send request → Stream progress as it happens → Get ZIP at end
- Good for: Web browsers, custom UIs
- Bad for: n8n (doesn't support SSE well)

---

## Technical Changes Made

### Change 1: Progress Callback Infrastructure

**File:** `schema_crawler.py`

**What Changed:**
1. Added global variable `_progress_callback` to store callback function
2. Modified `log_info()`, `log_warn()`, `log_error()` to call callback if set
3. Added `set_progress_callback()` function to register callback
4. Modified `crawl()` function to accept `progress_callback` parameter

**Code Flow:**
```python
# Before
def log_info(message: str):
    print("[INFO] " + message)  # Only prints to console

# After
def log_info(message: str):
    print("[INFO] " + message)  # Still prints
    if _progress_callback:
        _progress_callback("info", message)  # ALSO sends to callback
```

**When Progress is Emitted:**
- When crawl starts (banner, config info)
- When sitemap is discovered
- When URLs are found in sitemap
- When screenshot is captured (if vision enabled)
- When each page is saved (with count: "[1/10] Saved: ...")
- When index is written
- On any errors or warnings

### Change 2: Async Job System

**File:** `app.py`

**What Changed:**
1. Added in-memory job storage dictionary: `jobs = {}`
2. Created `/crawl/async` endpoint that:
   - Validates request
   - Creates unique job_id (UUID)
   - Starts crawler in background thread
   - Returns job_id immediately (202 Accepted)
3. Created `/crawl/status/<job_id>` endpoint that:
   - Looks up job by ID
   - Returns current status and all progress messages
4. Created `/crawl/result/<job_id>` endpoint that:
   - Checks if job is complete
   - Returns ZIP file or error message

**Job Data Structure:**
```python
jobs[job_id] = {
    "status": "running" | "completed" | "failed",
    "progress": [
        {"type": "info", "message": "...", "timestamp": "2024-..."},
        {"type": "info", "message": "...", "timestamp": "2024-..."}
    ],
    "base_url": "https://example.com",
    "created_at": "2024-01-01T00:00:00",
    "error": None or "error message",
    "zip_path": "/tmp/schema_gen_123/output/schema_output.zip",
    "filename": "schema_example.com_20240101_120000.zip",
    "temp_dir": "/tmp/schema_gen_123"  # For cleanup
}
```

### Change 3: Background Thread Execution

**How it Works:**
```python
# When /crawl/async is called:
1. Create job_id = "abc-123-def-456"
2. Initialize jobs[job_id] = {...}
3. Start background thread:
   thread = threading.Thread(target=run_crawl_job)
   thread.start()
4. Return job_id immediately (don't wait for crawl to finish)

# In background thread:
def run_crawl_job():
    progress_list = []
    
    def progress_callback(level, message):
        progress_list.append({"type": level, "message": message})
        jobs[job_id]["progress"] = progress_list  # Update job in real-time
    
    crawl(..., progress_callback=progress_callback)
    # When done, update jobs[job_id]["status"] = "completed"
```

**Why Background Threads?**
- Main Flask thread stays free to handle other requests
- Multiple crawls can run simultaneously
- API responds instantly (doesn't block)

### Change 4: Progress Collection

**How Progress Messages Flow:**
```
Crawler Code                    Progress Callback              Job Storage
────────────────────────────────────────────────────────────────────────────
log_info("Starting...")  →  progress_callback("info", "...")  →  jobs[job_id]["progress"].append(...)
log_info("Saved page 1")  →  progress_callback("info", "...")  →  jobs[job_id]["progress"].append(...)
log_warn("Failed URL")    →  progress_callback("warn", "...")  →  jobs[job_id]["progress"].append(...)
```

Each time `log_info()`, `log_warn()`, or `log_error()` is called:
1. Message is printed to console (for local development)
2. If callback exists, it's called with `(level, message)`
3. Callback adds to `progress_list`
4. `progress_list` is stored in `jobs[job_id]["progress"]`
5. When n8n polls `/crawl/status/<job_id>`, it gets all accumulated messages

---

## Data Flow Explained

### Scenario: n8n Workflow Crawling a Website

#### Step 1: Start Crawl Job
```
n8n HTTP Request Node
  ↓ POST /crawl/async
  ↓ Body: {"base_url": "https://example.com", "max_pages": 10}
  ↓
Flask API (app.py)
  ↓ Creates job_id = "abc-123"
  ↓ Initializes jobs["abc-123"] = {status: "running", progress: []}
  ↓ Starts background thread
  ↓ Returns immediately:
  ↓ Response: {
      "job_id": "abc-123",
      "status": "running",
      "status_url": "/crawl/status/abc-123"
    }
  ↓
n8n receives job_id (takes 0.1 seconds)
```

#### Step 2: Background Crawl Starts
```
Background Thread
  ↓ Calls crawl(..., progress_callback=callback)
  ↓
Crawler (schema_crawler.py)
  ↓ log_info("Starting crawl") 
  ↓ → callback("info", "Starting crawl")
  ↓ → jobs["abc-123"]["progress"].append({type: "info", message: "..."})
  ↓
  ↓ log_info("Found sitemap")
  ↓ → callback("info", "Found sitemap")
  ↓ → jobs["abc-123"]["progress"].append({type: "info", message: "..."})
  ↓
  ↓ For each page:
  ↓   log_info("✓ [1/10] Saved: ...")
  ↓   → callback("info", "✓ [1/10] Saved: ...")
  ↓   → jobs["abc-123"]["progress"].append({...})
```

#### Step 3: n8n Polls for Progress (Loop)
```
n8n Loop Node (runs every 5 seconds)
  ↓
n8n HTTP Request Node
  ↓ GET /crawl/status/abc-123
  ↓
Flask API
  ↓ Looks up jobs["abc-123"]
  ↓ Returns: {
      "job_id": "abc-123",
      "status": "running",
      "progress": [
        {"type": "info", "message": "Starting crawl", "timestamp": "..."},
        {"type": "info", "message": "Found 10 URLs", "timestamp": "..."},
        {"type": "info", "message": "✓ [1/10] Saved: ...", "timestamp": "..."},
        {"type": "info", "message": "✓ [2/10] Saved: ...", "timestamp": "..."}
      ]
    }
  ↓
n8n receives progress updates
  ↓
n8n Set Node (optional)
  ↓ Extracts latest: progress[progress.length - 1].message
  ↓ Stores: "Latest: ✓ [2/10] Saved: ..."
  ↓ (You can display this in n8n UI)
  ↓
n8n IF Node
  ↓ Checks: status == "completed"?
  ↓ If No → Wait 5s → Loop back
  ↓ If Yes → Continue to next step
```

#### Step 4: Get Result When Complete
```
n8n HTTP Request Node
  ↓ GET /crawl/result/abc-123
  ↓
Flask API
  ↓ Checks jobs["abc-123"]["status"] == "completed"
  ↓ Reads ZIP file from jobs["abc-123"]["zip_path"]
  ↓ Returns ZIP file as download
  ↓
n8n receives ZIP file
  ↓
n8n Save/Process Node
  ↓ Save to Google Drive, process files, etc.
```

### Complete Timeline Example

```
Time  | Action                           | Response/State
──────┼──────────────────────────────────┼──────────────────────────────
0:00  | n8n: POST /crawl/async           | job_id: "abc-123", status: "running"
      | Background: Crawl starts         | 
0:01  | Crawler: log_info("Starting...") | jobs["abc-123"]["progress"] = [{...}]
0:02  | Crawler: log_info("Found 10 URLs")| jobs["abc-123"]["progress"] = [{...}, {...}]
0:05  | n8n: GET /crawl/status/abc-123   | Returns 2 progress messages
      | n8n: Displays "Found 10 URLs"    |
0:08  | Crawler: log_info("✓ [1/10]")    | jobs["abc-123"]["progress"] = [..., {...}]
0:10  | n8n: GET /crawl/status/abc-123   | Returns 3 progress messages
      | n8n: Displays "✓ [1/10] Saved"   |
0:15  | Crawler: log_info("✓ [2/10]")    |
0:20  | n8n: GET /crawl/status/abc-123   | Returns 4 progress messages
      | n8n: Displays "✓ [2/10] Saved"   |
...   | (continues...)                   |
2:00  | Crawler: log_info("Wrote index") | Last progress message
      | Crawler: Sets status = "completed"|
2:05  | n8n: GET /crawl/status/abc-123   | Returns status: "completed"
      | n8n: IF node detects completion  |
2:06  | n8n: GET /crawl/result/abc-123   | Returns ZIP file
      | n8n: Downloads ZIP               |
```

---

## How to Test

### Test 1: Quick Manual Test (No n8n)

#### A. Start Your API Server
```bash
cd "/Users/shreyaspaul/code/AI-LLM -Schema-Generator"
source .venv/bin/activate
python3 app.py
```

You should see:
```
* Running on http://127.0.0.1:8000
```

#### B. Test Async Endpoint (Step by Step)

**Step 1: Start a job**
```bash
curl -X POST http://localhost:8000/crawl/async \
  -H "Content-Type: application/json" \
  -d '{"base_url": "https://www.webless.ai", "max_pages": 3}' | jq
```

**Expected Response:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "message": "Crawl job started",
  "status_url": "/crawl/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "result_url": "/crawl/result/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Copy the `job_id`** (e.g., `a1b2c3d4-e5f6-7890-abcd-ef1234567890`)

**Step 2: Poll for status (run this multiple times)**
```bash
# Replace JOB_ID with the actual job_id from Step 1
curl http://localhost:8000/crawl/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890 | jq
```

**Expected Response (while running):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "progress": [
    {
      "type": "info",
      "message": "Base: https://www.webless.ai",
      "timestamp": "2024-01-01T12:00:01"
    },
    {
      "type": "info",
      "message": "Found 10 URLs in sitemap",
      "timestamp": "2024-01-01T12:00:02"
    },
    {
      "type": "info",
      "message": "✓ [1/3] Saved: https://www.webless.ai -> output/pages/...",
      "timestamp": "2024-01-01T12:00:05"
    }
  ],
  "error": null,
  "created_at": "2024-01-01T12:00:00",
  "completed_at": null
}
```

**Keep polling every 5 seconds** - you'll see progress messages accumulate!

**Expected Response (when completed):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "progress": [
    ... (all messages from start to finish)
  ],
  "error": null,
  "created_at": "2024-01-01T12:00:00",
  "completed_at": "2024-01-01T12:00:30"
}
```

**Step 3: Get the ZIP file**
```bash
curl http://localhost:8000/crawl/result/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  -o test_result.zip
```

**Verify the ZIP:**
```bash
unzip -l test_result.zip
```

You should see:
```
Archive:  test_result.zip
  Length      Date    Time    Name
---------  ---------- -----   ----
     1234  01-01-2024 12:00   index.json
     5678  01-01-2024 12:00   pages/www-webless-ai-home.json
     5678  01-01-2024 12:00   pages/www-webless-ai-why-webless.json
     ...   prompts/www-webless-ai-home.txt
     ...
```

### Test 2: Automated Polling Script

Create a test script to simulate n8n polling:

```bash
# Create test_poll.sh
cat > test_poll.sh << 'EOF'
#!/bin/bash

# Start job
echo "Starting crawl job..."
RESPONSE=$(curl -s -X POST http://localhost:8000/crawl/async \
  -H "Content-Type: application/json" \
  -d '{"base_url": "https://www.webless.ai", "max_pages": 3}')

JOB_ID=$(echo $RESPONSE | jq -r '.job_id')
echo "Job ID: $JOB_ID"
echo ""

# Poll every 5 seconds until complete
while true; do
  STATUS_RESPONSE=$(curl -s http://localhost:8000/crawl/status/$JOB_ID)
  STATUS=$(echo $STATUS_RESPONSE | jq -r '.status')
  LATEST_MSG=$(echo $STATUS_RESPONSE | jq -r '.progress[-1].message // "..."')
  
  echo "[$(date +%H:%M:%S)] Status: $STATUS | Latest: $LATEST_MSG"
  
  if [ "$STATUS" = "completed" ]; then
    echo ""
    echo "✅ Job completed! Downloading ZIP..."
    curl -s http://localhost:8000/crawl/result/$JOB_ID -o result_${JOB_ID}.zip
    echo "✅ Downloaded: result_${JOB_ID}.zip"
    break
  elif [ "$STATUS" = "failed" ]; then
    ERROR=$(echo $STATUS_RESPONSE | jq -r '.error')
    echo "❌ Job failed: $ERROR"
    break
  fi
  
  sleep 5
done
EOF

chmod +x test_poll.sh
./test_poll.sh
```

### Test 3: Test in n8n (Full Integration)

#### Step 1: Setup n8n
1. Have n8n running (local or cloud)
2. Create new workflow

#### Step 2: Create Workflow Nodes

**Node 1: Manual Trigger**
- Type: Manual Trigger
- Just to start the workflow

**Node 2: Start Crawl (HTTP Request)**
- **Name:** "Start Crawl"
- **Method:** POST
- **URL:** `http://localhost:8000/crawl/async` (or your deployed URL)
- **Body Type:** JSON
- **Body:**
```json
{
  "base_url": "https://www.webless.ai",
  "max_pages": 3
}
```
- **Options:** 
  - Response Format: JSON
  - Timeout: 30 seconds

**Node 3: Loop (for polling)**
- **Name:** "Poll Status"
- **Type:** Loop
- **Mode:** Loop Over Items
- **Max Iterations:** 120 (10 minutes @ 5s intervals)

**Node 4: HTTP Request (Poll)**
- **Name:** "Check Status"
- **Method:** GET
- **URL:** `http://localhost:8000/crawl/status/{{ $('Start Crawl').item.json.job_id }}`
- **Response Format:** JSON

**Node 5: Set Node (Display Progress)**
- **Name:** "Show Progress"
- **Operation:** Set
- **Fields:**
  - `status`: `{{ $json.status }}`
  - `latest_message`: `{{ $json.progress[$json.progress.length - 1]?.message || 'Processing...' }}`
  - `progress_count`: `{{ $json.progress.length }}`

**Node 6: IF Node (Check if Complete)**
- **Name:** "Is Complete?"
- **Condition:** `{{ $json.status }}` equals `"completed"`

**Node 7: Wait Node (if not complete)**
- **Name:** "Wait 5 seconds"
- **Amount:** 5
- **Unit:** seconds

**Node 8: HTTP Request (Get Result)**
- **Name:** "Get ZIP"
- **Method:** GET
- **URL:** `http://localhost:8000/crawl/result/{{ $('Start Crawl').item.json.job_id }}`
- **Response Format:** File

**Node 9: Save/Process**
- **Name:** "Save Result"
- Type: Google Drive, Dropbox, or Code node to process

#### Step 3: Connect Nodes
```
Manual Trigger → Start Crawl → Poll Status (Loop)
                                          ↓
                                    Check Status
                                          ↓
                                    Show Progress
                                          ↓
                                    Is Complete?
                              ┌──────────┴──────────┐
                          Yes │                    │ No
                              ↓                    ↓
                         Get ZIP              Wait 5s
                              ↓                    ↓
                         Save Result          ────┘
                                    (loop back)
```

#### Step 4: Execute and Watch
1. Click "Execute Workflow"
2. Watch the execution logs
3. See progress messages in "Show Progress" node output
4. ZIP file appears when complete

---

## API Endpoints Deep Dive

### Endpoint 1: `POST /crawl/async`

**Purpose:** Start a crawl job asynchronously

**Request:**
```http
POST /crawl/async HTTP/1.1
Content-Type: application/json

{
  "base_url": "https://example.com",
  "max_pages": 10,
  "rate_limit": 1.0,
  "api_key": "sk-..."  // Optional, uses env var if not provided
}
```

**What Happens Inside:**
1. Validates request (checks for base_url)
2. Gets API key (from request or env var)
3. Generates unique UUID: `job_id = str(uuid.uuid4())`
4. Creates job entry in `jobs` dictionary
5. Starts background thread that runs the crawler
6. Returns immediately with job_id

**Response:**
```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "message": "Crawl job started",
  "status_url": "/crawl/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "result_url": "/crawl/result/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Status Code 202:** Means "Accepted" - request is valid, processing started, but not complete yet.

### Endpoint 2: `GET /crawl/status/<job_id>`

**Purpose:** Check job progress and status

**Request:**
```http
GET /crawl/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890 HTTP/1.1
```

**What Happens Inside:**
1. Looks up `job_id` in `jobs` dictionary
2. If not found → 404
3. Returns current state of job

**Response (Running):**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "progress": [
    {
      "type": "info",
      "message": "Starting crawl",
      "timestamp": "2024-01-01T12:00:00.123456"
    },
    {
      "type": "info",
      "message": "Found 10 URLs in sitemap",
      "timestamp": "2024-01-01T12:00:01.234567"
    },
    {
      "type": "info",
      "message": "✓ [1/10] Saved: https://example.com -> output/pages/...",
      "timestamp": "2024-01-01T12:00:05.345678"
    }
  ],
  "error": null,
  "created_at": "2024-01-01T12:00:00.000000",
  "completed_at": null
}
```

**Response (Completed):**
```json
{
  "job_id": "...",
  "status": "completed",
  "progress": [...],  // All messages from start to finish
  "error": null,
  "created_at": "2024-01-01T12:00:00",
  "completed_at": "2024-01-01T12:00:30"
}
```

**Response (Failed):**
```json
{
  "job_id": "...",
  "status": "failed",
  "progress": [...],  // Messages up to failure
  "error": "Crawler failed: Connection timeout",
  "created_at": "2024-01-01T12:00:00",
  "completed_at": "2024-01-01T12:00:15"
}
```

**Polling Strategy:**
- Poll every 5 seconds (not too frequent, not too slow)
- Stop when `status` is `"completed"` or `"failed"`
- Display `progress[progress.length - 1].message` for latest update

### Endpoint 3: `GET /crawl/result/<job_id>`

**Purpose:** Get the ZIP file result

**Request:**
```http
GET /crawl/result/a1b2c3d4-e5f6-7890-abcd-ef1234567890 HTTP/1.1
```

**What Happens Inside:**
1. Looks up job
2. Checks status:
   - If "running" → 202 (still processing)
   - If "failed" → 500 (with error message)
   - If "completed" → Read ZIP file and send it

**Response (Completed):**
```http
HTTP/1.1 200 OK
Content-Type: application/zip
Content-Disposition: attachment; filename="schema_example.com_20240101_120000.zip"

[Binary ZIP file data]
```

**Response (Still Running):**
```http
HTTP/1.1 202 Accepted
Content-Type: application/json

{
  "error": "Job is still running",
  "status": "running",
  "status_url": "/crawl/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

---

## Complete Testing Checklist

### ✅ Test Checklist

#### Basic Functionality
- [ ] API server starts without errors
- [ ] `/health` endpoint returns 200
- [ ] `/crawl/async` creates job and returns job_id
- [ ] `/crawl/status/<job_id>` returns job status
- [ ] `/crawl/result/<job_id>` returns ZIP when complete

#### Progress Tracking
- [ ] Progress messages appear in status response
- [ ] Progress array grows as crawl proceeds
- [ ] Latest progress message shows current activity
- [ ] Progress includes timestamps

#### Error Handling
- [ ] Invalid job_id returns 404
- [ ] Missing base_url returns 400
- [ ] Failed crawl sets status to "failed"
- [ ] Error message appears in status response

#### n8n Integration
- [ ] Can import workflow JSON
- [ ] Loop node polls correctly
- [ ] IF node detects completion
- [ ] ZIP file downloads correctly
- [ ] Progress visible in n8n execution logs

---

## Troubleshooting Guide

### Issue: "Job not found" (404)

**Cause:** Job ID doesn't exist or server restarted (jobs are in-memory)

**Solution:**
- Jobs are stored in memory, so server restart loses them
- For production, use Redis or database (see Production Notes)

### Issue: Status always shows "running"

**Cause:** Crawler might be stuck or taking very long

**Solution:**
- Check server logs for errors
- Increase timeout in crawler
- Check if URLs are accessible

### Issue: No progress messages

**Cause:** Progress callback not working

**Solution:**
- Verify crawler is actually running
- Check server console for log messages
- Ensure `progress_callback` is being called

### Issue: n8n timeout errors

**Cause:** Using blocking `/crawl` endpoint

**Solution:**
- Use `/crawl/async` instead
- Set n8n HTTP timeout to 30s (just for starting job)
- Polling requests should be fast (< 1s)

---

## Production Considerations

### Current Limitation: In-Memory Job Storage

**Problem:** Jobs are stored in `jobs = {}` dictionary in memory
- Lost on server restart
- Lost if multiple server instances (no sharing)
- Not persistent

**For Production, Use:**
- **Redis**: Fast, supports TTL, works with multiple servers
- **Database (PostgreSQL/MySQL)**: Persistent, queryable
- **File-based**: Simple, but not great for scale

**Quick Redis Implementation (Future):**
```python
import redis
r = redis.Redis(host='localhost', port=6379, db=0)

# Store job
r.setex(f"job:{job_id}", 3600, json.dumps(job_data))  # Expires in 1 hour

# Get job
job_data = json.loads(r.get(f"job:{job_id}"))
```

### Rate Limiting

For public API, add rate limiting:
```python
from flask_limiter import Limiter

limiter = Limiter(app, key_func=get_remote_address)
limiter.limit("10 per minute")(crawl_async_endpoint)
```

### Authentication

For sharing publicly, consider:
- API key authentication
- Per-user rate limits
- Usage tracking

---

## Summary

**What I Built:**
1. ✅ Progress callback system in crawler
2. ✅ Async job system with job_id tracking
3. ✅ Three endpoints: `/crawl/async`, `/crawl/status/<id>`, `/crawl/result/<id>`
4. ✅ Background thread execution
5. ✅ Real-time progress updates
6. ✅ Complete n8n workflow example

**How It Works:**
1. Client starts job → Gets job_id immediately
2. Client polls `/status/<job_id>` every 5 seconds
3. Server accumulates progress in memory
4. Client detects completion from status
5. Client downloads ZIP from `/result/<job_id>`

**Why This is Better for n8n:**
- ✅ No long timeouts
- ✅ Progress visibility
- ✅ Better error handling
- ✅ Non-blocking
- ✅ Works with n8n's Loop node

---

## Next Steps

1. **Test locally** using the curl commands above
2. **Import n8n workflow** (`n8n_workflow_example.json`)
3. **Deploy to cloud** (Railway/Render) for public sharing
4. **Share with LinkedIn** - include workflow JSON and API URL

