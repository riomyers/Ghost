#!/usr/bin/env python3
"""Ghost Voice — ElevenLabs TTS with Pickle Rick voice."""

import os
import subprocess
import tempfile
from pathlib import Path

VOICE_ID = "W1KVa6RXJnYKkxs5A9lP"  # Pickle Rick (generated)
MODEL_ID = "eleven_turbo_v2_5"
API_URL = "https://api.elevenlabs.io/v1"

def _get_api_key():
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        key_file = Path.home() / ".config" / "ghost" / ".elevenlabs_key"
        if key_file.exists():
            key = key_file.read_text().strip()
    if not key:
        raise RuntimeError("No ElevenLabs API key found")
    return key


def say(text, voice_id=VOICE_ID, stream=True):
    """Speak text with Rick's voice. Streams audio for low latency."""
    import json
    import urllib.request

    api_key = _get_api_key()

    if stream:
        url = f"{API_URL}/text-to-speech/{voice_id}/stream"
    else:
        url = f"{API_URL}/text-to-speech/{voice_id}"

    payload = json.dumps({
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.85,
            "style": 0.6,
            "use_speaker_boost": True
        }
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    })

    if stream:
        # Stream to a temp file and play with aplay/ffplay
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    tmp.write(chunk)
            tmp.close()
            _play_audio(tmp.name)
        finally:
            os.unlink(tmp.name)
    else:
        with urllib.request.urlopen(req, timeout=30) as resp:
            audio_data = resp.read()
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(audio_data)
        tmp.close()
        try:
            _play_audio(tmp.name)
        finally:
            os.unlink(tmp.name)


def say_to_file(text, output_path, voice_id=VOICE_ID):
    """Generate Rick speech and save to file."""
    import json
    import urllib.request

    api_key = _get_api_key()
    url = f"{API_URL}/text-to-speech/{voice_id}"

    payload = json.dumps({
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability": 0.4,
            "similarity_boost": 0.85,
            "style": 0.6,
            "use_speaker_boost": True
        }
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    })

    with urllib.request.urlopen(req, timeout=30) as resp:
        Path(output_path).write_bytes(resp.read())


def _play_audio(filepath):
    """Play audio file through speakers."""
    # Try ffplay first (quiet, reliable), fall back to aplay via ffmpeg pipe
    try:
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
            timeout=60, check=True
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        # Convert mp3 to wav and use aplay
        import tempfile
        wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav.close()
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", filepath, "-f", "wav", wav.name],
                capture_output=True, timeout=30, check=True
            )
            subprocess.run(["aplay", wav.name], timeout=60, check=True)
        finally:
            os.unlink(wav.name)


if __name__ == "__main__":
    import sys
    text = " ".join(sys.argv[1:]) or "I'm Pickle Rick, baby!"
    say(text)
