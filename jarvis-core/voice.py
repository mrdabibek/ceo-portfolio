"""WINA :: voice.py :: ovoz moduli (TTS + mikrofon + STT).

Windows 10/11, Python 3.11+. Uchinchi-tomon kutubxonalar ixtiyoriy:
pyttsx3 / sounddevice bo'lmasa funksiyalar graceful qaytadi.
Faqat stdlib majburiy. Logger: "wina.voice".
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import tempfile
import wave
from pathlib import Path
from threading import Lock
from urllib.parse import quote
from urllib.request import Request, urlopen

__all__ = [
    "speak",
    "stop_speaking",
    "mic_available",
    "record_wav",
    "transcribe",
    "listen",
]

logger = logging.getLogger("wina.voice")

_SAMPLE_RATE = 44100
_GEMINI_MODEL = "gemini-2.5-flash"
_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)
_TRANSCRIBE_PROMPT = (
    "Transcribe this audio exactly, Uzbek or Russian or English. "
    "Return ONLY the transcript."
)

_ENGINE: object | None = None
_ENGINE_LOCK = Lock()
_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Shu fayl yonidagi .env ni o'qib yetishmayotgan env'larni to'ldiradi."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        candidates = [Path(__file__).resolve().parent]
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            if exe_dir not in candidates:
                candidates.append(exe_dir)
        for base in candidates:
            env_path = base / ".env"
            if not env_path.is_file():
                continue
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'").strip()
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception as exc:
        logger.debug("dotenv o'qishda xato: %s", exc)


def _api_key() -> str:
    _load_dotenv()
    return os.environ.get("GEMINI_API_KEY", "").strip().strip('"').strip("'").strip()


def _voice_prefs(lang: str) -> list[str]:
    lang = (lang or "uz").strip().lower()
    if lang.startswith("ru"):
        return ["ru", "rus", "uz", "uzbek", "en"]
    if lang.startswith("en"):
        return ["en", "eng", "ru", "uz"]
    return ["uz", "uzbek", "ru", "rus", "en", "eng"]


def _select_voice(engine: object, lang: str) -> None:
    """Mavjud voice'lardan tilga mosini tanlaydi, topilmasa default qoladi."""
    try:
        voices = engine.getProperty("voices") or []  # type: ignore[attr-defined]
    except Exception:
        return
    blobs: list[tuple[str, str]] = []
    for voice in voices:
        try:
            blob = f"{getattr(voice, 'id', '')} {getattr(voice, 'name', '')}"
            langs = getattr(voice, "languages", None) or []
            for item in langs:
                try:
                    blob += " " + item.decode("utf-8", "ignore")
                except AttributeError:
                    blob += " " + str(item)
                except Exception:
                    continue
            blobs.append((blob.lower(), str(getattr(voice, "id", ""))))
        except Exception:
            continue
    for token in _voice_prefs(lang):
        for blob, voice_id in blobs:
            if token in blob and voice_id:
                try:
                    engine.setProperty("voice", voice_id)  # type: ignore[attr-defined]
                    logger.debug("voice tanlandi: %s", voice_id)
                    return
                except Exception:
                    continue


def speak(text: str, lang: str = "uz") -> bool:
    """pyttsx3 (SAPI5) bilan bloklovchi gapirish. Xatoda False, raise yo'q."""
    if not isinstance(text, str) or not text.strip():
        return False
    try:
        import pyttsx3  # type: ignore[import-not-found]
    except Exception as exc:
        logger.warning("pyttsx3 topilmadi: %s", exc)
        return False
    try:
        if sys.platform == "win32":
            try:
                engine = pyttsx3.init("sapi5")
            except Exception:
                engine = pyttsx3.init()
        else:
            engine = pyttsx3.init()
        try:
            engine.setProperty("rate", 165)
        except Exception:
            pass
        _select_voice(engine, lang)
        global _ENGINE
        with _ENGINE_LOCK:
            _ENGINE = engine
        try:
            engine.say(text.strip())
            engine.runAndWait()
        finally:
            with _ENGINE_LOCK:
                if _ENGINE is engine:
                    _ENGINE = None
        return True
    except Exception as exc:
        logger.warning("speak xatosi: %s", exc)
        return False


def stop_speaking() -> None:
    """Joriy gapni to'xtatish. Hech qachon raise qilmaydi."""
    try:
        with _ENGINE_LOCK:
            engine = _ENGINE
        if engine is not None:
            try:
                engine.stop()  # type: ignore[attr-defined]
            except Exception as exc:
                logger.debug("stop xatosi: %s", exc)
    except Exception as exc:
        logger.debug("stop_speaking xatosi: %s", exc)


def mic_available() -> bool:
    """sounddevice orqali kirish kanali bor qurilma mavjudligini tekshiradi."""
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except Exception as exc:
        logger.debug("sounddevice topilmadi: %s", exc)
        return False
    try:
        devices = sd.query_devices()
        for device in devices:
            if isinstance(device, dict):
                inputs = device.get("max_input_channels", 0)
            else:
                inputs = getattr(device, "max_input_channels", 0)
            try:
                if int(inputs or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False
    except Exception as exc:
        logger.debug("mic tekshirishda xato: %s", exc)
        return False


def record_wav(seconds: float, path: str) -> str:
    """44100 Hz mono 16-bit yozib WAV saqlaydi, path qaytaradi."""
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "sounddevice o'rnatilmagan: pip install sounddevice"
        ) from exc
    try:
        import numpy as np  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError(
            "numpy topilmadi (sounddevice bilan keladi): pip install sounddevice"
        ) from exc
    seconds = float(seconds)
    if not seconds > 0:
        raise ValueError(f"seconds musbat bo'lishi kerak: {seconds!r}")
    if not path or not str(path).strip():
        raise ValueError("path bo'sh bo'lmasligi kerak")
    frames = max(1, int(_SAMPLE_RATE * seconds))
    try:
        recording = sd.rec(frames, samplerate=_SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
    except Exception as exc:
        logger.warning("yozib olishda xato: %s", exc)
        raise
    parent = os.path.dirname(os.path.abspath(str(path)))
    if parent:
        os.makedirs(parent, exist_ok=True)
    data = np.asarray(recording, dtype="int16")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(data.tobytes())
    return str(path)


def _extract_transcript(payload: object) -> str:
    """Gemini JSON javobidan transcript matnini yig'adi, bo'sh bo'lsa ''."""
    try:
        if not isinstance(payload, dict):
            return ""
        if payload.get("error"):
            logger.warning("gemini xatosi: %s", payload.get("error"))
            return ""
        chunks: list[str] = []
        candidates = payload.get("candidates") or []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") or {}
            if not isinstance(content, dict):
                continue
            for part in content.get("parts") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        return "".join(chunks).strip()
    except Exception as exc:
        logger.debug("transcript ajratishda xato: %s", exc)
        return ""


def transcribe(path: str, timeout: int = 60) -> str:
    """WAV ni Gemini'ga yuborib transcript qaytaradi. Bo'sh javobda ''."""
    try:
        key = _api_key()
        if not key:
            logger.warning("GEMINI_API_KEY topilmadi")
            return ""
        raw = Path(str(path)).read_bytes()
        if not raw:
            return ""
        encoded = base64.b64encode(raw).decode("ascii")
        body = json.dumps({
            "contents": [{
                "parts": [
                    {"text": _TRANSCRIBE_PROMPT},
                    {"inline_data": {"mime_type": "audio/wav", "data": encoded}},
                ]
            }]
        }).encode("utf-8")
        url = f"{_GEMINI_ENDPOINT}?key={quote(key, safe='')}"
        request = Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=int(timeout)) as response:
            resp_raw = response.read()
        try:
            payload = json.loads(resp_raw.decode("utf-8"))
        except Exception:
            return ""
        return _extract_transcript(payload)
    except FileNotFoundError as exc:
        logger.warning("audio fayl topilmadi: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("transcribe xatosi: %s", exc)
        return ""


def listen(seconds: float = 6.0) -> str:
    """Vaqtinchalik faylga yozib transcribe qiladi. Har qanday xatoda ''."""
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="wina_voice_")
        os.close(fd)
        record_wav(seconds, tmp_path)
        return transcribe(tmp_path).strip()
    except Exception as exc:
        logger.warning("listen xatosi: %s", exc)
        return ""
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
