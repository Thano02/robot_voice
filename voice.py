# voice.py
# ElevenLabs TTS : conversion texte → audio (bytes MP3)
# OpenAI Whisper  : conversion audio → texte

import os
import io
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# eleven_multilingual_v2 : meilleure qualité pour le français
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "XB0fDUnXU5powFXDhCwa")  # Charlotte (naturelle, multilingue)
ELEVENLABS_MODEL    = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")


def _get_eleven_client() -> ElevenLabs:
    return ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))


def texte_vers_audio(texte: str) -> bytes:
    """Convertit du texte en audio MP3 via ElevenLabs."""
    client = _get_eleven_client()
    audio_stream = client.text_to_speech.convert(
        voice_id=ELEVENLABS_VOICE_ID,
        model_id=ELEVENLABS_MODEL,
        text=texte,
        voice_settings=VoiceSettings(
            stability=0.35,          # Moins stable = plus expressif
            similarity_boost=0.80,   # Fidélité à la voix
            style=0.45,              # Style prononcé pour sonner humain
            use_speaker_boost=True,
        ),
        output_format="mp3_44100_128",
    )
    audio_bytes = b"".join(audio_stream)
    print(f"[ElevenLabs] Audio généré ({len(audio_bytes)} bytes) | '{texte[:60]}...'")
    return audio_bytes


def audio_vers_texte(audio_bytes: bytes, format_audio: str = "wav") -> str:
    """Transcrit un audio en texte via OpenAI Whisper."""
    fichier_audio = io.BytesIO(audio_bytes)
    fichier_audio.name = f"audio.{format_audio}"
    try:
        transcription = OpenAI(api_key=os.getenv("OPENAI_API_KEY")).audio.transcriptions.create(
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
