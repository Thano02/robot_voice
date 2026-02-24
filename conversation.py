# conversation.py
# Cerveau de la conversation : GPT-4o pose les questions d'éligibilité
# et calcule le résultat final.

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Client initialisé à la demande pour éviter un crash au démarrage si la clé est absente
def _get_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Prompt système -----------------------------------------------------------
PROMPT_SYSTEME = """
Tu es Tom, un conseiller de la société Energie AI, spécialisé dans les aides à la rénovation énergétique (MaPrimeRénov, CEE, pompe à chaleur, isolation).
Tu appelles des particuliers pour évaluer leur éligibilité.

Ton comportement :
- Parle comme une vraie personne au téléphone : phrases courtes, ton naturel et chaleureux.
- Jamais de langage robotique ou trop formel.
- Pose UNE seule question à la fois. N'enchaîne jamais plusieurs questions.
- Si la réponse est floue, reformule simplement pour clarifier.
- Si la personne n'est pas disponible ou veut être rappelée, excuse-toi et conclus poliment.
- Ne promets jamais un montant précis d'aide.
- Parle uniquement en français.

Déroulement de l'appel :
1. Quand la personne répond à ton "Bonjour, comment allez-vous ?", présente-toi naturellement :
   tu t'appelles Tom, tu es de la société Energie AI, et tu appelles pour voir si la personne
   est éligible à des aides pour la rénovation énergétique. Demande-lui si elle a deux minutes.
2. Si elle est disponible, pose ces questions UNE par UNE, de façon conversationnelle :
   a. Vous êtes propriétaire ou locataire ?
   b. C'est une maison ou un appartement ?
   c. Il a été construit à peu près en quelle année ?
   d. Vous avez quel type de chauffage ? (fioul, gaz, électrique, autre)
   e. Et pour les revenus du foyer, vous êtes plutôt dans quelle tranche ?
      Modeste (moins de 21 000 €/an), intermédiaire (21 à 30 000 €), ou supérieur ?
3. Une fois les 5 réponses obtenues, conclus en 1 ou 2 phrases courtes et naturelles.
   Indique brièvement si la personne semble éligible, puis dis que tu transmets le dossier.

RÈGLE ABSOLUE pour la conclusion et l'au revoir :
- Maximum 20 mots. Phrase courte. Exemple : "Très bien, je transmets votre dossier. Bonne journée !"
- Ne fais JAMAIS de longues phrases à la fin. Une seule phrase d'au revoir, c'est tout.

Réponds UNIQUEMENT avec du texte à dire à voix haute. Pas de markdown, pas de listes, pas de symboles.
"""

# Clés extraites des réponses GPT
CHAMPS_REPONSES = [
    "proprietaire",
    "type_logement",
    "annee_construction",
    "chauffage_actuel",
    "revenus",
]


def calculer_eligibilite(reponses: dict) -> str:
    """
    Calcule l'éligibilité selon les règles MaPrimeRénov / CEE simplifiées.

    Règles :
    - PAC (pompe à chaleur) : propriétaire + maison + chauffage fioul ou gaz + construction < 2000
    - Isolation : propriétaire + construction < 1975 + revenus modeste ou intermédiaire
    - Les deux : cumul des conditions ci-dessus
    - Non éligible : sinon (ex : locataire, appartement récent, revenus supérieurs)
    """
    proprietaire        = str(reponses.get("proprietaire", "")).lower()
    type_logement       = str(reponses.get("type_logement", "")).lower()
    chauffage           = str(reponses.get("chauffage_actuel", "")).lower()
    revenus             = str(reponses.get("revenus", "")).lower()

    try:
        annee = int(reponses.get("annee_construction", 2010))
    except (ValueError, TypeError):
        annee = 2010

    est_proprietaire  = "propriétaire" in proprietaire or "proprio" in proprietaire
    est_maison        = "maison" in type_logement
    chauffage_fossile = any(c in chauffage for c in ["fioul", "gaz", "fuel"])
    revenus_ok        = "modeste" in revenus or "intermédiaire" in revenus or "intermediaire" in revenus
    ancien            = annee < 2000
    tres_ancien       = annee < 1975

    eligible_pac        = est_proprietaire and est_maison and chauffage_fossile and ancien
    eligible_isolation  = est_proprietaire and tres_ancien and revenus_ok

    if eligible_pac and eligible_isolation:
        return "Éligible PAC + Isolation"
    elif eligible_pac:
        return "Éligible PAC"
    elif eligible_isolation:
        return "Éligible Isolation"
    else:
        return "Non éligible"


class GestionnaireConversation:
    """
    Gère le fil de conversation avec GPT-4o pour un appel téléphonique.
    Maintient l'historique des messages et extrait les réponses.
    """

    def __init__(self, nom_prospect: str):
        self.nom_prospect = nom_prospect
        self.historique = []  # Liste de messages OpenAI (role/content)
        self.reponses_collectees: dict = {}
        self.conversation_terminee = False

        # Premier message : introduction
        self.historique.append({
            "role": "system",
            "content": PROMPT_SYSTEME
        })

    def demarrer(self) -> str:
        """Génère le message d'introduction : juste un bonjour, la présentation vient après."""
        introduction = f"Bonjour {self.nom_prospect}, comment allez-vous ?"
        self.historique.append({"role": "assistant", "content": introduction})
        return introduction

    def repondre(self, texte_utilisateur: str) -> str:
        """
        Prend la transcription de la réponse du prospect,
        extrait les données structurées, puis génère la prochaine réplique.
        Utilise gpt-4o-mini pour minimiser la latence.
        """
        if self.conversation_terminee:
            return ""

        # Ajoute la réponse du prospect
        self.historique.append({"role": "user", "content": texte_utilisateur})

        # Extraction des données (synchrone, rapide avec gpt-4o-mini)
        self._extraire_reponses(texte_utilisateur)

        # Génère la prochaine réplique
        try:
            completion = _get_client().chat.completions.create(
                model="gpt-4o-mini",
                messages=self.historique,
                temperature=0.7,
                max_tokens=100,  # Court pour éviter les longues phrases TTS
            )
            replique = completion.choices[0].message.content.strip()
        except Exception as e:
            print(f"[GPT] Erreur : {e}")
            replique = "Je suis désolé, une erreur s'est produite. Nous vous rappellerons."

        self.historique.append({"role": "assistant", "content": replique})

        # Détecte si la conversation est terminée
        mots_fin = ["bonne journée", "au revoir", "bonne soirée", "transmets votre dossier", "transmettre votre dossier", "passe une bonne"]
        if any(mot in replique.lower() for mot in mots_fin):
            self.conversation_terminee = True

        return replique

    def _extraire_reponses(self, texte: str):
        """
        Utilise GPT-4o pour extraire silencieusement les données structurées
        depuis le texte de l'utilisateur.
        """
        # On n'extrait que si on a encore des champs manquants
        champs_manquants = [c for c in CHAMPS_REPONSES if c not in self.reponses_collectees]
        if not champs_manquants:
            return

        prompt_extraction = f"""
À partir de ce texte prononcé par un prospect au téléphone :
"{texte}"

Extrait uniquement les informations disponibles parmi ces champs :
{', '.join(champs_manquants)}

Réponds UNIQUEMENT avec un JSON valide sans aucun texte autour.
Exemple : {{"proprietaire": "propriétaire", "type_logement": "maison"}}
Si une info n'est pas présente, ne l'inclus pas dans le JSON.
"""
        try:
            res = _get_client().chat.completions.create(
                model="gpt-4o-mini",  # Modèle léger pour l'extraction
                messages=[{"role": "user", "content": prompt_extraction}],
                temperature=0,
                max_tokens=150,
                response_format={"type": "json_object"},
            )
            import json
            extraits = json.loads(res.choices[0].message.content)
            self.reponses_collectees.update(extraits)
            print(f"[Extraction] Données collectées : {self.reponses_collectees}")
        except Exception as e:
            print(f"[Extraction] Impossible d'extraire les données : {e}")

    def obtenir_resultats(self) -> dict:
        """
        Retourne le dictionnaire final prêt à être écrit dans Excel.
        Inclut l'éligibilité calculée et le statut de l'appel.
        """
        eligibilite = calculer_eligibilite(self.reponses_collectees)

        return {
            "statut":             "Traité",
            "proprietaire":       self.reponses_collectees.get("proprietaire", ""),
            "type_logement":      self.reponses_collectees.get("type_logement", ""),
            "annee_construction": self.reponses_collectees.get("annee_construction", ""),
            "chauffage_actuel":   self.reponses_collectees.get("chauffage_actuel", ""),
            "revenus":            self.reponses_collectees.get("revenus", ""),
            "eligibilite":        eligibilite,
            "notes":              f"Appel traité automatiquement. {len(self.historique)} échanges.",
        }


# --- Test en mode console (sans téléphone) ------------------------------------
if __name__ == "__main__":
    print("=== Test conversation en mode console ===\n")
    gc = GestionnaireConversation("Jean Dupont")

    intro = gc.demarrer()
    print(f"Tom  : {intro}\n")

    while not gc.conversation_terminee:
        reponse = input("Vous : ").strip()
        if not reponse:
            continue
        replique = gc.repondre(reponse)
        print(f"Tom  : {replique}\n")

    print("\n=== Résultats collectés ===")
    import json
    print(json.dumps(gc.obtenir_resultats(), ensure_ascii=False, indent=2))
