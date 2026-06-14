#!/usr/bin/env python3.11
"""Send a prompt to Gemini API with grounded search and show full response details."""
import json
import os
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_api_key():
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    return os.environ.get("GOOGLE_API_KEY")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Send prompt to Gemini API (grounded search)")
    parser.add_argument("prompt", nargs="?", help="Prompt text (or pipe via stdin)")
    parser.add_argument("-m", "--model", default="gemini-2.5-flash")
    parser.add_argument("--no-search", action="store_true", help="Disable google_search grounding")
    parser.add_argument("--raw", action="store_true", help="Print raw JSON response")
    args = parser.parse_args()

    prompt = args.prompt or sys.stdin.read().strip()
    if not prompt:
        parser.print_help()
        sys.exit(1)

    api_key = get_api_key()
    if not api_key:
        print("ERROR: No API key (set GOOGLE_API_KEY or GEMINI_API_KEY)")
        sys.exit(1)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{args.model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    if not args.no_search:
        payload["tools"] = [{"google_search": {}}]

    print(f"Model: {args.model}")
    print(f"Search: {'disabled' if args.no_search else 'enabled'}")
    print(f"Prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}")
    print(f"{'='*60}")

    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        headers = dict(resp.headers)
        data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"\n❌ HTTP {e.code}: {e.reason}")
        body = e.read().decode()
        print(body[:500])
        sys.exit(1)

    if args.raw:
        print(json.dumps(data, indent=2))
        sys.exit(0)

    # Response headers
    print(f"\nHTTP Headers:")
    for k in ["x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "retry-after"]:
        if k in headers:
            print(f"  {k}: {headers[k]}")

    # Candidate
    candidate = data.get("candidates", [{}])[0]
    finish = candidate.get("finishReason", "?")
    print(f"\nFinish reason: {finish}")

    # Text
    text = ""
    for part in candidate.get("content", {}).get("parts", []):
        if "text" in part:
            text += part["text"]
    print(f"\nResponse ({len(text)} chars):")
    print(f"{'─'*60}")
    print(text)
    print(f"{'─'*60}")

    # Grounding metadata
    gm = candidate.get("groundingMetadata", {})
    if gm:
        queries = gm.get("webSearchQueries", [])
        chunks = gm.get("groundingChunks", [])
        supports = gm.get("groundingSupports", [])
        print(f"\nGrounding:")
        print(f"  Search queries: {queries}")
        print(f"  Sources: {len(chunks)}")
        for c in chunks[:5]:
            w = c.get("web", {})
            print(f"    - {w.get('title', '?')}: {w.get('uri', '?')[:80]}")
        if supports:
            scores = []
            for s in supports:
                cs = s.get("confidenceScores", [])
                scores.extend(cs)
            if scores:
                print(f"  Confidence scores: min={min(scores):.2f} avg={sum(scores)/len(scores):.2f} max={max(scores):.2f}")
        if gm.get("searchEntryPoint"):
            print(f"  Search entry point: present")
    else:
        print(f"\n⚠️  No grounding metadata (search did not trigger)")

    # Usage
    usage = data.get("usageMetadata", {})
    if usage:
        print(f"\nTokens: prompt={usage.get('promptTokenCount', '?')} response={usage.get('candidatesTokenCount', '?')} total={usage.get('totalTokenCount', '?')}")


if __name__ == "__main__":
    main()
