# Setup Guide: n8n + Render

## Part 1: Render Deployment (Deploy Your API First)

### Step 1: Prepare Your Code

1. **Make sure these files exist in your project:**
   - `app.py`
   - `schema_crawler.py`
   - `requirements.txt`
   - `Dockerfile`
   - `.env.example` (optional, for reference)

2. **Push to GitHub:**
   ```bash
   git add .
   git commit -m "Ready for deployment"
   git push origin main
   ```

### Step 2: Create Render Account

1. Go to: https://render.com
2. Sign up (free tier works)
3. Verify your email

### Step 3: Create New Web Service

1. In Render dashboard, click **"New +"** → **"Web Service"**
2. Connect your GitHub repository
3. Select your repository: `AI-LLM -Schema-Generator`

### Step 4: Configure Render Service

**Settings:**

- **Name:** `ai-schema-generator` (or any name)
- **Environment:** `Python 3`
- **Region:** Choose closest to you
- **Branch:** `main`
- **Root Directory:** Leave empty
- **Build Command:** `pip install -r requirements.txt && playwright install chromium --with-deps`
- **Start Command:** `python3 app.py`

**Environment Variables:**

Click "Add Environment Variable" and add:
- **Key:** `OPENAI_API_KEY`
- **Value:** `sk-proj-...` (your actual OpenAI API key)
- Click "Add Another" and add:
  - **Key:** `PORT`
  - **Value:** `8000`

**Advanced Settings:**
- **Auto-Deploy:** `Yes` (deploys on every push)
- Click **"Create Web Service"**

### Step 5: Wait for Deployment

1. Render will:
   - Install dependencies
   - Install Playwright browsers
   - Start your API
2. Watch the logs - it takes 5-10 minutes first time
3. When you see: `* Running on http://0.0.0.0:8000` → **Done!**

### Step 6: Get Your API URL

1. In Render dashboard, find your service
2. Copy the **URL** (e.g., `https://ai-schema-generator.onrender.com`)
3. **Save this URL** - you'll need it for n8n

### Step 7: Test Your Deployed API

```bash
curl https://ai-schema-generator.onrender.com/health
```

You should get:
```json
{"status": "healthy", "openai_configured": true}
```

---

## Part 2: n8n Setup

### Step 1: Open n8n

- **Cloud:** Go to https://n8n.io and log in
- **Self-hosted:** Go to your n8n URL (e.g., `http://localhost:5678`)

### Step 2: Create New Workflow

1. Click **"Workflows"** in sidebar
2. Click **"Add Workflow"** (or "+" button)
3. Name it: "AI Schema Generator"

### Step 3: Add Nodes (One by One)

#### Node 1: Manual Trigger
1. Click **"+"** to add node
2. Search: **"Manual Trigger"**
3. Click it
4. Leave settings as default

#### Node 2: HTTP Request (Start Crawl)
1. Click **"+"** after Manual Trigger node
2. Search: **"HTTP Request"**
3. Configure:
   - **Method:** `POST`
   - **URL:** `https://your-render-url.onrender.com/crawl/async`
     - Replace `your-render-url.onrender.com` with your actual Render URL
   - **Authentication:** `None`
   - **Body Content Type:** `JSON`
   - **Specify Body:** `Using JSON`
   - **JSON Body:**
     ```json
     {
       "base_url": "https://www.webless.ai",
       "max_pages": 5
     }
     ```
4. Click **"Execute Node"** (test it)
5. You should see: `{"job_id": "...", "status": "running"}`

#### Node 3: Loop Over Items
1. Click **"+"** after HTTP Request node
2. Search: **"Loop Over Items"**
3. Leave settings as default
4. **Important:** This will loop once (we need it for the polling cycle)

#### Node 4: HTTP Request (Poll Status)
1. Click **"+"** after Loop node
2. Search: **"HTTP Request"**
3. Name it: **"Poll Status"**
4. Configure:
   - **Method:** `GET`
   - **URL:** `=https://your-render-url.onrender.com/crawl/status/{{ $('HTTP Request').item.json.job_id }}`
     - Replace `your-render-url.onrender.com` with your Render URL
     - The `=` at start means "expression" (dynamic)
     - `$('HTTP Request')` refers to the first HTTP Request node
5. Click **"Execute Node"** to test

#### Node 5: IF Node (Check if Complete)
1. Click **"+"** after Poll Status node
2. Search: **"IF"**
3. Name it: **"Is Complete?"**
4. Configure:
   - **Condition:** Add condition
   - **Value 1:** `={{ $json.status }}`
   - **Operation:** `Equal`
   - **Value 2:** `completed`
5. Click **"Execute Node"** to test

#### Node 6: Wait Node (Wait 5 seconds)
1. Click **"+"** after Poll Status node (on the "false" output of IF node)
2. Search: **"Wait"**
3. Configure:
   - **Amount:** `5`
   - **Unit:** `Seconds`
4. Connect this back to "Poll Status" node (creates a loop)

#### Node 7: HTTP Request (Get ZIP)
1. Click **"+"** after IF node (on the "true" output)
2. Search: **"HTTP Request"**
3. Name it: **"Get ZIP"**
4. Configure:
   - **Method:** `GET`
   - **URL:** `=https://your-render-url.onrender.com/crawl/result/{{ $('HTTP Request').item.json.job_id }}`
   - **Response:** `File`
5. Click **"Execute Node"** to test

### Step 4: Connect the Nodes

Your workflow should look like:

```
Manual Trigger
    ↓
HTTP Request (Start Crawl)
    ↓
Loop Over Items
    ↓
HTTP Request (Poll Status)
    ↓
IF (Is Complete?)
    ├─ Yes → HTTP Request (Get ZIP)
    └─ No → Wait 5 seconds → (back to Poll Status)
```

**To connect:**
- Drag from output dot of one node to input dot of next node
- For IF node: Connect "true" to Get ZIP, "false" to Wait

### Step 5: Fix the Loop

**Problem:** The current setup won't loop properly.

**Solution:** Use a different approach:

1. **Delete** "Loop Over Items" node
2. **Delete** "Wait 5 seconds" node connections
3. **Reconnect:**
   - Start Crawl → Poll Status
   - Poll Status → IF
   - IF (false) → Wait 5 seconds → Poll Status (creates loop)
   - IF (true) → Get ZIP

4. **Add Loop node** (after Poll Status):
   - Add **"Loop"** node after Poll Status
   - Set **Mode:** `Loop Until`
   - **Value:** `={{ $json.status }}`
   - **Operation:** `Equal`
   - **Compare Value:** `completed`
   - **Max Iterations:** `120` (10 minutes max)

**Better flow:**
```
Manual Trigger
    ↓
HTTP Request (Start Crawl)
    ↓
Loop (until status = completed)
    ├─ HTTP Request (Poll Status)
    ├─ Wait 5 seconds
    └─ (loop back)
    ↓ (when complete)
HTTP Request (Get ZIP)
```

---

## Part 3: Making It Work

### Step 1: Test Locally First

Before deploying, test locally:

1. **Start your API locally:**
   ```bash
   cd "/Users/shreyaspaul/code/AI-LLM -Schema-Generator"
   source .venv/bin/activate
   python3 app.py
   ```

2. **Test endpoints:**
   ```bash
   # Health check
   curl http://localhost:8000/health
   
   # Start job
   curl -X POST http://localhost:8000/crawl/async \
     -H "Content-Type: application/json" \
     -d '{"base_url": "https://www.webless.ai", "max_pages": 2}'
   ```

### Step 2: Test in n8n with Local API

1. In n8n workflow, set all URLs to: `http://localhost:8000`
2. Click **"Execute Workflow"**
3. Watch the execution
4. Check each node's output

### Step 3: Test with Render API

1. Update all URLs in n8n workflow to your Render URL
2. Click **"Execute Workflow"**
3. Watch progress in each node

### Step 4: Common Issues & Fixes

**Issue: "Job not found"**
- **Cause:** Job ID mismatch or server restarted
- **Fix:** Check that you're using the same job_id from Start Crawl node

**Issue: Status stuck on "running"**
- **Cause:** Loop not configured correctly
- **Fix:** Make sure Wait node connects back to Poll Status

**Issue: Can't download ZIP**
- **Cause:** Response format not set to "File"
- **Fix:** In Get ZIP node, set Response to "File"

**Issue: n8n timeout**
- **Cause:** Workflow taking too long
- **Fix:** Increase n8n execution timeout in settings

### Step 5: Share Your Workflow

1. In n8n, click **"..."** on your workflow
2. Click **"Download"** → **"JSON"**
3. Share the JSON file with others
4. They can import it in their n8n

---

## Quick Reference

### Render API URL Format
```
https://your-service-name.onrender.com
```

### n8n Node Expressions
```javascript
// Get job_id from first HTTP Request node
{{ $('HTTP Request').item.json.job_id }}

// Get status from current node
{{ $json.status }}

// Get latest progress message
{{ $json.progress[$json.progress.length - 1].message }}
```

### API Endpoints
- Health: `GET /health`
- Start: `POST /crawl/async`
- Status: `GET /crawl/status/<job_id>`
- Result: `GET /crawl/result/<job_id>`

---

**That's it!** Your setup should now work end-to-end.

