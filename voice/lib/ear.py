#!/usr/bin/env python3
"""Ghost Ear — faster-whisper STT for local speech recognition."""

import os
import sys
import tempfile
import subprocess
import numpy as np
from pathlib import Path

MODEL_SIZE = "base.en"  # Good balance of speed/accuracy for 2012 MacBook
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 500  # RMS threshold for silence detection
SILENCE_DURATION = 1.5  # Seconds of silence to stop recording
MAX_RECORD_SECONDS = 30

_model = None

def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(
            MODEL_SIZE,
            device="cpu",
            compute_type="int8"
        )
    return _model


def listen(timeout=MAX_RECORD_SECONDS, silence_sec=SILENCE_DURATION):
    """Record from mic until silence, return transcribed text."""
    audio_data = _record_until_silence(timeout, silence_sec)
    if audio_data is None or len(audio_data) < SAMPLE_RATE:  # Less than 1 sec
        return ""

    # Convert to float32 normalized for whisper
    audio_float = audio_data.astype(np.float32) / 32768.0

    model = _get_model()
    segments, info = model.transcribe(audio_float, beam_size=3, language="en")
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text


def listen_from_file(filepath):
    """Transcribe an audio file."""
    model = _get_model()
    segments, info = model.transcribe(filepath, beam_size=3, language="en")
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text


def _record_until_silence(timeout, silence_sec):
    """Record from default mic until silence detected."""
    import sounddevice as sd

    chunk_duration = 0.1  # 100ms chunks
    chunk_samples = int(SAMPLE_RATE * chunk_duration)
    silence_chunks = int(silence_sec / chunk_duration)

    recorded = []
    silent_count = 0
    has_speech = False
    total_chunks = int(timeout / chunk_duration)

    print("🎙️  Listening...", file=sys.stderr)

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16',
                           blocksize=chunk_samples) as stream:
            for _ in range(total_chunks):
                chunk, _ = stream.read(chunk_samples)
                chunk_flat = chunk.flatten()
                recorded.append(chunk_flat)

                rms = np.sqrt(np.mean(chunk_flat.astype(np.float64) ** 2))

                if rms > SILENCE_THRESHOLD:
                    has_speech = True
                    silent_count = 0
                else:
                    silent_count += 1

                if has_speech and silent_count >= silence_chunks:
                    break
    except Exception as e:
        print(f"Recording error: {e}", file=sys.stderr)
        return None

    if not has_speech:
        return None

    print("✅ Processing...", file=sys.stderr)
    return np.concatenate(recorded)


def record_to_file(filepath, duration=5):
    """Record fixed duration to a WAV file."""
    import sounddevice as sd
    import wave

    audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype='int16')
    sd.wait()

    with wave.open(filepath, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())


if __name__ == "__main__":
    text = listen()
    if text:
        print(f"Heard: {text}")
    else:
        print("Nothing detected.")
