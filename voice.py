# voice.py
# Google Cloud TTS Neural2 : conversion texte → audio (bytes MP3)
# OpenAI Whisper : conversion audio → texte

import os
import io
import json
from openai import OpenAI
from google.cloud import texttospeech
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

# Voix Google Cloud TTS Neural2 pour le français
# Hommes : fr-FR-Neural2-B, fr-FR-Neural2-D
# Femmes : fr-FR-Neural2-A, fr-FR-Neural2-C, fr-FR-Neural2-E
TTS_VOICE_NAME = os.getenv("TTS_VOICE_NAME", "fr-FR-Neural2-B")  # Homme naturel


def _get_google_tts_client() -> texttospeech.TextToSpeechClient:
    """Crée le client Google TTS depuis les credentials JSON stockés en variable d'env."""
    credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if credentials_json:
        credentials_info = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return texttospeech.TextToSpeechClient(credentials=credentials)
    # Fallback : utilise GOOGLE_APPLICATION_CREDENTIALS (fichier local)
    return texttospeech.TextToSpeechClient()


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def texte_vers_audio(texte: str) -> bytes:
    """
    Convertit du texte en audio MP3 via Google Cloud TTS Neural2.

    :param texte: Texte à synthétiser
    :return:      Audio au format MP3 en bytes
    """
    client = _get_google_tts_client()

    synthesis_input = texttospeech.SynthesisInput(text=texte)

    voice = texttospeech.VoiceSelectionParams(
        language_code="fr-FR",
        name=TTS_VOICE_NAME,
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=1.05,  # Légèrement plus rapide (1.0 = normal)
        pitch=0.0,           # Ton naturel (0 = pas de modification)
    )

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config,
    )

    audio_bytes = response.audio_content
    print(f"[Google TTS] Audio généré ({len(audio_bytes)} bytes) | voix={TTS_VOICE_NAME} | '{texte[:60]}...'")
    return audio_bytes


def audio_vers_texte(audio_bytes: bytes, format_audio: str = "wav") -> str:
    """
    Transcrit un audio en texte via OpenAI Whisper.

    :param audio_bytes:  Audio brut en bytes
    :param format_audio: Format du fichier audio (wav, mp3, ogg…)
    :return:             Texte transcrit
    """
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
    """Sauvegarde les bytes audio dans un fichier (utile pour debug)."""
    with open(chemin_fichier, "wb") as f:
        f.write(audio_bytes)
    print(f"[Voice] Audio sauvegardé : {chemin_fichier}")


# --- Test rapide --------------------------------------------------------------
if __name__ == "__main__":
    import sys

    texte_test = (
        "Bonjour, je m'appelle Tom de la société Energie AI. "
        "Je vous contacte pour voir si vous êtes éligible à des aides pour la rénovation énergétique. "
        "Vous avez deux minutes ?"
    )

    print(f"Test Google Cloud TTS Neural2 (voix={TTS_VOICE_NAME})...")
    try:
        audio = texte_vers_audio(texte_test)
        sauvegarder_audio(audio, "test_voice.mp3")
        print("✓ Audio généré avec succès : test_voice.mp3")
    except Exception as e:
        print(f"✗ Erreur TTS : {e}")
        sys.exit(1)
