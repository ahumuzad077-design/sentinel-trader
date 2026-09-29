"""Sentinel video learning - reads YouTube transcripts, extracts trading rules."""

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


def fetch_transcript(video_id):
    if not HAS_YT:
        return None, "youtube-transcript-api not installed"
    try:
        try:
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id, languages=["en", "en-US", "en-GB"])
        except Exception:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = None
            for t in transcript_list:
                try:
                    transcript = t.fetch()
                    break
                except Exception:
                    continue
            if transcript is None:
                return None, "no usable transcript"
        text = " ".join(item["text"] for item in transcript)
        text = re.sub(r"\s+", " ", text).strip()
        return text, None
    except Exception as e:
        return None, str(e)


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
