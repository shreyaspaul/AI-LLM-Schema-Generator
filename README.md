# AI Schema Generator

Crawls websites, extracts content, and generates comprehensive JSON-LD schema.org markup using AI vision and text analysis. Perfect for Webflow sites, SEO optimization, and structured data generation.

## Features
- **AI-Powered**: Uses OpenAI vision models to understand page layout and content structure
- **Smart Extraction**: Intelligent text extraction with structured content analysis
- **Full Schema Generation**: Creates rich, accurate schema.org JSON-LD markup
- **Web API**: RESTful API for easy integration
- **Respectful Crawling**: Honors robots.txt, sitemap.xml, with rate limiting
- **Vision Mode**: Screenshot-based analysis for better accuracy

## Quick Start

1. Create and activate a virtual environment (recommended):
```bash
python3 -m venv .venv
. .venv/bin/activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set your OpenAI API key:
```bash
# Copy example env file
cp env.example .env

# Edit .env and add your API key
OPENAI_API_KEY=sk-your-key-here
```

4. Run the crawler (CLI):
```bash
python schema_crawler.py \
  --base-url https://example.com \
  --output-dir ./output \
  --max-pages 50
```

## Web API Usage

Start the API server:
```bash
python app.py
```

The API will run on `http://localhost:8000` (or PORT env var).

### Endpoints

#### `GET /health`
Health check endpoint.

Response:
```json
{
  "status": "healthy",
  "service": "AI Schema Generator",
  "timestamp": "2024-01-01T00:00:00",
  "openai_configured": true
}
```

#### `POST /crawl`
Crawl a website and generate schema markup. Returns ZIP file when complete (blocking).

**Best for:** Quick crawls, simple integrations

#### `POST /crawl/async` ⭐ **Recommended for n8n**
Start a crawl job asynchronously. Returns `job_id` immediately (202 Accepted).

**Response:**
```json
{
  "job_id": "uuid-here",
  "status": "running",
  "status_url": "/crawl/status/uuid-here",
  "result_url": "/crawl/result/uuid-here"
}
```

Then poll `/crawl/status/<job_id>` for progress and `/crawl/result/<job_id>` for ZIP.

**Best for:** n8n workflows, long-running crawls, progress tracking

#### `GET /crawl/status/<job_id>`
Get job status and progress updates.

**Response:**
```json
{
  "job_id": "...",
  "status": "running|completed|failed",
  "progress": [
    {"type": "info", "message": "...", "timestamp": "..."}
  ],
  "error": null
}
```

#### `GET /crawl/result/<job_id>`
Get the ZIP file for a completed job. Returns 202 if still running, 404 if not found.

**Request body (for `/crawl` and `/crawl/async`):**
```json
{
  "base_url": "https://example.com",
  "sitemap_url": "https://example.com/sitemap.xml",  // Optional
  "max_pages": 50,  // Optional, default 500
  "rate_limit": 1.0,  // Optional, default 0.5
  "timeout": 30,  // Optional, default 20
  "allow_subdomains": false,  // Optional
  "model": "gpt-4o",  // Optional, default: gpt-4o (vision-capable)
  "api_key": "sk-..."  // Optional, overrides env var
}
```

Response: ZIP file download containing:
- `index.json` - Master index of all pages
- `pages/*.json` - Individual page schemas
- `prompts/*.txt` - Generated prompts for auditing

Example with curl:
```bash
curl -X POST http://localhost:8000/crawl \
  -H "Content-Type: application/json" \
  -d '{"base_url": "https://example.com", "max_pages": 10}' \
  -o schema_output.zip
```

#### `POST /crawl/stream`
**Streaming endpoint with real-time progress updates** via Server-Sent Events (SSE).

Same request format as `/crawl`, but streams progress messages and returns ZIP as base64 at the end.

**Response format (SSE):**
- Progress messages: `{"type": "info|warn|error", "message": "...", "timestamp": "..."}`
- Completion: `{"type": "complete", "message": "Crawl completed"}`
- ZIP file: `{"type": "zip", "filename": "...", "data": "base64..."}`

**JavaScript Example (using fetch with streaming):**
```javascript
fetch('/crawl/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ base_url: 'https://example.com', max_pages: 10 })
})
.then(response => {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  
  function readChunk() {
    reader.read().then(({ done, value }) => {
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\\n\\n');
      buffer = lines.pop();
      
      lines.forEach(line => {
        if (line.startsWith('data: ')) {
          const data = JSON.parse(line.substring(6));
          if (data.type === 'zip') {
            // Download ZIP
            const link = document.createElement('a');
            link.href = 'data:application/zip;base64,' + data.data;
            link.download = data.filename;
            link.click();
          } else {
            console.log(`[${data.type}] ${data.message}`);
          }
        }
      });
      readChunk();
    });
  }
  readChunk();
});
```

**Or use the test page:**
Open `test_stream.html` in your browser for a ready-made UI to test the streaming endpoint.

## CLI Arguments
- `--base-url` (required): Site root to crawl
- `--sitemap-url` (optional): Override sitemap URL (auto-discovers if not provided)
- `--output-dir` (default: `./output`): Output directory for generated files
- `--max-pages` (default: 500): Maximum pages to process
- `--rate-limit` (default: 0.5): Seconds to wait between requests
- `--timeout` (default: 20): Request timeout in seconds
- `--allow-subdomains` (flag): Also crawl subdomains
- `--model` (default: `gpt-4o`): OpenAI model (default: gpt-4o with vision capabilities)
- `--api-key` (optional): Override API key
- `--config` (optional): Path to project config JSON
- `--save-outline` (flag): Save structured outline analysis files

**Note**: Vision mode, full text extraction, and prompt dumping are always enabled by default.

## Output Structure
- `output/index.json`: Array of entries with `{ url, slug, title, schema_path }`.
- `output/pages/<slug>.json`: Object containing `{ url, title, extracted_text, schema_jsonld }`.

These are designed so your MCP can map `url` or `slug` to the corresponding schema file and inject it into the matching Webflow page.

## Notes
- The crawler only follows links within the same registrable domain by default. Use `--allow-subdomains` to include subdomains.
- JavaScript-rendered sites: This tool fetches server-rendered HTML. If your site is heavily client-side rendered, consider pre-rendering or swapping fetch logic to a headless browser.
- Rate limits and robots: Respect site policies. Increase `--rate-limit` and `--max-pages` as needed.

## Docker Deployment

Build and run:
```bash
docker build -t schema-generator .
docker run -p 8000:8000 -e OPENAI_API_KEY=sk-... schema-generator
```

The API will be available at `http://localhost:8000`.

## API Key Configuration

Priority order:
1. `--api-key` CLI flag (CLI only)
2. `api_key` in POST request body (API only)
3. `OPENAI_API_KEY` environment variable
4. `schema_config.json` (project config)
5. `~/.ai_schema_generator/config.json` (user config)

## MCP Handoff
- Use `output/index.json` to iterate pages and `schema_path` for the JSON-LD payload.
- Your MCP can inject a `<script type="application/ld+json">` block with the file contents into the appropriate Webflow page.
