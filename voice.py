# voice.py
# OpenAI TTS : conversion texte → audio (bytes MP3)
# OpenAI Whisper : conversion audio → texte

import os
import io
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

TTS_VOICE = os.getenv("TTS_VOICE", "nova")   # nova (femme), echo (homme), onyx (homme grave)
TTS_MODEL = os.getenv("TTS_MODEL", "tts-1")  # tts-1 (rapide) ou tts-1-hd (qualité)


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def texte_vers_audio(texte: str) -> bytes:
    """Convertit du texte en audio MP3 via OpenAI TTS."""
    client = _get_openai_client()
    reponse = client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=texte,
        response_format="mp3",
    )
    audio_bytes = reponse.content
    print(f"[TTS] Audio généré ({len(audio_bytes)} bytes) | voix={TTS_VOICE} | '{texte[:60]}...'")
    return audio_bytes


def audio_vers_texte(audio_bytes: bytes, format_audio: str = "wav") -> str:
    """Transcrit un audio en texte via OpenAI Whisper."""
    fichier_audio = io.BytesIO(audio_bytes)
    fichier_audio.name = f"audio.{format_audio}"
    try:
        transcription = _get_openai_client().audio.transcriptions.create(
            model="whisper-1",
            file=fichier_audio,
            language="fr",
        )
        texte = transcription.text.strip()
        print(f"[Whisper] Transcription : '{texte}'")
        return texte
    except Exception as e:
        print(f"[Whisper] Erreur de transcription : {e}")
        return ""


def sauvegarder_audio(audio_bytes: bytes, chemin_fichier: str):
    """Sauvegarde les bytes audio dans un fichier (debug)."""
    with open(chemin_fichier, "wb") as f:
        f.write(audio_bytes)
    print(f"[Voice] Audio sauvegardé : {chemin_fichier}")
