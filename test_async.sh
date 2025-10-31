#!/bin/bash

# Test script for async polling endpoints
# This simulates what n8n would do

API_URL="${API_URL:-http://localhost:8000}"
BASE_URL="${1:-https://www.webless.ai}"
MAX_PAGES="${2:-3}"

echo "🚀 Testing Async API Endpoints"
echo "API URL: $API_URL"
echo "Target URL: $BASE_URL"
echo "Max Pages: $MAX_PAGES"
echo ""

# Step 1: Start crawl job
echo "📤 Step 1: Starting crawl job..."
RESPONSE=$(curl -s -X POST "$API_URL/crawl/async" \
  -H "Content-Type: application/json" \
  -d "{
    \"base_url\": \"$BASE_URL\",
    \"max_pages\": $MAX_PAGES
  }")

JOB_ID=$(echo "$RESPONSE" | jq -r '.job_id // empty')
STATUS=$(echo "$RESPONSE" | jq -r '.status // empty')

if [ -z "$JOB_ID" ]; then
  echo "❌ Error: Failed to start job"
  echo "Response: $RESPONSE"
  exit 1
fi

echo "✅ Job started!"
echo "   Job ID: $JOB_ID"
echo "   Status: $STATUS"
echo ""

# Step 2: Poll for status
echo "🔄 Step 2: Polling for status (every 5 seconds)..."
echo ""

POLL_COUNT=0
MAX_POLLS=120  # 10 minutes max

while [ $POLL_COUNT -lt $MAX_POLLS ]; do
  STATUS_RESPONSE=$(curl -s "$API_URL/crawl/status/$JOB_ID")
  JOB_STATUS=$(echo "$STATUS_RESPONSE" | jq -r '.status // "unknown"')
  PROGRESS_COUNT=$(echo "$STATUS_RESPONSE" | jq -r '.progress | length // 0')
  LATEST_MSG=$(echo "$STATUS_RESPONSE" | jq -r '.progress[-1].message // "Waiting..."')
  ERROR=$(echo "$STATUS_RESPONSE" | jq -r '.error // empty')
  
  TIMESTAMP=$(date +%H:%M:%S)
  
  # Show latest progress
  if [ "$PROGRESS_COUNT" -gt 0 ]; then
    echo "[$TIMESTAMP] Status: $JOB_STATUS | Messages: $PROGRESS_COUNT | Latest: $LATEST_MSG"
  else
    echo "[$TIMESTAMP] Status: $JOB_STATUS | Waiting for progress..."
  fi
  
  # Check if completed
  if [ "$JOB_STATUS" = "completed" ]; then
    echo ""
    echo "✅ Job completed!"
    break
  elif [ "$JOB_STATUS" = "failed" ]; then
    echo ""
    echo "❌ Job failed!"
    if [ -n "$ERROR" ]; then
      echo "   Error: $ERROR"
    fi
    exit 1
  fi
  
  POLL_COUNT=$((POLL_COUNT + 1))
  sleep 5
done

if [ $POLL_COUNT -eq $MAX_POLLS ]; then
  echo ""
  echo "⏰ Timeout: Job took too long (max $MAX_POLLS polls)"
  exit 1
fi

# Step 3: Get result
echo ""
echo "📥 Step 3: Downloading result ZIP..."
ZIP_FILE="result_${JOB_ID}.zip"

curl -s "$API_URL/crawl/result/$JOB_ID" -o "$ZIP_FILE"

if [ -f "$ZIP_FILE" ] && [ -s "$ZIP_FILE" ]; then
  SIZE=$(ls -lh "$ZIP_FILE" | awk '{print $5}')
  echo "✅ ZIP downloaded: $ZIP_FILE ($SIZE)"
  echo ""
  echo "📦 ZIP contents:"
  unzip -l "$ZIP_FILE" | head -20
  echo ""
  echo "🎉 Test completed successfully!"
  echo "   Result file: $ZIP_FILE"
else
  echo "❌ Failed to download ZIP file"
  exit 1
fi

