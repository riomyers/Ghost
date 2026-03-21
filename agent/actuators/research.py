#!/usr/bin/env python3
"""Web Research Actuator — search the web and summarize findings.

Uses DuckDuckGo HTML (no API key, zero cost) for search,
urllib for fetching pages, Nexus for summarization.
"""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import json
import urllib.request
import urllib.parse
import re
from html.parser import HTMLParser
import nexus_client
import database


class DDGParser(HTMLParser):
    """Parse DuckDuckGo lite HTML results."""
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_result = False
        self._in_snippet = False
        self._current = {}
        self._text = ''

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'a' and 'result-link' in attrs_dict.get('class', ''):
            self._in_result = True
            self._current = {'url': attrs_dict.get('href', ''), 'title': '', 'snippet': ''}
            self._text = ''
        if tag == 'td' and 'result-snippet' in attrs_dict.get('class', ''):
            self._in_snippet = True
            self._text = ''

    def handle_endtag(self, tag):
        if tag == 'a' and self._in_result:
            self._current['title'] = self._text.strip()
            self._in_result = False
        if tag == 'td' and self._in_snippet:
            self._current['snippet'] = self._text.strip()
            self._in_snippet = False
            if self._current.get('url') and self._current.get('title'):
                self.results.append(self._current)
            self._current = {}

    def handle_data(self, data):
        if self._in_result or self._in_snippet:
            self._text += data


def search_ddg(query, num_results=5):
    """Search DuckDuckGo lite and return results."""
    url = 'https://lite.duckduckgo.com/lite/'
    data = urllib.parse.urlencode({'q': query}).encode()
    req = urllib.request.Request(url, data=data, headers={
        'User-Agent': 'Ghost/1.0 (Autonomous Agent)',
        'Content-Type': 'application/x-www-form-urlencoded'
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return [], f'Search error: {e}'

    parser = DDGParser()
    parser.feed(html)

    return parser.results[:num_results], None


def fetch_page_text(url, max_chars=3000):
    """Fetch a URL and extract readable text."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Ghost/1.0 (Autonomous Agent)'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='replace')

        # Strip HTML tags for readable text
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return text[:max_chars]
    except Exception as e:
        return f'(fetch error: {e})'


def research(query):
    """Main entry point — search, fetch top results, summarize with Nexus.

    Returns a structured summary string.
    """
    if not query:
        return 'ERROR: empty query'

    # Search
    results, err = search_ddg(query, num_results=5)
    if err:
        return f'Search failed: {err}'
    if not results:
        return f'No results found for: {query}'

    # Fetch top 3 pages
    sources = []
    for r in results[:3]:
        text = fetch_page_text(r['url'])
        sources.append({
            'title': r['title'],
            'url': r['url'],
            'snippet': r['snippet'],
            'content': text[:2000]
        })

    # Build summary prompt
    sources_text = ''
    for i, s in enumerate(sources, 1):
        sources_text += f"\n--- Source {i}: {s['title']} ---\nURL: {s['url']}\nSnippet: {s['snippet']}\nContent: {s['content'][:1500]}\n"

    prompt = f"""You are Ghost, an autonomous research agent. Summarize the following search results for the query: "{query}"

{sources_text}

Provide a concise summary (3-5 bullet points) of the key findings. Include which sources were most relevant.
Format: bullet points, no headers, cite URLs inline."""

    try:
        summary, model, provider = nexus_client.chat(prompt, model='haiku', timeout=45)
        database.record_token_usage('nexus', 1)
    except Exception as e:
        # Fallback: return raw snippets
        summary = f'Nexus unavailable. Raw results for "{query}":\n'
        for s in sources:
            summary += f'- {s["title"]}: {s["snippet"]}\n  {s["url"]}\n'

    # Log the research
    database.log_action('act', f'research: {query[:80]} -> {len(sources)} sources, summarized')

    result = f'Research: {query}\n\n{summary}\n\nSources: {", ".join(s["url"] for s in sources)}'
    return result


if __name__ == '__main__':
    database.init_db()
    if len(sys.argv) > 1:
        query = ' '.join(sys.argv[1:])
        print(research(query))
    else:
        print('Usage: research.py <query>')
