# Quick Start: Testing Async Polling Endpoints

This guide will help you test the async polling system step-by-step in under 5 minutes.

---

## Prerequisites

1. ✅ API server is ready to run
2. ✅ Virtual environment activated
3. ✅ `.env` file has `OPENAI_API_KEY`
4. ✅ `jq` installed (for JSON parsing) - if not: `brew install jq` (macOS)

---

## Test Method 1: Manual curl Commands (5 minutes)

### Step 1: Start Your API Server

Open Terminal 1:
```bash
cd "/Users/shreyaspaul/code/AI-LLM -Schema-Generator"
source .venv/bin/activate
python3 app.py
```

You should see:
```
* Running on http://127.0.0.1:8000
```

**Keep this terminal open!** This is your server.

---

### Step 2: Start a Crawl Job

Open Terminal 2:
```bash
curl -X POST http://localhost:8000/crawl/async \
  -H "Content-Type: application/json" \
  -d '{"base_url": "https://www.webless.ai", "max_pages": 3}' | jq
```

**Expected Output:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "running",
  "message": "Crawl job started",
  "status_url": "/crawl/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "result_url": "/crawl/result/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**📝 Copy the `job_id`** (e.g., `a1b2c3d4-e5f6-7890-abcd-ef1234567890`)

---

### Step 3: Check Status (Poll)

Still in Terminal 2, replace `YOUR_JOB_ID` with the actual job_id:

```bash
# Replace YOUR_JOB_ID with your actual job_id
JOB_ID="a1b2c3d4-e5f6-7890-abcd-ef1234567890"

curl http://localhost:8000/crawl/status/$JOB_ID | jq
```

**Expected Output (while running):**
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
      "message": "✓ [1/3] Saved: https://www.webless.ai -> output/pages/www-webless-ai-home.json",
      "timestamp": "2024-01-01T12:00:05"
    }
  ],
  "error": null,
  "created_at": "2024-01-01T12:00:00"
}
```

**🔁 Run this command again every 5 seconds** - you'll see new progress messages appearing!

---

### Step 4: Check Status Again (After ~30 seconds)

```bash
curl http://localhost:8000/crawl/status/$JOB_ID | jq
```

**Expected Output (when complete):**
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

Notice `"status": "completed"` - this means the job is done!

---

### Step 5: Download the ZIP File

```bash
curl http://localhost:8000/crawl/result/$JOB_ID -o result.zip
```

**Verify:**
```bash
ls -lh result.zip
unzip -l result.zip
```

You should see:
```
Archive:  result.zip
  Length      Date    Time    Name
---------  ---------- -----   ----
     1234  01-01-2024 12:00   index.json
     5678  01-01-2024 12:00   pages/www-webless-ai-home.json
     ...   prompts/www-webless-ai-home.txt
```

**✅ Success!** You've tested the complete async flow!

---

## Test Method 2: Automated Script (1 minute)

### Step 1: Make Script Executable

```bash
cd "/Users/shreyaspaul/code/AI-LLM -Schema-Generator"
chmod +x test_async.sh
```

### Step 2: Run the Script

```bash
./test_async.sh https://www.webless.ai 3
```

**What it does:**
1. Starts crawl job
2. Polls status every 5 seconds
3. Shows progress in real-time
4. Downloads ZIP when complete

**Expected Output:**
```
🚀 Testing Async API Endpoints
API URL: http://localhost:8000
Target URL: https://www.webless.ai
Max Pages: 3

📤 Step 1: Starting crawl job...
✅ Job started!
   Job ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
   Status: running

🔄 Step 2: Polling for status (every 5 seconds)...

[12:00:05] Status: running | Messages: 2 | Latest: Found 10 URLs in sitemap
[12:00:10] Status: running | Messages: 3 | Latest: ✓ [1/3] Saved: https://www.webless.ai -> ...
[12:00:15] Status: running | Messages: 4 | Latest: ✓ [2/3] Saved: https://www.webless.ai/about -> ...
[12:00:20] Status: running | Messages: 5 | Latest: ✓ [3/3] Saved: https://www.webless.ai/pricing -> ...
[12:00:25] Status: completed | Messages: 6 | Latest: Wrote index.json

✅ Job completed!

📥 Step 3: Downloading result ZIP...
✅ ZIP downloaded: result_a1b2c3d4-e5f6-7890-abcd-ef1234567890.zip (45KB)

📦 ZIP contents:
Archive:  result_a1b2c3d4-e5f6-7890-abcd-ef1234567890.zip
  Length      Date    Time    Name
---------  ---------- -----   ----
     1234  01-01-2024 12:00   index.json
     5678  01-01-2024 12:00   pages/www-webless-ai-home.json
     ...

🎉 Test completed successfully!
```

---

## Test Method 3: Test in n8n (10 minutes)

### Step 1: Have n8n Running

- Local: `n8n start`
- Cloud: Use your n8n instance

### Step 2: Import Workflow

1. Open n8n
2. Click "Workflows" → "Import from File"
3. Select `n8n_workflow_example.json`
4. Open the imported workflow

### Step 3: Configure API URL

1. Find "Start Crawl" node (HTTP Request)
2. Update URL to: `http://localhost:8000/crawl/async` (or your deployed URL)
3. Update Body with your test URL:
   ```json
   {
     "base_url": "https://www.webless.ai",
     "max_pages": 3
   }
   ```

4. Find "Check Status" node
5. Update URL to: `http://localhost:8000/crawl/status/{{ $('Start Crawl').item.json.job_id }}`

6. Find "Get ZIP" node
7. Update URL to: `http://localhost:8000/crawl/result/{{ $('Start Crawl').item.json.job_id }}`

### Step 4: Execute

1. Click "Execute Workflow"
2. Watch execution flow
3. Check "Show Progress" node output - see progress messages!
4. Wait for completion
5. Check "Get ZIP" node - ZIP file should appear

---

## Troubleshooting

### Issue: "Job not found" (404)

**Cause:** Server restarted or job_id is wrong

**Solution:**
- Jobs are stored in memory, restarting server clears them
- Start a new job
- Check that job_id matches exactly

---

### Issue: Status stays "running" forever

**Cause:** Crawler stuck or taking very long

**Solution:**
1. Check Terminal 1 (server logs) for errors
2. Try with smaller `max_pages` (e.g., 1)
3. Check if target URL is accessible

---

### Issue: No progress messages

**Cause:** Progress callback not working

**Solution:**
1. Check server logs in Terminal 1
2. Verify crawler is actually running (you should see logs)
3. Try starting a new job

---

### Issue: ZIP file is empty or corrupted

**Cause:** Crawl failed but status wasn't updated

**Solution:**
1. Check status response for `"error"` field
2. Check server logs
3. Try again with smaller site

---

## What to Look For

### ✅ Success Indicators:

1. **Job Creation:**
   - `POST /crawl/async` returns `job_id` immediately
   - Status is `202 Accepted`

2. **Progress Tracking:**
   - `GET /crawl/status/<id>` shows growing `progress` array
   - Messages appear in real-time
   - Latest message shows current activity

3. **Completion:**
   - Status changes from `"running"` to `"completed"`
   - `completed_at` timestamp appears
   - Progress array contains all messages

4. **Result Download:**
   - `GET /crawl/result/<id>` returns ZIP file
   - ZIP contains `index.json`, `pages/*.json`, `prompts/*.txt`

---

## Next Steps

Once testing works:

1. ✅ Deploy to cloud (Railway/Render)
2. ✅ Update n8n workflow with production URL
3. ✅ Share with your LinkedIn connections!

---

## Quick Reference

### Endpoints Summary

| Endpoint | Method | Purpose | Response Time |
|----------|--------|---------|---------------|
| `/crawl/async` | POST | Start job | Instant (0.1s) |
| `/crawl/status/<id>` | GET | Check progress | Fast (< 1s) |
| `/crawl/result/<id>` | GET | Get ZIP | Fast (< 1s) |

### Polling Strategy

- **Frequency:** Every 5 seconds
- **Stop When:** `status == "completed"` or `status == "failed"`
- **Display:** Latest progress message: `progress[progress.length - 1].message`

---

**Questions?** Check `DETAILED_EXPLANATION.md` for comprehensive documentation!

