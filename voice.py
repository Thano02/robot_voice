# voice.py
# OpenAI TTS (tts-1-hd / voix onyx) : conversion texte → audio (bytes MP3)
# OpenAI Whisper : conversion audio → texte

import os
import io
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

TTS_VOICE = os.getenv("TTS_VOICE", "echo")   # echo (homme doux), onyx (homme grave), nova (femme)
TTS_MODEL = os.getenv("TTS_MODEL", "tts-1")  # tts-1 (rapide ~1s) ou tts-1-hd (qualité ~4s)


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def texte_vers_audio(texte: str) -> bytes:
    """
    Convertit du texte en audio MP3 via OpenAI TTS.

    :param texte: Texte à synthétiser
    :return:      Audio au format MP3 en bytes
    """
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

    print(f"Test OpenAI TTS (voix={TTS_VOICE}, modèle={TTS_MODEL})...")
    try:
        audio = texte_vers_audio(texte_test)
        sauvegarder_audio(audio, "test_voice.mp3")
        print("✓ Audio généré avec succès : test_voice.mp3")
    except Exception as e:
        print(f"✗ Erreur TTS : {e}")
        sys.exit(1)

    print("\nTest Whisper STT (depuis le fichier généré)...")
    with open("test_voice.mp3", "rb") as f:
        audio_bytes = f.read()
    texte_reconnu = audio_vers_texte(audio_bytes, format_audio="mp3")
    print(f"✓ Texte reconnu : '{texte_reconnu}'")
