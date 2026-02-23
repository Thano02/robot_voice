# voice.py
# Intégration ElevenLabs : conversion texte → audio (bytes MP3)
# et OpenAI Whisper : conversion audio → texte

import os
import io
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# --- Clients ------------------------------------------------------------------
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")

# URL de l'API ElevenLabs Text-to-Speech
ELEVENLABS_TTS_URL = (
    f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
)

# Paramètres de voix ElevenLabs (ajuste selon la voix choisie)
PARAMETRES_VOIX = {
    "stability":         0.55,  # Stabilité : 0 (expressif) → 1 (stable)
    "similarity_boost":  0.75,  # Fidélité à la voix clonée
    "style":             0.20,  # Style (disponible sur v2)
    "use_speaker_boost": True,
}


def texte_vers_audio(texte: str) -> bytes:
    """
    Envoie du texte à ElevenLabs et retourne l'audio en bytes (MP3).

    :param texte: Texte à synthétiser
    :return:      Audio au format MP3 en bytes
    :raises:      RuntimeError si l'API retourne une erreur
    """
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise RuntimeError(
            "ELEVENLABS_API_KEY ou ELEVENLABS_VOICE_ID manquant dans le .env"
        )

    headers = {
        "xi-api-key":   ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept":       "audio/mpeg",
    }

    payload = {
        "text":           texte,
        "model_id":       "eleven_multilingual_v2",  # Supporte le français
        "voice_settings": PARAMETRES_VOIX,
    }

    reponse = requests.post(
        ELEVENLABS_TTS_URL,
        headers=headers,
        json=payload,
        timeout=30,
    )

    if reponse.status_code != 200:
        raise RuntimeError(
            f"ElevenLabs erreur {reponse.status_code} : {reponse.text[:200]}"
        )

    print(f"[ElevenLabs] Audio généré ({len(reponse.content)} bytes) pour : '{texte[:60]}...'")
    return reponse.content


def audio_vers_texte(audio_bytes: bytes, format_audio: str = "wav") -> str:
    """
    Transcrit un audio en texte via OpenAI Whisper.

    :param audio_bytes:  Audio brut en bytes
    :param format_audio: Format du fichier audio (wav, mp3, ogg…)
    :return:             Texte transcrit
    """
    # Whisper attend un fichier-like object avec un nom
    fichier_audio = io.BytesIO(audio_bytes)
    fichier_audio.name = f"audio.{format_audio}"

    try:
        transcription = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=fichier_audio,
            language="fr",  # Forcer le français pour de meilleures performances
        )
        texte = transcription.text.strip()
        print(f"[Whisper] Transcription : '{texte}'")
        return texte
    except Exception as e:
        print(f"[Whisper] Erreur de transcription : {e}")
        return ""


def sauvegarder_audio(audio_bytes: bytes, chemin_fichier: str):
    """
    Sauvegarde les bytes audio dans un fichier (utile pour debug).

    :param audio_bytes:    Contenu audio en bytes
    :param chemin_fichier: Chemin de destination (ex: 'debug_audio.mp3')
    """
    with open(chemin_fichier, "wb") as f:
        f.write(audio_bytes)
    print(f"[Voice] Audio sauvegardé : {chemin_fichier}")


# --- Test rapide --------------------------------------------------------------
if __name__ == "__main__":
    import sys

    texte_test = (
        "Bonjour, je suis Sophie, conseillère en rénovation énergétique. "
        "Êtes-vous propriétaire ou locataire de votre logement ?"
    )

    print("Test ElevenLabs TTS...")
    try:
        audio = texte_vers_audio(texte_test)
        sauvegarder_audio(audio, "test_voice.mp3")
        print("✓ Audio généré avec succès : test_voice.mp3")
    except RuntimeError as e:
        print(f"✗ Erreur TTS : {e}")
        sys.exit(1)

    print("\nTest Whisper STT (depuis le fichier généré)...")
    with open("test_voice.mp3", "rb") as f:
        audio_bytes = f.read()
    texte_reconnu = audio_vers_texte(audio_bytes, format_audio="mp3")
    print(f"✓ Texte reconnu : '{texte_reconnu}'")
