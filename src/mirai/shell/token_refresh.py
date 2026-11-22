"""Renouvellement du jeton SSO : un seul à la fois dans le processus.

Le SSO fait tourner le jeton de renouvellement : le premier échange le
remplace, un second échange avec l'ancien reçoit 400 et la session est
perdue. Or plusieurs fils constatent ensemble que le jeton d'accès a expiré
(récupération de la configuration DM, relais, Éloignement) : un seul
renouvelle, les autres attendent puis reprennent le jeton qu'il a obtenu.

Le délai d'attente du serveur dépasse celui du Device Management vers le SSO
(30 s) : abandonner avant lui laisserait le SSO faire tourner le jeton sans
que le poste reçoive le nouveau.
"""

TOKEN_TIMEOUT_SECONDS = 35


def refresh_once(lock, current_token, refresh):
    """Jeton d'accès valide, ou None.

    `current_token()` rend le jeton stocké s'il est encore valide, sinon "" ;
    `refresh()` échange le jeton de renouvellement, stocke et rend le nouveau
    jeton d'accès, ou None. Sous le verrou, le jeton stocké est relu d'abord :
    un autre fil a pu le renouveler pendant l'attente."""
    if not lock.acquire(timeout=TOKEN_TIMEOUT_SECONDS):
        return current_token() or None
    try:
        return current_token() or refresh()
    finally:
        lock.release()


def refresh_payload(refresh_token, keycloak, read):
    """Formulaire d'échange du jeton de renouvellement. Le client vient de la
    configuration Keycloak reçue du DM, à défaut des réglages (`read(clé)`)."""
    client_id = (keycloak.get("client_id") or keycloak.get("clientId")
                 or read("keycloakClientId") or read("keycloak_client_id") or read("client_id"))
    client_secret = (keycloak.get("client_secret") or keycloak.get("clientSecret")
                     or read("keycloak_client_secret") or read("client_secret"))
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token,
               "client_id": client_id}
    if client_secret:
        payload["client_secret"] = client_secret
    return payload
