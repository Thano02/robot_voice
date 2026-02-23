# caller.py
# Lance les appels téléphoniques en lisant prospects.xlsx
# Gère la concurrence (max_simultane appels en parallèle)

import os
import time
import asyncio
import httpx
from dotenv import load_dotenv

from excel_handler import lire_prospects, marquer_pas_repondu, ecrire_resultat

load_dotenv()

# --- Configuration ------------------------------------------------------------
BASE_URL        = os.getenv("BASE_URL", "http://localhost:8000")
MAX_SIMULTANE   = int(os.getenv("MAX_APPELS_SIMULTANES", 3))   # Appels en parallèle
DELAI_ENTRE_APPELS = float(os.getenv("DELAI_ENTRE_APPELS", 2.0))  # Secondes entre chaque lancement
TIMEOUT_APPEL   = int(os.getenv("TIMEOUT_APPEL", 60))          # Secondes avant abandon


async def lancer_un_appel(
    client: httpx.AsyncClient,
    prospect: dict,
    semaphore: asyncio.Semaphore,
) -> dict:
    """
    Lance un appel pour un prospect via l'endpoint /lancer-appel de main.py.
    Utilise un sémaphore pour limiter le nombre d'appels simultanés.

    :param client:    Client HTTP asyncio partagé
    :param prospect:  Dict avec keys: telephone, nom, numero_ligne
    :param semaphore: Limite la concurrence
    :return:          Résultat de l'appel (dict)
    """
    async with semaphore:
        telephone    = prospect["telephone"]
        nom          = prospect["nom"]
        numero_ligne = prospect["numero_ligne"]

        print(f"[Caller] Appel → {nom} ({telephone}) | Ligne Excel {numero_ligne}")

        # Validation du numéro (format E.164 minimum)
        if not telephone or not telephone.startswith("+") or len(telephone) < 10:
            print(f"[Caller] ✗ Numéro invalide pour {nom} : '{telephone}'")
            marquer_pas_repondu(numero_ligne)
            return {"prospect": nom, "statut": "numéro_invalide"}

        try:
            reponse = await client.post(
                f"{BASE_URL}/lancer-appel",
                json={
                    "telephone":    telephone,
                    "nom":          nom,
                    "numero_ligne": numero_ligne,
                },
                timeout=TIMEOUT_APPEL,
            )
            reponse.raise_for_status()
            data = reponse.json()

            if "erreur" in data:
                print(f"[Caller] ✗ Erreur pour {nom} : {data['erreur']}")
                return {"prospect": nom, "statut": "erreur", "detail": data["erreur"]}

            print(f"[Caller] ✓ Appel lancé | SID={data.get('call_sid')} | {nom}")
            return {"prospect": nom, "statut": "lancé", "call_sid": data.get("call_sid")}

        except httpx.TimeoutException:
            print(f"[Caller] ✗ Timeout pour {nom} ({telephone})")
            marquer_pas_repondu(numero_ligne)
            return {"prospect": nom, "statut": "timeout"}

        except httpx.HTTPStatusError as e:
            print(f"[Caller] ✗ HTTP {e.response.status_code} pour {nom}")
            return {"prospect": nom, "statut": "erreur_http", "code": e.response.status_code}

        except Exception as e:
            print(f"[Caller] ✗ Exception inattendue pour {nom} : {e}")
            return {"prospect": nom, "statut": "erreur_inconnue", "detail": str(e)}


async def lancer_tous_les_appels(prospects: list[dict]) -> list[dict]:
    """
    Lance tous les appels en parallèle (limité par MAX_SIMULTANE).

    :param prospects: Liste de prospects à appeler
    :return:          Liste des résultats
    """
    if not prospects:
        print("[Caller] Aucun prospect à appeler.")
        return []

    semaphore = asyncio.Semaphore(MAX_SIMULTANE)

    # Délai progressif entre le lancement des tâches pour éviter les bursts
    async def appeler_avec_delai(client, prospect, index):
        await asyncio.sleep(index * DELAI_ENTRE_APPELS)
        return await lancer_un_appel(client, prospect, semaphore)

    async with httpx.AsyncClient() as client:
        taches = [
            appeler_avec_delai(client, prospect, i)
            for i, prospect in enumerate(prospects)
        ]
        resultats = await asyncio.gather(*taches, return_exceptions=True)

    # Normalise les exceptions en résultats d'erreur
    resultats_propres = []
    for i, r in enumerate(resultats):
        if isinstance(r, Exception):
            resultats_propres.append({
                "prospect": prospects[i]["nom"],
                "statut":   "exception",
                "detail":   str(r),
            })
        else:
            resultats_propres.append(r)

    return resultats_propres


def afficher_bilan(resultats: list[dict]):
    """Affiche un résumé des appels lancés."""
    print("\n" + "=" * 50)
    print("BILAN DES APPELS")
    print("=" * 50)

    compteurs = {}
    for r in resultats:
        statut = r.get("statut", "inconnu")
        compteurs[statut] = compteurs.get(statut, 0) + 1

    for statut, nb in compteurs.items():
        print(f"  {statut:20s} : {nb}")

    print(f"\nTotal traité : {len(resultats)} prospect(s)")
    print("=" * 50)


def verifier_serveur_disponible(base_url: str) -> bool:
    """Vérifie que le serveur FastAPI est bien démarré avant de lancer les appels."""
    try:
        reponse = httpx.get(f"{base_url}/docs", timeout=5)
        return reponse.status_code == 200
    except Exception:
        return False


async def poller_resultats(nb_appels: int, timeout_minutes: int = 10):
    """
    Attend les résultats des appels depuis Railway et les écrit dans Excel local.
    Polls GET /resultats toutes les 15 secondes jusqu'à avoir tous les résultats
    ou jusqu'au timeout.
    """
    print(f"\n[Caller] En attente des résultats ({nb_appels} appel(s) en cours)...")
    print("[Caller] Les résultats seront écrits dans prospects.xlsx au fur et à mesure.")

    resultats_recus = 0
    debut = time.time()
    intervalle = 15  # secondes entre chaque poll

    async with httpx.AsyncClient() as client:
        while resultats_recus < nb_appels:
            # Timeout global
            if time.time() - debut > timeout_minutes * 60:
                print(f"[Caller] Timeout atteint ({timeout_minutes} min). "
                      f"{resultats_recus}/{nb_appels} résultats reçus.")
                break

            await asyncio.sleep(intervalle)

            try:
                reponse = await client.get(f"{BASE_URL}/resultats", timeout=10)
                data = reponse.json()
                nouveaux = data.get("resultats", [])

                for item in nouveaux:
                    numero_ligne = item["numero_ligne"]
                    donnees      = item["donnees"]
                    ecrire_resultat(numero_ligne, donnees)
                    eligibilite = donnees.get("eligibilite", "?")
                    print(f"[Caller] ✓ Excel mis à jour | Ligne {numero_ligne} | {eligibilite}")
                    resultats_recus += 1

            except Exception as e:
                print(f"[Caller] Erreur polling : {e}")

    if resultats_recus >= nb_appels:
        print(f"\n[Caller] ✓ Tous les résultats sauvegardés ({resultats_recus}/{nb_appels})")
    print(f"[Caller] Durée totale : {(time.time() - debut) / 60:.1f} min")


def main():
    """Point d'entrée principal : lit Excel et lance les appels."""
    print("=" * 50)
    print("ROBOT ÉLIGIBILITÉ ÉNERGÉTIQUE")
    print(f"MAX appels simultanés : {MAX_SIMULTANE}")
    print(f"Serveur              : {BASE_URL}")
    print("=" * 50 + "\n")

    # Vérifie que le serveur est disponible
    if not verifier_serveur_disponible(BASE_URL):
        print(f"[Caller] ✗ Le serveur FastAPI n'est pas accessible sur {BASE_URL}")
        print("[Caller]   Démarre d'abord : python main.py")
        return

    # Lecture des prospects à appeler
    print("[Caller] Lecture du fichier prospects.xlsx...")
    prospects = lire_prospects()

    if not prospects:
        print("[Caller] Aucun prospect avec le statut 'À appeler'. Arrêt.")
        return

    print(f"[Caller] {len(prospects)} prospect(s) trouvé(s) :\n")
    for p in prospects:
        print(f"  Ligne {p['numero_ligne']:3d} | {p['nom']:<20s} | {p['telephone']}")

    print()

    # Lance les appels de manière asynchrone
    debut = time.time()
    resultats_lancement = asyncio.run(lancer_tous_les_appels(prospects))
    duree = time.time() - debut

    afficher_bilan(resultats_lancement)
    print(f"\nDurée totale de lancement : {duree:.1f}s")

    # Compte les appels réellement lancés (pas les erreurs immédiates)
    nb_lances = sum(1 for r in resultats_lancement if r.get("statut") == "lancé")

    if nb_lances > 0:
        # Attend et récupère les résultats depuis Railway → écrit dans Excel local
        asyncio.run(poller_resultats(nb_lances))


if __name__ == "__main__":
    main()
