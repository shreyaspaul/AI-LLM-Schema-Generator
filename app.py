#!/usr/bin/env python3
"""
Web API wrapper for AI Schema Generator
Exposes HTTP endpoints for crawling websites and generating schema markup
"""
import os
import shutil
import tempfile
import zipfile
import json
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, Response, stream_with_context
import base64
from flask_cors import CORS
from dotenv import load_dotenv

from schema_crawler import crawl

# In-memory job storage (use Redis/database in production)
jobs = {}

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Get API key from environment
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


@app.route("/health", methods=["GET"])
def health():
	"""Health check endpoint"""
	return jsonify({
		"status": "healthy",
		"service": "AI Schema Generator",
		"timestamp": datetime.utcnow().isoformat(),
		"openai_configured": bool(OPENAI_API_KEY)
	}), 200


@app.route("/crawl/stream", methods=["POST"])
def crawl_stream_endpoint():
	"""
	Streaming crawl endpoint with real-time progress updates via Server-Sent Events (SSE).
	
	Same request format as /crawl, but streams progress updates and returns ZIP at the end.
	Use EventSource or SSE client to receive updates.
	"""
	if not request.is_json:
		return jsonify({"error": "Request must be JSON"}), 400
	
	data = request.get_json()
	
	if not data.get("base_url"):
		return jsonify({"error": "base_url is required"}), 400
	
	# Get parameters
	base_url = data["base_url"]
	sitemap_url = data.get("sitemap_url")
	max_pages = data.get("max_pages", 500)
	rate_limit = data.get("rate_limit", 0.5)
	timeout = data.get("timeout", 20)
	allow_subdomains = data.get("allow_subdomains", False)
	model = data.get("model", "gpt-4o-mini")
	api_key = data.get("api_key") or OPENAI_API_KEY
	
	if not api_key:
		return jsonify({
			"error": "OpenAI API key is required. Provide via api_key in request or OPENAI_API_KEY env var."
		}), 400
	
	def generate():
		"""Generator function for SSE streaming"""
		progress_queue = queue.Queue()
		crawl_error = None
		zip_path = None
		temp_dir = None
		
		def progress_callback(level, message):
			"""Send progress updates to queue"""
			progress_queue.put({"type": level, "message": message, "timestamp": datetime.utcnow().isoformat()})
		
		def run_crawl():
			"""Run crawler in background thread"""
			nonlocal crawl_error, zip_path, temp_dir
			try:
				temp_dir = tempfile.mkdtemp(prefix="schema_gen_", suffix=f"_{int(datetime.utcnow().timestamp())}")
				output_dir = os.path.join(temp_dir, "output")
				
				# Run crawler with progress callback
				crawl(
					base_url=base_url,
					sitemap_url=sitemap_url,
					output_dir=output_dir,
					max_pages=max_pages,
					rate_limit=rate_limit,
					user_agent=None,
					allow_subdomains=allow_subdomains,
					timeout=timeout,
					skip_llm=False,
					model=model,
					api_key=api_key,
					dump_prompts=True,
					no_truncate=True,
					extract_mode="smart",
					use_vision=True,
					progress_callback=progress_callback,
				)
				
				# Create ZIP file
				zip_path = os.path.join(temp_dir, "schema_output.zip")
				with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
					for root, dirs, files in os.walk(output_dir):
						for file in files:
							file_path = os.path.join(root, file)
							arc_name = os.path.relpath(file_path, output_dir)
							zipf.write(file_path, arc_name)
				
				progress_queue.put({"type": "complete", "message": "Crawl completed", "zip_path": zip_path})
			except Exception as exc:
				crawl_error = str(exc)
				progress_queue.put({"type": "error", "message": f"Crawler failed: {crawl_error}"})
		
		# Start crawler in background thread
		crawl_thread = threading.Thread(target=run_crawl, daemon=True)
		crawl_thread.start()
		
		# Stream progress updates
		while True:
			try:
				update = progress_queue.get(timeout=1)
				
				if update["type"] == "complete":
					# Send completion and ZIP file
					yield f"data: {json.dumps(update)}\n\n"
					
					# Read ZIP file and send as base64
					with open(update["zip_path"], "rb") as f:
						zip_b64 = base64.b64encode(f.read()).decode("utf-8")
						timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
						safe_domain = base_url.replace("https://", "").replace("http://", "").replace("/", "_")[:50]
						filename = f"schema_{safe_domain}_{timestamp}.zip"
						
						yield f"data: {json.dumps({'type': 'zip', 'filename': filename, 'data': zip_b64})}\n\n"
					
					# Cleanup
					if temp_dir and os.path.exists(temp_dir):
						shutil.rmtree(temp_dir, ignore_errors=True)
					break
				
				elif update["type"] == "error":
					yield f"data: {json.dumps(update)}\n\n"
					if temp_dir and os.path.exists(temp_dir):
						shutil.rmtree(temp_dir, ignore_errors=True)
					break
				
				else:
					# Regular progress update
					yield f"data: {json.dumps(update)}\n\n"
					
			except queue.Empty:
				# Check if thread is still alive
				if not crawl_thread.is_alive():
					if crawl_error:
						yield f"data: {json.dumps({'type': 'error', 'message': crawl_error})}\n\n"
					break
				# Send heartbeat
				yield f": heartbeat\n\n"
	
	return Response(stream_with_context(generate()), mimetype="text/event-stream", headers={
		"Cache-Control": "no-cache",
		"Connection": "keep-alive",
		"X-Accel-Buffering": "no"
	})


@app.route("/crawl", methods=["POST"])
def crawl_endpoint():
	"""
	Crawl a website and generate schema markup.
	
	Request body (JSON):
	{
		"base_url": "https://example.com",  # Required
		"sitemap_url": "https://example.com/sitemap.xml",  # Optional
		"max_pages": 50,  # Optional, default 500
		"rate_limit": 1.0,  # Optional, default 0.5
		"timeout": 30,  # Optional, default 20
		"allow_subdomains": false,  # Optional, default false
		"model": "gpt-4o-mini",  # Optional
		"api_key": "sk-..."  # Optional, overrides env var
	}
	
	Returns: ZIP file with all generated schema files
	"""
	# Validate request
	if not request.is_json:
		return jsonify({"error": "Request must be JSON"}), 400
	
	data = request.get_json()
	
	if not data.get("base_url"):
		return jsonify({"error": "base_url is required"}), 400
	
	# Get parameters with defaults
	base_url = data["base_url"]
	sitemap_url = data.get("sitemap_url")
	max_pages = data.get("max_pages", 500)
	rate_limit = data.get("rate_limit", 0.5)
	timeout = data.get("timeout", 20)
	allow_subdomains = data.get("allow_subdomains", False)
	model = data.get("model", "gpt-4o-mini")
	api_key = data.get("api_key") or OPENAI_API_KEY
	
	if not api_key:
		return jsonify({
			"error": "OpenAI API key is required. Provide via api_key in request or OPENAI_API_KEY env var."
		}), 400
	
	# Create temporary directory for this job
	temp_dir = None
	try:
		temp_dir = tempfile.mkdtemp(prefix="schema_gen_", suffix=f"_{int(datetime.utcnow().timestamp())}")
		output_dir = os.path.join(temp_dir, "output")
		
		# Run crawler
		try:
			crawl(
				base_url=base_url,
				sitemap_url=sitemap_url,
				output_dir=output_dir,
				max_pages=max_pages,
				rate_limit=rate_limit,
				user_agent=None,
				allow_subdomains=allow_subdomains,
				timeout=timeout,
				skip_llm=False,
				model=model,
				api_key=api_key,
				dump_prompts=True,
				no_truncate=True,
				extract_mode="smart",
				use_vision=True,
			)
		except Exception as exc:
			return jsonify({
				"error": f"Crawler failed: {str(exc)}"
			}), 500
		
		# Check if output was generated
		if not os.path.exists(output_dir) or not os.listdir(output_dir):
			return jsonify({
				"error": "No output generated. Check logs for details."
			}), 500
		
		# Create ZIP file
		zip_path = os.path.join(temp_dir, "schema_output.zip")
		with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
			# Add all files from output directory
			for root, dirs, files in os.walk(output_dir):
				for file in files:
					file_path = os.path.join(root, file)
					arc_name = os.path.relpath(file_path, output_dir)
					zipf.write(file_path, arc_name)
		
		# Generate filename with timestamp
		timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
		safe_domain = base_url.replace("https://", "").replace("http://", "").replace("/", "_")[:50]
		filename = f"schema_{safe_domain}_{timestamp}.zip"
		
		# Send ZIP file
		return send_file(
			zip_path,
			mimetype="application/zip",
			as_attachment=True,
			download_name=filename
		)
		
	except Exception as exc:
		return jsonify({
			"error": f"Server error: {str(exc)}"
		}), 500
	
	finally:
		# Cleanup temp directory after response is sent
		# Note: Flask sends file asynchronously, so we schedule cleanup
		if temp_dir and os.path.exists(temp_dir):
			try:
				# Use a background task or schedule cleanup
				# For now, we'll clean up immediately after send
				# In production, consider using background tasks
				pass  # Temp dir will be cleaned by OS eventually, or use cleanup task
			except Exception:
				pass


@app.errorhandler(404)
def not_found(error):
	return jsonify({"error": "Endpoint not found"}), 404


@app.route("/crawl/async", methods=["POST"])
def crawl_async_endpoint():
	"""
	Start a crawl job asynchronously. Returns job_id immediately.
	Use /crawl/status/<job_id> to poll for progress and /crawl/result/<job_id> to get ZIP.
	
	Perfect for n8n workflows that need to poll for completion.
	"""
	if not request.is_json:
		return jsonify({"error": "Request must be JSON"}), 400
	
	data = request.get_json()
	
	if not data.get("base_url"):
		return jsonify({"error": "base_url is required"}), 400
	
	# Get parameters
	base_url = data["base_url"]
	api_key = data.get("api_key") or OPENAI_API_KEY
	
	if not api_key:
		return jsonify({
			"error": "OpenAI API key is required. Provide via api_key in request or OPENAI_API_KEY env var."
		}), 400
	
	# Create job
	job_id = str(uuid.uuid4())
	jobs[job_id] = {
		"status": "running",
		"progress": [],
		"base_url": base_url,
		"created_at": datetime.utcnow().isoformat(),
		"error": None,
		"zip_path": None,
		"filename": None,
	}
	
	def run_crawl_job():
		"""Run crawler in background"""
		progress_list = []
		
		def progress_callback(level, message):
			"""Collect progress updates"""
			progress_list.append({
				"type": level,
				"message": message,
				"timestamp": datetime.utcnow().isoformat()
			})
			jobs[job_id]["progress"] = progress_list
		
		temp_dir = None
		try:
			temp_dir = tempfile.mkdtemp(prefix="schema_gen_", suffix=f"_{int(datetime.utcnow().timestamp())}")
			output_dir = os.path.join(temp_dir, "output")
			
			# Run crawler
			crawl(
				base_url=data["base_url"],
				sitemap_url=data.get("sitemap_url"),
				output_dir=output_dir,
				max_pages=data.get("max_pages", 500),
				rate_limit=data.get("rate_limit", 0.5),
				user_agent=None,
				allow_subdomains=data.get("allow_subdomains", False),
				timeout=data.get("timeout", 20),
				skip_llm=False,
				model=data.get("model", "gpt-4o-mini"),
				api_key=api_key,
				dump_prompts=True,
				no_truncate=True,
				extract_mode="smart",
				use_vision=True,
				progress_callback=progress_callback,
			)
			
			# Create ZIP
			zip_path = os.path.join(temp_dir, "schema_output.zip")
			with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
				for root, dirs, files in os.walk(output_dir):
					for file in files:
						file_path = os.path.join(root, file)
						arc_name = os.path.relpath(file_path, output_dir)
						zipf.write(file_path, arc_name)
			
			timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
			safe_domain = base_url.replace("https://", "").replace("http://", "").replace("/", "_")[:50]
			filename = f"schema_{safe_domain}_{timestamp}.zip"
			
			jobs[job_id]["status"] = "completed"
			jobs[job_id]["zip_path"] = zip_path
			jobs[job_id]["filename"] = filename
			jobs[job_id]["temp_dir"] = temp_dir
			
		except Exception as exc:
			jobs[job_id]["status"] = "failed"
			jobs[job_id]["error"] = str(exc)
			if temp_dir and os.path.exists(temp_dir):
				shutil.rmtree(temp_dir, ignore_errors=True)
	
	# Start job in background
	thread = threading.Thread(target=run_crawl_job, daemon=True)
	thread.start()
	
	return jsonify({
		"job_id": job_id,
		"status": "running",
		"message": "Crawl job started",
		"status_url": f"/crawl/status/{job_id}",
		"result_url": f"/crawl/result/{job_id}"
	}), 202


@app.route("/crawl/status/<job_id>", methods=["GET"])
def crawl_status_endpoint(job_id):
	"""
	Get status and progress of a crawl job.
	
	Returns:
	- status: "running" | "completed" | "failed"
	- progress: Array of progress messages
	- error: Error message if failed
	"""
	if job_id not in jobs:
		return jsonify({"error": "Job not found"}), 404
	
	job = jobs[job_id]
	
	return jsonify({
		"job_id": job_id,
		"status": job["status"],
		"progress": job["progress"],
		"error": job.get("error"),
		"created_at": job["created_at"],
		"completed_at": datetime.utcnow().isoformat() if job["status"] in ["completed", "failed"] else None
	}), 200


@app.route("/crawl/result/<job_id>", methods=["GET"])
def crawl_result_endpoint(job_id):
	"""
	Get the ZIP file result for a completed job.
	
	Returns ZIP file if job is completed, 404 if not found, 202 if still running.
	"""
	if job_id not in jobs:
		return jsonify({"error": "Job not found"}), 404
	
	job = jobs[job_id]
	
	if job["status"] == "running":
		return jsonify({
			"error": "Job is still running",
			"status": "running",
			"status_url": f"/crawl/status/{job_id}"
		}), 202
	
	if job["status"] == "failed":
		return jsonify({
			"error": job.get("error", "Job failed"),
			"status": "failed"
		}), 500
	
	if not job.get("zip_path") or not os.path.exists(job["zip_path"]):
		return jsonify({"error": "Result file not found"}), 404
	
	# Send ZIP file
	return send_file(
		job["zip_path"],
		mimetype="application/zip",
		as_attachment=True,
		download_name=job["filename"]
	)


@app.errorhandler(404)
def not_found(error):
	return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
	return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
	port = int(os.environ.get("PORT", 8000))
	debug = os.environ.get("DEBUG", "false").lower() == "true"
	
	app.run(host="0.0.0.0", port=port, debug=debug)

