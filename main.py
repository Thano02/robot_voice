# main.py
# Serveur FastAPI — reçoit les webhooks Twilio, orchestre la conversation
# et écrit les résultats dans Excel en fin d'appel.

import os
import uuid
import asyncio
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv

from conversation import GestionnaireConversation
from voice import texte_vers_audio, audio_vers_texte
from excel_handler import (
    ecrire_resultat,
    marquer_pas_repondu,
    marquer_messagerie,
)

load_dotenv()

# --- Configuration ------------------------------------------------------------
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

# URL publique du serveur (Railway, ngrok en dev…)
BASE_URL = os.getenv("BASE_URL", "https://ton-projet.railway.app")


def _get_twilio_client() -> TwilioClient:
    """Client Twilio initialisé à la demande pour éviter un crash au démarrage."""
    return TwilioClient(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN"),
    )

app = FastAPI(title="Robot Éligibilité Énergétique")

# Dictionnaire en mémoire : call_sid → état de l'appel
# { call_sid: { "gestionnaire": GestionnaireConversation, "numero_ligne": int } }
sessions_actives: dict = {}

# Cache audio temporaire : audio_id → bytes MP3
# Les fichiers sont supprimés après avoir été servis une fois
audio_cache: dict = {}


# --- Endpoint : servir les fichiers audio temporaires -------------------------
@app.get("/audio/{audio_id}")
async def servir_audio(audio_id: str):
    """
    Sert un fichier audio MP3 depuis le cache mémoire.
    Twilio a besoin d'une URL publique pour <Play> — les data: URIs ne sont pas supportés.
    Le fichier est supprimé du cache après avoir été servi.
    """
    audio_bytes = audio_cache.pop(audio_id, None)
    if audio_bytes is None:
        return Response(status_code=404)
    return Response(content=audio_bytes, media_type="audio/mpeg")


# --- Utilitaire : TwiML audio -------------------------------------------------
def _stocker_audio(audio_bytes: bytes) -> str:
    """Stocke l'audio dans le cache et retourne l'URL publique."""
    audio_id = str(uuid.uuid4())
    audio_cache[audio_id] = audio_bytes
    return f"{BASE_URL}/audio/{audio_id}"


def twiml_jouer_audio(audio_bytes: bytes, url_suivant: str) -> str:
    """
    Construit un TwiML qui :
    1. Joue l'audio MP3 via une URL publique (Twilio ne supporte pas les data: URIs)
    2. Capture la réponse du prospect via <Gather>
    """
    audio_url = _stocker_audio(audio_bytes)

    response = VoiceResponse()
    gather = Gather(
        input="speech",           # Capture la voix
        action=url_suivant,       # Webhook appelé après la prise de parole
        method="POST",
        language="fr-FR",
        speech_timeout="auto",    # Détecte automatiquement la fin de parole
        timeout=8,                # Secondes d'attente avant timeout
    )
    gather.play(audio_url)
    response.append(gather)

    # Si personne ne parle après timeout, redirige vers /silence
    response.redirect(f"{BASE_URL}/silence", method="POST")

    return str(response)


def twiml_raccrocher(message_final: str = "") -> str:
    """TwiML simple pour raccrocher proprement."""
    response = VoiceResponse()
    if message_final:
        response.say(message_final, language="fr-FR")
    response.hangup()
    return str(response)


# --- Webhook : appel décroché -------------------------------------------------
@app.post("/appel-decroche")
async def appel_decroche(request: Request):
    """
    Twilio appelle ce webhook quand le prospect décroche.
    Reçoit : CallSid, To, From, CallStatus, etc.
    """
    form = await request.form()
    call_sid     = form.get("CallSid", "")
    call_status  = form.get("CallStatus", "")

    print(f"[Webhook] Appel décroché | SID={call_sid} | Status={call_status}")

    # --- Récupère le contexte de session (créé par caller.py) -----------------
    session = sessions_actives.get(call_sid)
    if not session:
        print(f"[Webhook] Session introuvable pour SID={call_sid}")
        return Response(content=twiml_raccrocher(), media_type="text/xml")

    gestionnaire: GestionnaireConversation = session["gestionnaire"]

    # Génère et joue le message d'introduction
    intro = gestionnaire.demarrer()
    try:
        audio = texte_vers_audio(intro)
        twiml = twiml_jouer_audio(audio, f"{BASE_URL}/reponse")
    except Exception as e:
        print(f"[ElevenLabs] Erreur TTS : {e}")
        # Fallback : voix Twilio native si ElevenLabs échoue
        response = VoiceResponse()
        gather = Gather(
            input="speech",
            action=f"{BASE_URL}/reponse",
            method="POST",
            language="fr-FR",
            speech_timeout="auto",
            timeout=8,
        )
        gather.say(intro, language="fr-FR")
        response.append(gather)
        response.redirect(f"{BASE_URL}/silence", method="POST")
        twiml = str(response)

    return Response(content=twiml, media_type="text/xml")


# --- Webhook : réponse du prospect --------------------------------------------
@app.post("/reponse")
async def reponse_prospect(request: Request):
    """
    Twilio envoie la transcription (ou l'audio) après que le prospect a parlé.
    On passe la réponse à GPT-4o et on joue la prochaine réplique.
    """
    form = await request.form()
    call_sid            = form.get("CallSid", "")
    speech_result       = form.get("SpeechResult", "")       # Transcription Twilio (basique)
    recording_url       = form.get("RecordingUrl", "")       # Si on utilise <Record>
    confidence          = float(form.get("Confidence", 0))

    print(f"[Webhook] Réponse SID={call_sid} | Confiance={confidence:.2f} | Texte='{speech_result}'")

    session = sessions_actives.get(call_sid)
    if not session:
        return Response(content=twiml_raccrocher(), media_type="text/xml")

    gestionnaire: GestionnaireConversation = session["gestionnaire"]

    # Si pas de transcription Twilio, utilise Whisper sur l'enregistrement
    if not speech_result and recording_url:
        speech_result = await _transcrire_enregistrement(recording_url)

    # Si toujours vide → silence / incompréhension
    if not speech_result:
        replique = "Je n'ai pas bien entendu, pourriez-vous répéter ?"
    else:
        replique = gestionnaire.repondre(speech_result)

    # Fin de conversation
    if gestionnaire.conversation_terminee:
        audio = texte_vers_audio(replique)
        _sauvegarder_resultats(call_sid, gestionnaire)
        # Joue l'au-revoir puis raccroche
        audio_url = _stocker_audio(audio)
        response = VoiceResponse()
        response.play(audio_url)
        response.pause(length=1)
        response.hangup()
        return Response(content=str(response), media_type="text/xml")

    # Continue la conversation
    try:
        audio = texte_vers_audio(replique)
        twiml = twiml_jouer_audio(audio, f"{BASE_URL}/reponse")
    except Exception as e:
        print(f"[ElevenLabs] Erreur TTS : {e}")
        response = VoiceResponse()
        gather = Gather(
            input="speech",
            action=f"{BASE_URL}/reponse",
            method="POST",
            language="fr-FR",
            speech_timeout="auto",
            timeout=8,
        )
        gather.say(replique, language="fr-FR")
        response.append(gather)
        response.redirect(f"{BASE_URL}/silence", method="POST")
        twiml = str(response)

    return Response(content=twiml, media_type="text/xml")


# --- Webhook : silence / pas de réponse ---------------------------------------
@app.post("/silence")
async def silence(request: Request):
    """Appelé si le prospect ne répond pas dans le délai imparti."""
    form = await request.form()
    call_sid = form.get("CallSid", "")

    session = sessions_actives.get(call_sid)
    if not session:
        return Response(content=twiml_raccrocher(), media_type="text/xml")

    # Incrémente le compteur de silences
    session["silences"] = session.get("silences", 0) + 1

    if session["silences"] >= 2:
        # Après 2 silences consécutifs → raccroche
        numero_ligne = session.get("numero_ligne")
        if numero_ligne:
            marquer_pas_repondu(numero_ligne)
        _nettoyer_session(call_sid)
        return Response(
            content=twiml_raccrocher("Je ne vous entends pas. Je vous rappellerai. Bonne journée."),
            media_type="text/xml",
        )

    # Relance avec une invite
    gestionnaire: GestionnaireConversation = session["gestionnaire"]
    replique = gestionnaire.repondre("[silence]")
    try:
        audio = texte_vers_audio(replique)
        twiml = twiml_jouer_audio(audio, f"{BASE_URL}/reponse")
    except Exception as e:
        print(f"[ElevenLabs] Erreur TTS : {e}")
        response = VoiceResponse()
        gather = Gather(
            input="speech",
            action=f"{BASE_URL}/reponse",
            method="POST",
            language="fr-FR",
            speech_timeout="auto",
            timeout=8,
        )
        gather.say(replique, language="fr-FR")
        response.append(gather)
        response.redirect(f"{BASE_URL}/silence", method="POST")
        twiml = str(response)

    return Response(content=twiml, media_type="text/xml")


# --- Webhook : résultat AMD (détection messagerie asynchrone) -----------------
@app.post("/amd-statut")
async def amd_statut(request: Request):
    """
    Twilio envoie ici le résultat de la détection messagerie (async_amd=True).
    Appelé séparément du webhook /appel-decroche, après analyse de la ligne.
    """
    form = await request.form()
    call_sid    = form.get("CallSid", "")
    answered_by = form.get("AnsweredBy", "")

    print(f"[AMD] SID={call_sid} | AnsweredBy={answered_by}")

    if answered_by in ("machine_start", "machine_end_beep", "machine_end_silence", "fax"):
        numero_ligne = _recuperer_numero_ligne(call_sid)
        if numero_ligne:
            marquer_messagerie(numero_ligne)
        # Raccroche l'appel via l'API Twilio
        try:
            _get_twilio_client().calls(call_sid).update(status="completed")
        except Exception as e:
            print(f"[AMD] Impossible de raccrocher {call_sid} : {e}")
        _nettoyer_session(call_sid)

    return PlainTextResponse("OK")


# --- Webhook : statut de l'appel (raccroché, etc.) ----------------------------
@app.post("/statut-appel")
async def statut_appel(request: Request):
    """
    Twilio notifie ici les changements de statut (completed, no-answer, busy, failed).
    Permet de gérer les cas d'absence de réponse côté Twilio.
    """
    form = await request.form()
    call_sid    = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")

    print(f"[Statut] SID={call_sid} | Status={call_status}")

    numero_ligne = _recuperer_numero_ligne(call_sid)

    if call_status == "no-answer" and numero_ligne:
        marquer_pas_repondu(numero_ligne)
    elif call_status == "busy" and numero_ligne:
        ecrire_resultat(numero_ligne, {"statut": "Occupé"})
    elif call_status == "failed" and numero_ligne:
        ecrire_resultat(numero_ligne, {"statut": "Échec appel"})

    _nettoyer_session(call_sid)
    return PlainTextResponse("OK")


# --- Endpoint : lancer un appel depuis caller.py ------------------------------
@app.post("/lancer-appel")
async def lancer_appel(request: Request):
    """
    Reçu depuis caller.py pour initier un appel Twilio.
    Body JSON : { "telephone": "+33612345678", "nom": "Jean Dupont", "numero_ligne": 2 }
    """
    data         = await request.json()
    telephone    = data.get("telephone")
    nom          = data.get("nom", "Prospect")
    numero_ligne = data.get("numero_ligne")

    if not telephone or not numero_ligne:
        return {"erreur": "Champs 'telephone' et 'numero_ligne' requis."}

    try:
        # Crée la session avant l'appel pour qu'elle soit prête au décrochage
        gestionnaire = GestionnaireConversation(nom)
        call = _get_twilio_client().calls.create(
            to=telephone,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{BASE_URL}/appel-decroche",
            status_callback=f"{BASE_URL}/statut-appel",
            status_callback_method="POST",
            machine_detection="DetectMessageEnd",           # Détection messagerie
            async_amd=True,                                 # Asynchrone : ne bloque pas le décrochage
            async_amd_status_callback=f"{BASE_URL}/amd-statut",  # Callback AMD dédié
            async_amd_status_callback_method="POST",
            timeout=30,                                     # Secondes avant "pas répondu"
        )

        sessions_actives[call.sid] = {
            "gestionnaire": gestionnaire,
            "numero_ligne": numero_ligne,
            "silences":     0,
        }

        print(f"[Appel] Initié | SID={call.sid} | {nom} | {telephone}")
        return {"call_sid": call.sid, "statut": "en_cours"}

    except Exception as e:
        print(f"[Appel] Erreur pour {telephone} : {e}")
        return {"erreur": str(e)}


# --- Fonctions utilitaires privées --------------------------------------------

def _recuperer_numero_ligne(call_sid: str) -> int | None:
    """Retourne le numéro de ligne Excel d'une session, ou None si introuvable."""
    session = sessions_actives.get(call_sid)
    return session.get("numero_ligne") if session else None


def _sauvegarder_resultats(call_sid: str, gestionnaire: GestionnaireConversation):
    """Écrit les résultats de la conversation dans Excel."""
    session = sessions_actives.get(call_sid)
    if not session:
        return
    numero_ligne = session.get("numero_ligne")
    if numero_ligne:
        resultats = gestionnaire.obtenir_resultats()
        ecrire_resultat(numero_ligne, resultats)
        print(f"[Excel] Résultats sauvegardés pour ligne {numero_ligne} : {resultats.get('eligibilite')}")
    _nettoyer_session(call_sid)


def _nettoyer_session(call_sid: str):
    """Supprime la session de la mémoire."""
    sessions_actives.pop(call_sid, None)


async def _transcrire_enregistrement(recording_url: str) -> str:
    """
    Télécharge un enregistrement Twilio et le transcrit via Whisper.
    Utilisé comme fallback si la transcription Twilio est vide.
    """
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            reponse = await client.get(
                recording_url + ".wav",
                auth=(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")),
                timeout=15,
            )
        return audio_vers_texte(reponse.content, format_audio="wav")
    except Exception as e:
        print(f"[Whisper] Erreur téléchargement enregistrement : {e}")
        return ""


# --- Point d'entrée -----------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
