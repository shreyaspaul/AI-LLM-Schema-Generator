#!/usr/bin/env python3
import argparse
import base64
import gc
import json
import os
import re
import time
import urllib.parse as urlparse
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup
import html as ihtml
from slugify import slugify
from dotenv import load_dotenv
from colorama import Fore, Style, init as colorama_init

# Minimal, structured logging with color
_progress_callback = None

def set_progress_callback(callback):
	"""Set a callback function to receive progress updates."""
	global _progress_callback
	_progress_callback = callback

def log_info(message: str) -> None:
	print(Fore.CYAN + "[INFO] " + Style.RESET_ALL + f"{message}")
	if _progress_callback:
		_progress_callback("info", message)


def log_warn(message: str) -> None:
	print(Fore.YELLOW + "[WARN] " + Style.RESET_ALL + f"{message}")
	if _progress_callback:
		_progress_callback("warn", message)


def log_error(message: str) -> None:
	print(Fore.RED + "[ERROR] " + Style.RESET_ALL + f"{message}")
	if _progress_callback:
		_progress_callback("error", message)


@dataclass
class PageData:
	url: str
	title: str
	extracted_text: str
	schema_jsonld: Optional[Dict] = None


USER_AGENT_DEFAULT = (
	"AI-Schema-Crawler/1.0 (+https://github.com/) "
	"Contact: webmaster@example.com"
)

CONFIG_DIR = os.path.expanduser("~/.ai_schema_generator")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PROJECT_CONFIG_FILE = "schema_config.json"


def normalize_url(base: str, link: str) -> Optional[str]:
	if not link:
		return None
	try:
		joined = urlparse.urljoin(base, link)
		parsed = urlparse.urlparse(joined)
		if not parsed.scheme.startswith("http"):
			return None
		# Drop fragments
		clean = parsed._replace(fragment="").geturl()
		return clean
	except Exception:
		return None


def same_registrable_domain(a: str, b: str) -> bool:
	try:
		pa = urlparse.urlparse(a)
		pb = urlparse.urlparse(b)
		# Basic host check without public suffix list dependency
		return pa.hostname == pb.hostname or (
			pa.hostname and pb.hostname and pa.hostname.endswith("." + pb.hostname)
		)
	except Exception:
		return False


def is_navigable_link(href: str) -> bool:
	if not href:
		return False
	if href.startswith("mailto:") or href.startswith("tel:"):
		return False
	if href.startswith("javascript:"):
		return False
	return True


def fetch_text(
	url: str,
	session: requests.Session,
	timeout: int,
	rate_limit: float,
	allowed_content_types: Optional[List[str]] = None,
) -> Optional[str]:
	try:
		resp = session.get(url, timeout=timeout)
		if rate_limit > 0:
			time.sleep(rate_limit)
		if resp.status_code >= 400:
			log_warn(f"HTTP {resp.status_code}: {url}")
			return None
		if allowed_content_types:
			content_type = resp.headers.get("content-type", "")
			if not any(t in content_type for t in allowed_content_types):
				log_warn(f"Unexpected content-type {content_type}: {url}")
				return None
		# Improve encoding handling to avoid garbled characters
		if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
			resp.encoding = resp.apparent_encoding or "utf-8"
		return resp.text
	except requests.RequestException as exc:
		log_warn(f"Request failed {url}: {exc}")
		return None


def discover_sitemaps(base_url: str, session: requests.Session, timeout: int) -> List[str]:
	candidates = [
		urlparse.urljoin(base_url, "/sitemap.xml"),
		urlparse.urljoin(base_url, "/sitemap_index.xml"),
	]
	# robots.txt discovery
	robots_url = urlparse.urljoin(base_url, "/robots.txt")
	try:
		resp = session.get(robots_url, timeout=timeout)
		if resp.status_code == 200:
			for line in resp.text.splitlines():
				if line.lower().startswith("sitemap:"):
					maybe = line.split(":", 1)[1].strip()
					candidates.append(maybe)
	except requests.RequestException:
		pass
	# De-duplicate while preserving order
	seen = set()
	unique = []
	for c in candidates:
		if c not in seen:
			seen.add(c)
			unique.append(c)
	return unique


def parse_sitemap_for_urls(sitemap_xml: str) -> List[str]:
	urls: List[str] = []
	# Light-weight extraction to avoid heavy XML parsing
	for match in re.finditer(r"<loc>\s*([^<]+?)\s*</loc>", sitemap_xml, re.IGNORECASE):
		urls.append(match.group(1).strip())
	return urls


def extract_hidden_and_faq_content(soup: BeautifulSoup) -> str:
	"""Extract text from hidden/collapsed elements and FAQ structures that might be missed."""
	extracted = []
	
	# Extract FAQ structures (dl/dt/dd)
	for dl in soup.find_all("dl"):
		faq_text = []
		dt_tags = dl.find_all("dt", recursive=False)
		dd_tags = dl.find_all("dd", recursive=False)
		for i, dt in enumerate(dt_tags):
			q = dt.get_text(" ", strip=True)
			if q:
				faq_text.append(f"Q: {q}")
			if i < len(dd_tags):
				a = dd_tags[i].get_text(" ", strip=True)
				if a:
					faq_text.append(f"A: {a}")
		if faq_text:
			extracted.append("\n".join(faq_text))
	
	# Extract from common FAQ/accordion class patterns (even if hidden)
	faq_patterns = [
		r"faq", r"accordion", r"question", r"answer", r"collapse",
		r"expandable", r"toggle", r"panel", r"item"
	]
	for pattern in faq_patterns:
		for elem in soup.find_all(class_=re.compile(pattern, re.I)):
			text = elem.get_text(" ", strip=True)
			if text and len(text) > 10:  # Ignore very short matches
				extracted.append(text)
	
	# Extract from data attributes commonly used for hidden content
	for elem in soup.find_all(attrs={"data-content": True}):
		text = elem.get("data-content", "").strip()
		if text:
			extracted.append(text)
	
	# Extract from aria-hidden="false" elements (accessible but might be visually hidden)
	for elem in soup.find_all(attrs={"aria-hidden": "false"}):
		text = elem.get_text(" ", strip=True)
		if text:
			extracted.append(text)
	
	# Extract from elements with common hidden content patterns
	# Look for divs/spans with FAQ-like content even if they have height:0 styling
	# We check the HTML directly for these patterns before CSS filtering
	for elem in soup.find_all(["div", "section", "article"]):
		# Check for common FAQ/question/answer text patterns in class/id
		elem_id = elem.get("id", "").lower()
		elem_class = " ".join(elem.get("class", [])).lower()
		if any(term in elem_id or term in elem_class for term in ["faq", "question", "answer", "q-and-a"]):
			text = elem.get_text(" ", strip=True)
			if text and len(text) > 20:  # Only meaningful content
				extracted.append(text)
	
	return "\n\n".join(extracted)


def extract_visible_text_full(html: str, url: str) -> Tuple[str, str]:
	"""Return (title, full_text) from the entire page (excluding scripts/styles).
	
	This function extracts ALL text content, including hidden/collapsed content
	like FAQ answers in accordions (height:0 divs, etc.)
	"""
	# Prefer lxml parser for better structure
	soup = BeautifulSoup(html, "lxml")
	title_tag = soup.find("title")
	title = title_tag.get_text(strip=True) if title_tag else url
	# Remove only script/style tags; keep structural elements so we capture full copy
	for tag in soup(["script", "style", "noscript"]):
		tag.decompose()
	
	# Extract main text (this gets everything in the DOM regardless of CSS)
	text = soup.get_text("\n", strip=True)
	
	# Also explicitly extract hidden/FAQ content that might be missed
	hidden_content = extract_hidden_and_faq_content(soup)
	if hidden_content:
		# Append hidden content if not already in main text (avoid duplicates)
		# We check if key phrases from hidden content are missing from main text
		hidden_lines = [line.strip() for line in hidden_content.split("\n") if len(line.strip()) > 30]
		for line in hidden_lines[:10]:  # Check first 10 lines to avoid performance issues
			if line.lower() not in text.lower():
				text += "\n\n" + hidden_content
				break
	
	# Normalize entities and whitespace, collapse 3+ newlines to 2
	text = ihtml.unescape(text)
	text = re.sub(r"\r\n?", "\n", text)
	text = re.sub(r"\n{3,}", "\n\n", text)
	return title[:280], text[:2500000]


# Removed extract_visible_text_smart - we only use extract_visible_text_full now
# because it's more comprehensive and foolproof (extracts ALL content including hidden/collapsed elements)


def build_structured_outline(html: str) -> Dict:
	"""Produce a structured outline from the DOM: meta, headings, and sectionized text.

	The goal is to give the LLM a higher-signal, well-structured view of the page.
	"""
	soup = BeautifulSoup(html, "lxml")

	# Meta tags
	meta: Dict[str, str] = {}
	mtitle = soup.find("title")
	if mtitle:
		meta["title"] = mtitle.get_text(strip=True)
	for name in ["description", "keywords"]:
		tag = soup.find("meta", attrs={"name": name})
		if tag and tag.get("content"):
			meta[name] = tag["content"].strip()
	for prop in [
		"og:title", "og:description", "og:type", "og:url", "og:image",
		"twitter:title", "twitter:description", "twitter:image",
	]:
		tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
		if tag and tag.get("content"):
			meta[prop] = tag["content"].strip()

	# Headings and sectionization

	def block_text(node) -> str:
		# Convert common block elements to readable text with preserved structure
		# - Paragraphs: as-is
		# - Lists: prefix with "- "
		# - Tables: pipe-delimited rows
		# - Images: include alt text caption
		# - Code/pre: keep text
		# - FAQ structures (dl/dt/dd): Q/A format
		if not getattr(node, "name", None):
			return str(node).strip()
		name = node.name.lower()
		if name in ["script", "style", "noscript"]:
			return ""
		if name == "dl":
			# FAQ structure: extract Q&A pairs
			faq_items = []
			dt_tags = node.find_all("dt", recursive=False)
			dd_tags = node.find_all("dd", recursive=False)
			for i, dt in enumerate(dt_tags):
				q = dt.get_text(" ", strip=True)
				if q:
					faq_items.append(f"Q: {q}")
				if i < len(dd_tags):
					a = dd_tags[i].get_text(" ", strip=True)
					if a:
						faq_items.append(f"A: {a}")
			return "\n".join(faq_items) if faq_items else ""
		if name in ["dt", "dd"]:
			# Extract directly (will be handled by parent dl)
			return node.get_text(" ", strip=True)
		if name in ["p", "blockquote", "pre", "code"]:
			text = node.get_text(" ", strip=True)
			return text
		if name in ["ul", "ol"]:
			items = []
			for li in node.find_all("li", recursive=False):
				items.append("- " + li.get_text(" ", strip=True))
			return "\n".join(items)
		if name == "table":
			rows = []
			for tr in node.find_all("tr"):
				cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
				if cells:
					rows.append(" | ".join(cells))
			return "\n".join(rows)
		if name == "img":
			alt = node.get("alt") or ""
			return f"[image: {alt}]" if alt else ""
		# Generic container: concatenate child blocks
		parts: List[str] = []
		for child in node.children:
			ct = block_text(child)
			if ct:
				parts.append(ct)
		return "\n".join(parts)
	def heading_level(tag_name: str) -> int:
		try:
			return int(tag_name[1]) if tag_name and tag_name.startswith("h") else 7
		except Exception:
			return 7

	headings = []
	for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
		headings.append({
			"tag": h.name,
			"level": heading_level(h.name),
			"text": h.get_text(strip=True),
		})

	sections: List[Dict] = []

	# Capture preface content before the first heading
	first_heading = soup.find(["h1", "h2", "h3", "h4", "h5", "h6"])
	preface_texts: List[str] = []
	if first_heading:
		for sib in first_heading.previous_siblings:
			bt = block_text(sib)
			if bt:
				preface_texts.append(bt)
		preface_texts.reverse()
		preface = "\n".join([t for t in preface_texts if t.strip()])
		if preface.strip():
			sections.append({"heading": "Intro", "level": 0, "text": preface})

	# Build sections by collecting siblings until next heading of same or higher level
	for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
		level = heading_level(h.name)
		texts: List[str] = []
		for sib in h.next_siblings:
			if getattr(sib, "name", None) in ["h1", "h2", "h3", "h4", "h5", "h6"] and heading_level(sib.name) <= level:
				break
			bt = block_text(sib)
			if bt:
				texts.append(bt)
		sections.append({
			"heading": h.get_text(strip=True),
			"level": level,
			"text": "\n".join([t for t in texts if t.strip()]),
		})

	# Capture trailing content after the last heading
	last_heading = None
	for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
		last_heading = h
	if last_heading:
		trail_texts: List[str] = []
		for sib in last_heading.next_siblings:
			bt = block_text(sib)
			if bt:
				trail_texts.append(bt)
		trail = "\n".join([t for t in trail_texts if t.strip()])
		if trail.strip():
			sections.append({"heading": "Outro", "level": 7, "text": trail})

	return {
		"meta": meta,
		"headings": headings,
		"sections": sections,
	}


def iterate_links(html: str, base_url: str) -> List[str]:
	soup = BeautifulSoup(html, "html5lib")
	links: List[str] = []
	for a in soup.find_all("a"):
		href = a.get("href")
		if not is_navigable_link(href):
			continue
		norm = normalize_url(base_url, href)
		if norm:
			links.append(norm)
	return links


def infer_page_type_from_url(url: str) -> Dict[str, str]:
	"""Infer likely page type from URL patterns. Returns hints for the LLM."""
	hints = {}
	url_lower = url.lower()
	path = urlparse.urlparse(url).path.lower()
	
	if any(p in path for p in ["/blog/", "/article/", "/post/", "/news/", "/story/"]):
		hints["likely_type"] = "Article"
		hints["reason"] = "URL suggests blog/article section"
	elif any(p in path for p in ["/product/", "/products/", "/p/"]):
		hints["likely_type"] = "Product"
		hints["reason"] = "URL suggests product page"
	elif any(p in path for p in ["/service/", "/services/"]):
		hints["likely_type"] = "Service"
		hints["reason"] = "URL suggests service page"
	elif any(p in path for p in ["/faq", "/help/", "/questions/"]):
		hints["likely_type"] = "FAQPage"
		hints["reason"] = "URL suggests FAQ page"
	elif any(p in path for p in ["/about", "/company", "/team", "/contact"]):
		hints["likely_type"] = "AboutPage or WebPage"
		hints["reason"] = "URL suggests informational/company page"
	elif path == "/" or path == "":
		hints["likely_type"] = "WebPage (Homepage)"
		hints["reason"] = "Homepage - likely marketing/landing page"
	else:
		hints["likely_type"] = "WebPage (default)"
		hints["reason"] = "URL pattern suggests informational/marketing page (not Article)"
	
	return hints


def capture_screenshot(url: str, timeout: int = 30) -> Optional[str]:
	"""Capture a screenshot of the page using Playwright and return as base64 string.
	
	Optimized for memory: reduced viewport size, JPEG compression, immediate cleanup.
	
	Works in cloud environments when:
	- Browser binaries are installed: playwright install chromium --with-deps
	- Required system dependencies are present (libnss3, libatk, etc.)
	- DISPLAY is not needed (headless mode)
	
	For Docker/cloud deployment, install browser during image build:
	RUN playwright install chromium --with-deps
	"""
	try:
		from playwright.sync_api import sync_playwright
		
		with sync_playwright() as p:
			# Cloud-friendly launch args for containerized environments
			browser = p.chromium.launch(
				headless=True,
				args=[
					"--no-sandbox",
					"--disable-setuid-sandbox",
					"--disable-dev-shm-usage",
					"--disable-accelerated-2d-canvas",
					"--no-first-run",
					"--no-zygote",
					"--disable-gpu",
					"--memory-pressure-off"  # Prevent aggressive memory usage
				]
			)
			# Reduced viewport to save memory (1280x720 instead of 1920x1080)
			context = browser.new_context(
				viewport={"width": 1280, "height": 720},
				user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
			)
			page = context.new_page()
			page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
			# Reduced wait time to save memory
			page.wait_for_timeout(1000)
			# Use JPEG with quality=75 instead of PNG to reduce memory (smaller file size)
			screenshot_bytes = page.screenshot(full_page=True, type="jpeg", quality=75)
			page.close()
			context.close()
			browser.close()
			
			# Convert to base64
			screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
			return screenshot_b64
	except ImportError:
		log_warn("Playwright not installed. Install with: pip install playwright && playwright install chromium --with-deps")
		return None
	except Exception as exc:
		log_warn(f"Screenshot capture failed for {url}: {exc}")
		return None


def safe_slug_from_url(url: str) -> str:
	parsed = urlparse.urlparse(url)
	path = parsed.path.strip("/") or "home"
	# Include host to avoid collisions across subpaths if needed
	candidate = f"{parsed.hostname or 'site'}-{path}"
	slug = slugify(candidate, max_length=120)
	return slug or "page"


def ensure_dir(path: str) -> None:
	os.makedirs(path, exist_ok=True)


def call_openai_schema(
	model: str,
	api_key: str,
	page_title: str,
	page_url: str,
	extracted_text: str,
) -> Dict:
	"""Call OpenAI to generate detailed JSON-LD schema for the page."""
	from openai import OpenAI

	client = OpenAI(api_key=api_key)
	system = (
		"You are a structured data expert. Produce STRICTLY VALID schema.org JSON-LD based ONLY on the provided page content. "
		"Rules: 1) DO NOT INVENT values. If unknown, OMIT. 2) Use only schema.org properties valid for the selected @type(s). "
		"3) Do NOT include ratings, reviewCount, aggregateRating, offers, prices, or similar unless explicit numeric values are present. 4) Output MUST be a single JSON object suitable for <script type=\\\"application/ld+json\\\"> with @context and @type. "
		"5) NEVER include debug fields (e.g., tag, level, headings, evidence) or any non-schema keys."
	)
	user = (
		"Page URL: "
		+ page_url
		+ "\nPage Title: "
		+ page_title
		+ "\nExtracted Text:\n"
		+ extracted_text
	)

	resp = client.chat.completions.create(
		model=model,
		messages=[
			{"role": "system", "content": system},
			{"role": "user", "content": user},
		],
		response_format={"type": "json_object"},
		temperature=0.2,
	)
	content = resp.choices[0].message.content
	try:
		return json.loads(content)
	except json.JSONDecodeError:
		# As a fallback, wrap raw string
		return {"@context": "https://schema.org", "@type": "WebPage", "name": page_title, "url": page_url}


def crawl(
	base_url: str,
	sitemap_url: Optional[str],
	output_dir: str,
	max_pages: int,
	rate_limit: float,
	user_agent: Optional[str],
	allow_subdomains: bool,
	timeout: int,
	skip_llm: bool,
	model: str,
	api_key: str,
	dump_prompts: bool = False,
	no_truncate: bool = False,
	save_outline: bool = False,
	use_vision: bool = False,
	progress_callback: Optional[callable] = None,
) -> None:
	# Set global progress callback
	if progress_callback:
		set_progress_callback(progress_callback)
	ensure_dir(output_dir)
	pages_dir = os.path.join(output_dir, "pages")
	ensure_dir(pages_dir)
	prompts_dir = os.path.join(output_dir, "prompts")
	# Always create prompts_dir (dump_prompts is always enabled)
	ensure_dir(prompts_dir)
	analysis_dir = os.path.join(output_dir, "analysis")
	if save_outline:
		ensure_dir(analysis_dir)

	print(Fore.MAGENTA + "\n" + "═" * 60 + Style.RESET_ALL)
	print(Fore.MAGENTA + "  AI Schema Crawler" + Style.RESET_ALL)
	print(Fore.MAGENTA + "═" * 60 + Style.RESET_ALL)
	log_info(f"Base: {Fore.WHITE}{base_url}{Style.RESET_ALL}")
	log_info(f"Max pages: {Fore.WHITE}{max_pages}{Style.RESET_ALL}  Rate: {Fore.WHITE}{rate_limit}s{Style.RESET_ALL}")
	print("")

	session = requests.Session()
	session.headers.update({"User-Agent": user_agent or USER_AGENT_DEFAULT})

	seed_urls: List[str] = []
	if sitemap_url:
		print(Fore.BLUE + "Sitemap: " + Style.RESET_ALL + f"{sitemap_url}")
		text = fetch_text(
			sitemap_url, session, timeout, rate_limit,
			allowed_content_types=["application/xml", "text/xml", "application/rss+xml", "text/plain"]
		)
		if text:
			if "<sitemapindex" in text:
				child_maps = parse_sitemap_for_urls(text)
				log_info(Fore.WHITE + f"Found sitemap index with {len(child_maps)} child sitemaps" + Style.RESET_ALL)
				for child in child_maps:
					ctext = fetch_text(
						child, session, timeout, rate_limit,
						allowed_content_types=["application/xml", "text/xml", "application/rss+xml", "text/plain"]
					)
					if ctext and "<urlset" in ctext:
						seed_urls.extend(parse_sitemap_for_urls(ctext))
			elif "<urlset" in text:
				urls = parse_sitemap_for_urls(text)
				log_info(Fore.WHITE + f"Found {len(urls)} URLs in sitemap" + Style.RESET_ALL)
				seed_urls.extend(urls)
	else:
		maps = discover_sitemaps(base_url, session, timeout)
		if maps:
			log_info(Fore.WHITE + f"Discovered {len(maps)} sitemap candidate(s)" + Style.RESET_ALL)
		for sm in maps:
			print(Fore.BLUE + "Sitemap: " + Style.RESET_ALL + f"{sm}")
			text = fetch_text(
				sm, session, timeout, rate_limit,
				allowed_content_types=["application/xml", "text/xml", "application/rss+xml", "text/plain"]
			)
			if not text:
				continue
			if "<sitemapindex" in text:
				child_maps = parse_sitemap_for_urls(text)
				log_info(Fore.WHITE + f"Found sitemap index with {len(child_maps)} child sitemaps" + Style.RESET_ALL)
				for child in child_maps:
					ctext = fetch_text(
						child, session, timeout, rate_limit,
						allowed_content_types=["application/xml", "text/xml", "application/rss+xml", "text/plain"]
					)
					if ctext and "<urlset" in ctext:
						seed_urls.extend(parse_sitemap_for_urls(ctext))
			elif "<urlset" in text:
				urls = parse_sitemap_for_urls(text)
				log_info(Fore.WHITE + f"Found {len(urls)} URLs in sitemap" + Style.RESET_ALL)
				seed_urls.extend(urls)

	if not seed_urls:
		log_warn("No sitemap URLs found; falling back to base URL crawl")
		seed_urls = [base_url]

	log_info(f"Seed queue size: {Fore.WHITE}{len(seed_urls)}{Style.RESET_ALL}")

	visited: Set[str] = set()
	queue: deque[str] = deque()
	for u in seed_urls:
		queue.append(u)

	origin = base_url
	origin_host = urlparse.urlparse(origin).hostname or ""

	index_entries: List[Dict] = []
	count = 0
	
	# Initialize manifest file path and dictionary - we'll write incrementally
	manifest_path = os.path.join(output_dir, "manifest.v1.json")
	manifest: Dict[str, Dict[str, Any]] = {}  # Will store {"/path": {schema}}

	if not api_key:
		log_error("OPENAI_API_KEY not set. Please set it via --api-key, .env file, or config.")
		log_error("Schema generation requires an API key. Exiting.")
		return

	while queue and count < max_pages:
		url = queue.popleft()
		if url in visited:
			continue
		visited.add(url)

		if not allow_subdomains and not same_registrable_domain(url, origin):
			continue

		# Fetch only HTML pages during crawl
		html = fetch_text(url, session, timeout, rate_limit, allowed_content_types=["text/html"])
		if not html:
			continue

		# Always use full extraction - it's more comprehensive and captures ALL content
		# including hidden/collapsed elements like FAQ answers in accordions
		title, text = extract_visible_text_full(html, url)

		# Build structured outline for better LLM grounding
		outline = build_structured_outline(html)
		
		# Save HTML reference for link extraction before we clear it
		html_for_links = html

		# Compute slug early for prompt dump path
		page_slug = safe_slug_from_url(url)
		
		# Capture screenshot if vision mode is enabled
		screenshot_b64 = None
		if use_vision and not skip_llm:
			log_info(f"Capturing screenshot for {url}...")
			screenshot_b64 = capture_screenshot(url, timeout)
			if screenshot_b64:
				# Calculate approximate file size (base64 is ~1.33x larger than binary)
				approx_size_kb = round(len(screenshot_b64) * 3 / 4 / 1024, 1)
				log_info(f"Screenshot captured ({len(screenshot_b64):,} chars base64, ~{approx_size_kb} KB PNG)")
			else:
				log_warn(f"Screenshot capture failed for {url}, continuing without vision")
		
		try:
			# Build the exact prompt that will be sent - comprehensive instruction for rich schema
			system = (
				"You are an expert schema.org structured data analyst. Your task is to generate comprehensive, accurate, "
				"and machine-readable JSON-LD markup that enables LLMs and search engines to deeply understand the page content.\n\n"
				"ANALYSIS PROCESS:\n"
				"1. Examine the Structured Outline to understand page structure, sections, and content hierarchy.\n"
				"2. CRITICAL: Determine page type using these strict rules:\n"
				"   - Article: ONLY if page has datePublished, author (Person), and is clearly a blog post/news article. "
				"     URL patterns like /blog/, /article/, /news/ suggest Article. Marketing pages are NOT articles.\n"
				"   - Product/Service: If page describes a specific product or service with features, pricing, or offers.\n"
				"   - WebPage: DEFAULT for marketing pages, landing pages, informational pages, company pages. "
				"     Use WebPage unless page clearly fits another type with strong indicators.\n"
				"   - FAQPage: Only if page has explicit Q&A format (questions and answers clearly paired).\n"
				"   - HowTo: Only if page contains step-by-step instructions with numbered steps.\n"
				"3. For mainEntity: Use Article ONLY if ALL of: datePublished exists, author exists (Person), "
				"and URL suggests blog/article. Otherwise, use appropriate type (Service, Product, WebPage, etc.) "
				"or omit mainEntity and describe content directly in WebPage properties.\n"
				"4. Extract all relevant entities and relationships (Organization, Person, Product, Service, etc.)\n"
				"5. Identify structured content: FAQs, HowTo steps, breadcrumbs, reviews/testimonials, "
				"features/benefits, pricing/offers (if explicit), contact information, social profiles.\n\n"
				"SCHEMA REQUIREMENTS:\n"
				"- ALWAYS include @context and @type. Use WebPage as base, add mainEntity for primary content.\n"
				"- Extract Organization details: name, url, logo (from meta og:image if available), description, "
				"contactPoint (email, phone), address (if present), sameAs (social links if mentioned).\n"
				"- For product/service pages: extract name, description, featureList, brand, category.\n"
				"- For article/blog pages: extract headline, description, author (if mentioned), datePublished, "
				"publisher (Organization), keywords, articleSection.\n"
				"- Include BreadcrumbList if navigation structure is clear from headings/sections.\n"
				"- Extract FAQPage schema if Q&A format or question-answer patterns are detected.\n"
				"- Include HowTo if step-by-step instructions or processes are described.\n"
				"- Add aggregateRating/reviewCount ONLY if explicit numeric ratings or review counts are mentioned.\n"
				"- Include offers/price ONLY if specific prices or offers are explicitly stated.\n"
				"- Extract testimonials/reviews as Review objects with author, reviewBody, ratingValue if present.\n"
				"- Use speakable property for key content snippets if appropriate.\n"
				"- Include potentialAction (e.g., RequestQuoteAction, ContactAction) if call-to-action buttons are mentioned.\n\n"
				"ACCURACY RULES:\n"
				"- NEVER invent data. Only extract what is explicitly stated in the content.\n"
				"- Use null or omit properties if information is not available.\n"
				"- Extract dates, prices, ratings, counts only when explicit numeric/text values are present.\n"
				"- Validate all property names against schema.org vocabulary.\n"
				"- Ensure proper nesting: mainEntity, author, publisher should be complete objects with @type.\n"
				"- Do NOT include debug/metadata fields (tag, level, headings, evidence, etc.)\n"
				"- DO NOT include extracted text, raw text, or any non-schema.org fields in your output\n"
				"- DO NOT include 'extracted_text', 'extractedText', 'rawText', 'content', or similar fields\n"
				"- Only return valid schema.org JSON-LD markup properties\n\n"
				"OUTPUT FORMAT:\n"
				"- Single JSON object with @context=\"https://schema.org\"\n"
				"- Rich nested structure with mainEntity and related entities\n"
				"- All text values should be clean, trimmed strings\n"
				"- Arrays for lists (sameAs, keywords, featureList, etc.)\n"
				"- Proper URL format for all url properties\n"
				"- ONLY schema.org properties - no custom fields, no extracted text, no metadata\n\n"
				"Your goal is to create schema markup so comprehensive and accurate that another LLM reading only the JSON-LD "
				"could reconstruct a detailed understanding of the page content, entities, relationships, and key information."
			)
			# Smart truncation: Estimate tokens and keep under limits
			# Rough estimate: ~4 chars = 1 token for English text
			# We need to leave room for: system prompt (~1000), outline (~3000), user prompt text (~2000), response (~2000)
			# Target: ~25000 tokens total (leaving buffer under 30k limit)
			max_chars_for_text = 80000 if no_truncate else 60000  # ~15k tokens for text
			max_sections = None if no_truncate else 30
			
			# Smart truncation: prioritize important content
			if len(text) > max_chars_for_text and not no_truncate:
				# Try to preserve FAQ content and main sections
				text_lower = text.lower()
				
				# Find FAQ sections (Q:, A:, FAQ, etc.)
				faq_markers = ["q:", "a:", "faq", "question", "answer", "q&a"]
				faq_indices = []
				for marker in faq_markers:
					idx = text_lower.find(marker)
					if idx != -1:
						faq_indices.append((idx, idx + 500))  # Assume ~500 chars per FAQ item
				
				# Prioritize: beginning of text + FAQ sections
				if faq_indices:
					# Keep first 40k chars (usually main content) + FAQ sections
					keep_chars = min(40000, max_chars_for_text - 10000)  # Reserve space for FAQs
					text_start = text[:keep_chars]
					
					# Append FAQ sections that aren't already included
					faq_content = []
					for start_idx, end_idx in sorted(faq_indices):
						if start_idx > keep_chars:  # FAQ is after the cutoff
							faq_section = text[start_idx:min(end_idx, len(text))]
							if faq_section.strip() and len(faq_section) < 5000:  # Reasonable size
								faq_content.append(faq_section)
					
					# Combine: start + FAQs (up to limit)
					remaining_chars = max_chars_for_text - len(text_start)
					if faq_content:
						faq_text = "\n\n".join(faq_content[:remaining_chars // 500])  # Approx
						if len(text_start) + len(faq_text) <= max_chars_for_text:
							text_for_llm = text_start + "\n\n[FAQ Content from later in page]\n\n" + faq_text
						else:
							text_for_llm = text_start
					else:
						text_for_llm = text_start
				else:
					# No FAQs found, just truncate from beginning (most important content is usually at top)
					text_for_llm = text[:max_chars_for_text]
			else:
				text_for_llm = text
			
			if outline.get("sections") and max_sections:
				outline_for_llm = {**outline, "sections": outline["sections"][:max_sections]}
			else:
				outline_for_llm = outline
			
			# Build comprehensive user prompt with clear instructions
			meta_info = outline_for_llm.get("meta", {})
			url_hints = infer_page_type_from_url(url)
			user_parts = [
				"=== PAGE INFORMATION ===",
				f"URL: {url}",
				f"Title: {title}",
				f"\nURL Analysis Hint: {url_hints.get('likely_type', 'Unknown')} - {url_hints.get('reason', 'No specific pattern detected')}",
				"NOTE: Use this hint as guidance, but verify against actual content. Do NOT classify as Article unless "
				"the page has datePublished and author information, even if URL suggests blog.",
			]
			
			# Add meta tags if available
			if meta_info:
				user_parts.append("\n=== META INFORMATION ===")
				if meta_info.get("description"):
					user_parts.append(f"Description: {meta_info['description']}")
				if meta_info.get("og:description"):
					user_parts.append(f"OG Description: {meta_info['og:description']}")
				if meta_info.get("og:image"):
					user_parts.append(f"OG Image (potential logo): {meta_info['og:image']}")
				if meta_info.get("keywords"):
					user_parts.append(f"Keywords: {meta_info['keywords']}")
			
			user_parts.append("\n=== STRUCTURED CONTENT OUTLINE ===")
			user_parts.append("Analyze this outline carefully. The 'sections' array contains the page content organized by headings. ")
			user_parts.append("Each section has a heading, level (hierarchy), and associated text content.")
			user_parts.append("Use this structure to identify entities, relationships, FAQs, HowTo steps, features, testimonials, etc.")
			user_parts.append("\n" + json.dumps(outline_for_llm, ensure_ascii=False, indent=2))
			
			# Always add full extracted text (we always use full extraction mode now)
			text_status = "complete" if len(text_for_llm) >= len(text) else f"truncated to {len(text_for_llm):,} chars (of {len(text):,} total)"
			user_parts.append(f"\n=== FULL EXTRACTED TEXT ({text_status}) ===")
			user_parts.append("Use this full text to verify details and extract any information missing from the outline above.")
			if len(text_for_llm) < len(text):
				user_parts.append("⚠️ NOTE: Text has been truncated. Use the screenshot (if provided) to extract additional details that may be missing from this truncated text, including FAQ answers, contact information, features, or any content visible in the screenshot.")
			user_parts.append(text_for_llm)
			
			user_parts.append("\n=== YOUR TASK ===")
			user_parts.append("Based on the structured outline and content above, generate comprehensive schema.org JSON-LD markup.")
			user_parts.append("Extract ALL relevant entities (Organization, Product, Service, Person, etc.), relationships, and structured data.")
			user_parts.append("Be thorough: include breadcrumbs, FAQs, features, testimonials, contact info, social links, etc. when present.")
			user_parts.append("Remember: accuracy is critical—only include data explicitly present in the content.")
			user_parts.append("CRITICAL: Your output must ONLY contain valid schema.org JSON-LD properties. DO NOT include 'extracted_text', 'extractedText', 'rawText', 'content', or any other non-schema.org fields. Only return the schema markup.")
			
			user = "\n".join(user_parts)

			# Dump the prompt for auditing (must happen before API call)
			if dump_prompts:
				try:
					prompt_path = os.path.join(prompts_dir, f"{page_slug}.txt")
					with open(prompt_path, "w", encoding="utf-8") as pf:
						pf.write("SYSTEM:\n" + system + "\n\n")
						pf.write("USER:\n" + user + "\n")
						if screenshot_b64:
							pf.write(f"\n[NOTE: Screenshot was also included ({len(screenshot_b64):,} chars base64)]\n")
							pf.write("[The actual API call included the screenshot as an image_url in the content array]\n")
					log_info(f"Saved prompt to {prompt_path}")
				except Exception as exc:
					log_warn(f"Failed to save prompt dump: {exc}")

			# Generate schema using the comprehensive prompt we built
			if not skip_llm:
				from openai import OpenAI
				client = OpenAI(api_key=api_key)
				try:
					# Build messages array - include image if screenshot is available
					messages = [{"role": "system", "content": system}]
					
					if screenshot_b64:
						# Use vision-capable model (fallback to gpt-4o if model doesn't support vision)
						vision_model = "gpt-4o" if model not in ["gpt-4o", "gpt-4-vision-preview"] else model
						if vision_model != model:
							log_info(f"Using vision model {vision_model} instead of {model}")
						
						vision_instruction = "\n\nIMPORTANT: Analyze the screenshot above to:"
						vision_instruction += "\n1. Better understand the page layout, visual hierarchy, and content structure"
						vision_instruction += "\n2. Extract any details missing from the truncated text (read text directly from the screenshot)"
						vision_instruction += "\n3. Identify all FAQs, contact info, features, and key content visible in the image"
						vision_instruction += "\n4. Use visual context to improve schema accuracy, especially for page type classification"
						vision_instruction += "\n5. The screenshot shows the FULL page - use it to fill gaps from text truncation"
						
						messages.append({
							"role": "user",
							"content": [
								{"type": "text", "text": user + vision_instruction},
								{
									"type": "image_url",
									"image_url": {
										"url": f"data:image/png;base64,{screenshot_b64}"
									}
								}
							]
						})
						actual_model = vision_model
					else:
						messages.append({"role": "user", "content": user})
						actual_model = model
					
					# Retry logic for token limit errors
					max_retries = 2
					retry_count = 0
					current_text = text_for_llm
					current_outline = outline_for_llm
					
					while retry_count <= max_retries:
						try:
							resp = client.chat.completions.create(
								model=actual_model,
								messages=messages,
								response_format={"type": "json_object"},
								temperature=0.2,
							)
							content = resp.choices[0].message.content
							page_schema = json.loads(content)
							break
						except Exception as api_error:
							error_str = str(api_error)
							# Check if it's a token limit error
							if "429" in error_str and ("token" in error_str.lower() or "TPM" in error_str or "rate_limit" in error_str.lower()):
								retry_count += 1
								if retry_count > max_retries:
									log_warn(f"Token limit exceeded after {max_retries} retries. Using aggressive truncation.")
									# Last resort: aggressive truncation
									current_text = text[:20000]  # ~5k tokens
									if outline.get("sections"):
										current_outline = {**outline, "sections": outline["sections"][:15]}
									else:
										current_outline = outline
									
									# Rebuild user message with truncated content
									user_parts_trunc = user_parts[:-3]  # Remove old text parts
									user_parts_trunc.append(f"\n=== FULL EXTRACTED TEXT (heavily truncated to {len(current_text):,} chars due to token limits) ===")
									user_parts_trunc.append("Use this truncated text to verify details. Original text was too large for API.")
									user_parts_trunc.append(current_text)
									user_parts_trunc.append("\n=== YOUR TASK ===")
									user_parts_trunc.append("Based on the structured outline and content above, generate comprehensive schema.org JSON-LD markup.")
									user_parts_trunc.append("Extract ALL relevant entities (Organization, Product, Service, Person, etc.), relationships, and structured data.")
									user_parts_trunc.append("CRITICAL: Your output must ONLY contain valid schema.org JSON-LD properties. DO NOT include 'extracted_text', 'extractedText', 'rawText', 'content', or any other non-schema.org fields.")
									user_trunc = "\n".join(user_parts_trunc)
									
									if screenshot_b64:
										vision_inst = "\n\nCRITICAL: Text is heavily truncated. Use the screenshot to extract ALL missing content including FAQs, contact info, features, and any text visible in the image."
										messages = [
											{"role": "system", "content": system},
											{
												"role": "user",
												"content": [
													{"type": "text", "text": user_trunc + vision_inst},
													{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}}
												]
											}
										]
									else:
										messages = [{"role": "system", "content": system}, {"role": "user", "content": user_trunc}]
									
									# Final retry with aggressive truncation
									resp = client.chat.completions.create(
										model=actual_model,
										messages=messages,
										response_format={"type": "json_object"},
										temperature=0.2,
									)
									content = resp.choices[0].message.content
									page_schema = json.loads(content)
									log_warn(f"Successfully generated schema with aggressive truncation after token limit error")
									break
								else:
									# Progressive truncation on retry
									log_warn(f"Token limit exceeded (attempt {retry_count}/{max_retries}). Truncating content and retrying...")
									current_text = current_text[:int(len(current_text) * 0.7)]  # Reduce by 30%
									if current_outline.get("sections"):
										current_outline = {**current_outline, "sections": current_outline["sections"][:int(len(current_outline["sections"]) * 0.7)]}
									
									# Rebuild user message with truncated content
									# Find where to insert the new truncated text (before the old text section)
									text_start_idx = None
									for i, part in enumerate(user_parts):
										if "=== FULL EXTRACTED TEXT" in part:
											text_start_idx = i
											break
									
									if text_start_idx is not None:
										# Rebuild: keep everything before text section, add new truncated text
										user_parts_retry = user_parts[:text_start_idx]
										# Update the outline in the STRUCTURED CONTENT OUTLINE section
										outline_idx = None
										for i, part in enumerate(user_parts_retry):
											if "=== STRUCTURED CONTENT OUTLINE ===" in part:
												outline_idx = i + 1  # Next line after header
												break
										if outline_idx and outline_idx < len(user_parts_retry):
											# Replace outline JSON
											user_parts_retry[outline_idx] = "\n" + json.dumps(current_outline, ensure_ascii=False, indent=2)
										
										# Add new truncated text section
										user_parts_retry.append(f"\n=== FULL EXTRACTED TEXT (truncated to {len(current_text):,} chars after token limit error) ===")
										user_parts_retry.append("Use this text to verify details and extract information.")
										user_parts_retry.append(current_text)
										user_parts_retry.append("\n=== YOUR TASK ===")
										user_parts_retry.append("Based on the structured outline and content above, generate comprehensive schema.org JSON-LD markup.")
										user_parts_retry.append("Extract ALL relevant entities (Organization, Product, Service, Person, etc.), relationships, and structured data.")
										user_parts_retry.append("CRITICAL: Your output must ONLY contain valid schema.org JSON-LD properties. DO NOT include 'extracted_text', 'extractedText', 'rawText', 'content', or any other non-schema.org fields.")
									else:
										# Fallback: just rebuild from scratch
										user_parts_retry = user_parts[:-3]
										user_parts_retry.append(f"\n=== FULL EXTRACTED TEXT (truncated to {len(current_text):,} chars after token limit error) ===")
										user_parts_retry.append("Use this text to verify details and extract information.")
										user_parts_retry.append(current_text)
										user_parts_retry.append("\n=== YOUR TASK ===")
										user_parts_retry.append("Based on the structured outline and content above, generate comprehensive schema.org JSON-LD markup.")
										user_parts_retry.append("Extract ALL relevant entities (Organization, Product, Service, Person, etc.), relationships, and structured data.")
										user_parts_retry.append("CRITICAL: Your output must ONLY contain valid schema.org JSON-LD properties. DO NOT include 'extracted_text', 'extractedText', 'rawText', 'content', or any other non-schema.org fields.")
									
									user_retry = "\n".join(user_parts_retry)
									
									if screenshot_b64:
										vision_inst = "\n\nNOTE: Text was truncated due to token limits. Use the screenshot to read and extract any missing content, especially FAQs, contact details, or features visible in the image."
										messages = [
											{"role": "system", "content": system},
											{
												"role": "user",
												"content": [
													{"type": "text", "text": user_retry + vision_inst},
													{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}}
												]
											}
										]
									else:
										messages = [{"role": "system", "content": system}, {"role": "user", "content": user_retry}]
							else:
								# Not a token limit error, re-raise
								raise api_error
				except json.JSONDecodeError:
					log_warn(f"Failed to parse LLM JSON response for {url}, using fallback")
					page_schema = {"@context": "https://schema.org", "@type": "WebPage", "name": title, "url": url}
			else:
				page_schema = {
					"@context": "https://schema.org",
					"@type": "WebPage",
					"name": title,
					"url": url,
				}
		except Exception as exc:
			log_error(f"LLM error for {url}: {exc}")
			page_schema = {
				"@context": "https://schema.org",
				"@type": "WebPage",
				"name": title,
				"url": url,
			}

		# Clean schema: Remove any non-schema.org fields that LLM might have added
		non_schema_fields = ["extracted_text", "extractedText", "rawText", "content", "raw_text", "full_text", 
		                      "outline", "sections", "headings", "tag", "level", "evidence", "metadata"]
		if isinstance(page_schema, dict):
			for field in non_schema_fields:
				if field in page_schema:
					del page_schema[field]
			# Recursively clean nested objects
			def clean_dict(d):
				if isinstance(d, dict):
					return {k: clean_dict(v) for k, v in d.items() if k not in non_schema_fields}
				elif isinstance(d, list):
					return [clean_dict(item) for item in d]
				return d
			page_schema = clean_dict(page_schema)

		# Optionally persist outline separately for audit; do not embed in page JSON
		if save_outline:
			with open(os.path.join(analysis_dir, f"{page_slug}.outline.json"), "w", encoding="utf-8") as of:
				json.dump(outline, of, ensure_ascii=False, indent=2)

		page_path = os.path.join(pages_dir, f"{page_slug}.json")
		with open(page_path, "w", encoding="utf-8") as f:
			json.dump(
				{
					"url": url,
					"title": title,
					"schema_jsonld": page_schema,
				},
				f,
				ensure_ascii=False,
				indent=2,
			)

		# Extract path from URL for manifest key
		# IMPORTANT: Use the exact URL as it appears in the queue (original crawled URL)
		# Normalize trailing slashes for consistency with Webflow injection script
		parsed_url = urlparse.urlparse(url)
		# Get the full path including all segments
		path = parsed_url.path or "/"
		# Ensure path starts with / for consistency
		if not path.startswith("/"):
			path = "/" + path
		# Normalize trailing slashes: remove trailing slashes except for root "/"
		# This ensures consistent matching with the Webflow injection script
		if path != "/" and path.endswith("/"):
			path = path.rstrip("/")
		
		# Debug logging to verify path extraction (can be removed after testing)
		log_info(f"Extracted path '{path}' from URL: {url}")
		
		# Store minimal index entry (no longer storing schema here, it's in manifest)
		index_entries.append(
			{"url": url, "slug": page_slug, "title": title, "path": path}
		)
		
		# Add to manifest (key is path, value is schema)
		manifest[path] = page_schema
		
		# Write manifest incrementally after each page to save memory
		with open(manifest_path, "w", encoding="utf-8") as f:
			json.dump(manifest, f, ensure_ascii=False, indent=2)
		
		# Delete individual page JSON file immediately to free memory
		try:
			if os.path.exists(page_path):
				os.remove(page_path)
		except Exception as e:
			log_warn(f"Could not delete {page_path}: {e}")
		
		count += 1
		log_info(f"✓ [{count}/{max_pages}] Saved: {url} -> {path}")

		# Enqueue links for BFS if we started from base (extract links before clearing)
		for link in iterate_links(html_for_links, url):
			if link not in visited and (
				allow_subdomains or (urlparse.urlparse(link).hostname == origin_host)
			):
				queue.append(link)
		
		# Clear large variables to free memory (after processing and link extraction)
		html = None
		html_for_links = None
		text = None
		screenshot_b64 = None
		outline = None
		page_schema = None
		
		# Force garbage collection after each page
		gc.collect()

	# Final manifest write (already written incrementally, but ensure it's complete)
	with open(manifest_path, "w", encoding="utf-8") as f:
		json.dump(manifest, f, ensure_ascii=False, indent=2)
	
	# Also create a .txt copy for Webflow (Webflow doesn't allow .json uploads)
	manifest_txt_path = manifest_path.replace(".json", ".txt")
	with open(manifest_txt_path, "w", encoding="utf-8") as f:
		json.dump(manifest, f, ensure_ascii=False, indent=2)
	
	log_info("─" * 60)
	log_info(f"Wrote manifest with {len(manifest)} entries: {manifest_path}")
	log_info(f"Also created .txt copy for Webflow: {manifest_txt_path}")
	
	# Final cleanup - clear manifest from memory
	manifest.clear()
	gc.collect()


def main() -> None:
	parser = argparse.ArgumentParser(description="Crawl site and generate JSON-LD schemas per page.")
	parser.add_argument("--base-url", required=True, help="Root URL to crawl")
	parser.add_argument("--sitemap-url", help="Optional sitemap URL override")
	parser.add_argument("--output-dir", default="./output", help="Directory for outputs")
	parser.add_argument("--max-pages", type=int, default=500, help="Max pages to process")
	parser.add_argument("--rate-limit", type=float, default=0.5, help="Seconds to sleep between requests")
	parser.add_argument("--user-agent", help="Custom User-Agent header")
	parser.add_argument("--allow-subdomains", action="store_true", help="Also crawl subdomains")
	parser.add_argument("--timeout", type=int, default=20, help="Per-request timeout in seconds")
	parser.add_argument("--model", default="gpt-4o", help="OpenAI model for schema generation (default: gpt-4o with vision capabilities)")
	parser.add_argument("--api-key", help="OpenAI API key override (will take precedence)")
	parser.add_argument("--config", help="Path to project config JSON (default: schema_config.json)")
	parser.add_argument("--save-outline", action="store_true", help="Save structured outline to output/analysis/<slug>.outline.json (not embedded in page JSON)")
	args = parser.parse_args()

	# Init color output and load .env if present
	colorama_init(autoreset=True)
	load_dotenv()

	def read_json(path: str) -> Dict:
		try:
			with open(path, "r", encoding="utf-8") as f:
				return json.load(f)
		except Exception:
			return {}

	# Resolve config precedence for API key and model
	project_cfg_path = args.config or PROJECT_CONFIG_FILE
	project_cfg = read_json(project_cfg_path)
	user_cfg = read_json(CONFIG_FILE)

	api_key = (
		args.api_key
		or os.environ.get("OPENAI_API_KEY")
		or project_cfg.get("openai_api_key")
		or user_cfg.get("openai_api_key")
		or ""
	)

	# Allow config to set default model if user didn't override
	model = args.model or project_cfg.get("model") or user_cfg.get("model") or "gpt-4o"

	# Default behaviors (always enabled):
	# - use_vision: Always use screenshot + vision model
	# - no_truncate: Always send full extracted text
	# - Text extraction: Always use full extraction (captures ALL content including hidden/collapsed elements)
	# - skip_llm: Always false (always generate schema)
	# - dump_prompts: Always save prompts to output/prompts/

	crawl(
		base_url=args.base_url,
		sitemap_url=args.sitemap_url,
		output_dir=args.output_dir,
		max_pages=args.max_pages,
		rate_limit=args.rate_limit,
		user_agent=args.user_agent,
		allow_subdomains=args.allow_subdomains,
		timeout=args.timeout,
		skip_llm=False,  # Always generate schema
		model=model,
		api_key=api_key,
		dump_prompts=True,  # Always save prompts
		no_truncate=True,  # Always send full text
		use_vision=True,  # Always use vision
	)


if __name__ == "__main__":
	main()
