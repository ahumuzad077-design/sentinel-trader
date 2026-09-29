"""Sentinel video learning - reads YouTube transcripts, extracts trading rules.

Compatible with youtube-transcript-api versions:
- 0.6.x (older: get_transcript, list_transcripts)
- 1.x (newer: YouTubeTranscriptApi().fetch())
"""

import re
import json

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    HAS_YT = True
except ImportError:
    HAS_YT = False


def extract_video_id(url):
    m = re.search(
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|"
        r"youtube\.com/embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


def _snippets_to_text(snippets):
    """Convert various snippet formats to plain text."""
    parts = []
    for item in snippets:
        if isinstance(item, dict):
            parts.append(item.get("text", ""))
        elif hasattr(item, "text"):
            parts.append(item.text)
        else:
            parts.append(str(item))
    text = " ".join(parts)
    return re.sub(r"\s+", " ", text).strip()


def fetch_transcript(video_id):
    """Try every known API surface. Return (text, error)."""
    if not HAS_YT:
        return None, "youtube-transcript-api not installed"

    # ---- API 1.x: instance method ----
    try:
        api = YouTubeTranscriptApi()
        if hasattr(api, "fetch"):
            try:
                result = api.fetch(video_id,
                                     languages=["en", "en-US", "en-GB"])
            except Exception:
                result = api.fetch(video_id)

            # FetchedTranscript object — has .snippets or is iterable
            if hasattr(result, "snippets"):
                snippets = result.snippets
            elif hasattr(result, "to_raw_data"):
                snippets = result.to_raw_data()
            else:
                snippets = list(result)
            text = _snippets_to_text(snippets)
            if text:
                return text, None
    except Exception:
        pass

    # ---- API 0.6.x: class method get_transcript ----
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        try:
            data = YouTubeTranscriptApi.get_transcript(
                video_id, languages=["en", "en-US", "en-GB"])
            text = _snippets_to_text(data)
            if text:
                return text, None
        except Exception as e:
            err1 = str(e)
        else:
            err1 = None
        try:
            data = YouTubeTranscriptApi.get_transcript(video_id)
            text = _snippets_to_text(data)
            if text:
                return text, None
        except Exception as e:
            err2 = str(e)
        return None, f"{err1 or err2}"

    # ---- API 0.6.x: list_transcripts fallback ----
    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            for t in transcript_list:
                try:
                    data = t.fetch()
                    text = _snippets_to_text(data)
                    if text:
                        return text, None
                except Exception:
                    continue
            return None, "no fetchable transcript found"
        except Exception as e:
            return None, str(e)

    return None, "no compatible youtube-transcript-api method found"


def chunk_text(text, max_chars=12000):
    if len(text) <= max_chars:
        return [text]
    chunks = []
    words = text.split()
    current, size = [], 0
    for w in words:
        current.append(w)
        size += len(w) + 1
        if size >= max_chars:
            chunks.append(" ".join(current))
            current, size = [], 0
    if current:
        chunks.append(" ".join(current))
    return chunks


EXTRACT_PROMPT = """You are analyzing a YouTube transcript for a trading bot.

Extract 3-7 concrete, actionable trading rules from the content.

Format each rule as a short instruction:
- "Only enter when X and Y are both true"
- "Avoid trading during Z"
- "Prefer setups where A exceeds B"
- "Exit when C happens"

Requirements:
- Be SPECIFIC and OPERATIONAL
- Each rule must be checkable as a condition
- Skip mindset, motivation, general advice
- Skip anything about "believing in yourself" or "discipline"
- If no actionable rules exist, return empty list

Output ONLY valid JSON:
{"lessons": [{"category": "setup|risk|timing|management|avoid", "rule": "..."}]}

Transcript:
---
{transcript}
---"""


def extract_lessons(transcript_text, llm_call):
    chunks = chunk_text(transcript_text)
    all_lessons = []
    seen = set()
    for chunk in chunks:
        user = EXTRACT_PROMPT.replace("{transcript}", chunk)
        try:
            reply = llm_call(
                "You extract structured trading rules from text.", user)
            reply = re.sub(r"```json|```", "", reply).strip()
            data = json.loads(reply)
            for lesson in data.get("lessons", []):
                cat = lesson.get("category", "general")
                rule = lesson.get("rule", "").strip()
                if rule and rule not in seen:
                    seen.add(rule)
                    all_lessons.append({"category": cat, "rule": rule})
        except Exception:
            continue
    return all_lessons
