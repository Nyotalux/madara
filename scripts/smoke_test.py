"""Test de fumée HTTP de bout en bout (jalon 1).

Vérifie la connexion web, le cloisonnement multi-cliniques et les permissions
par rôle, en parlant réellement au serveur.

    python manage.py runserver 8765        # dans un terminal
    python scripts/smoke_test.py            # dans un autre

    python scripts/smoke_test.py --base http://127.0.0.1:8000 --password MonMotDePasse
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

CSRF_RE = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
LINK_RE = re.compile(r'href="(/personnel/\d+/)"')


class Browser:
    """Client HTTP minimal avec conservation des cookies (session + CSRF)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def get(self, path: str, headers: dict | None = None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with self.opener.open(request) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def post(self, path: str, data: dict, headers: dict | None = None):
        token_match = CSRF_RE.search(self.get(path)[1])
        payload = dict(data)
        if token_match:
            payload["csrfmiddlewaretoken"] = token_match.group(1)
        request = urllib.request.Request(
            self.base + path,
            data=urllib.parse.urlencode(payload).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self.base + path,
                **(headers or {}),
            },
        )
        try:
            with self.opener.open(request) as response:
                return (
                    response.status,
                    response.read().decode("utf-8", "replace"),
                    response.url,
                )
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), path

    def api(
        self,
        path: str,
        payload: dict | None = None,
        token: str | None = None,
        method: str | None = None,
    ):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method=method or ("POST" if data is not None else "GET"),
        )
        try:
            with self.opener.open(request) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")


class Report:
    def __init__(self):
        self.failures = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        status = "OK   " if condition else "FAIL "
        if not condition:
            self.failures += 1
        line = f"{status} {label}"
        if detail:
            line += f"  ({detail})"
        print(line)


def run(base: str, password: str) -> int:
    report = Report()

    # --- Sondes de sante -------------------------------------------------
    anon = Browser(base)
    status, _ = anon.get("/health/")
    report.check("GET /health/", status == 200)
    status, _ = anon.get("/health/ready/")
    report.check("GET /health/ready/", status == 200)

    # --- Connexion medecin ------------------------------------------------
    doctor = Browser(base)
    status, _, url = doctor.post(
        "/connexion/",
        {"username": "dr.amine.benali@clinique-al-amal.ma", "password": password},
    )
    report.check("connexion médecin", status == 200 and url.rstrip("/") == base, url)

    status, body = doctor.get("/")
    report.check("tableau de bord médecin", status == 200 and "Clinique Al Amal" in body)
    report.check(
        "aucune fuite inter-clinique",
        "Clinique Essaouira" not in body.split("<footer")[0],
    )

    status, body = doctor.get("/personnel/")
    links = sorted(set(LINK_RE.findall(body)))
    report.check(
        "liste du personnel cloisonnée",
        status == 200 and len(links) >= 11,
        f"{len(links)} membres",
    )

    if links:
        status, body = doctor.get(links[0])
        report.check(
            "fiche du personnel",
            status == 200 and "Horaires d" in body and "Indisponibilit" in body,
            f"{links[0]}",
        )

    status, _ = doctor.get("/personnel/nouveau/")
    report.check("médecin : création de personnel interdite", status == 403, str(status))

    status, _ = doctor.get("/personnel/", headers={"X-Clinic": "clinique-essaouira"})
    report.check("en-tête X-Clinic hors périmètre refusé", status == 404, str(status))

    # --- Connexion accueil ------------------------------------------------
    reception = Browser(base)
    status, _, _ = reception.post(
        "/connexion/",
        {"username": "reception@clinique-al-amal.ma", "password": password},
    )
    report.check("connexion accueil", status == 200)
    status, _ = reception.get("/personnel/nouveau/")
    report.check("accueil : création de personnel interdite", status == 403, str(status))

    # --- Connexion administrateur de clinique ------------------------------
    admin = Browser(base)
    status, _, _ = admin.post(
        "/connexion/", {"username": "admin@clinique-al-amal.ma", "password": password}
    )
    report.check("connexion administrateur de clinique", status == 200)
    status, body = admin.get("/personnel/nouveau/")
    report.check("administrateur : formulaire accessible", status == 200, str(status))
    status, body = admin.get("/profil/")
    report.check("page profil", status == 200 and "Mon profil" in body)
    status, body = admin.get("/cliniques/clinique-al-amal/")
    report.check("page clinique", status == 200 and "Coordonnées" in body)

    # --- Mot de passe errone ----------------------------------------------
    bad = Browser(base)
    status, body, _ = bad.post(
        "/connexion/",
        {"username": "dr.amine.benali@clinique-al-amal.ma", "password": "faux"},
    )
    report.check(
        "mot de passe erroné refusé", status == 200 and "incorrect" in body.lower()
    )

    # --- API mobile (JWT) --------------------------------------------------
    status, body = anon.api(
        "/api/v1/auth/login/",
        {"email": "dr.amine.benali@clinique-al-amal.ma", "password": password},
    )
    report.check("API login", status == 200, str(status))
    token = None
    if status == 200:
        payload = json.loads(body)
        token = payload.get("access")
        report.check(
            "réponse JWT enrichie", "memberships" in payload and "clinics" in payload
        )

    if token:
        status, body = anon.api("/api/v1/me/", token=token)
        report.check("API /me/", status == 200, str(status))
        status, body = anon.api(
            "/api/v1/me/",
            payload={"first_name": "Amine"},
            token=token,
            method="PATCH",
        )
        report.check("API /me/ PATCH", status == 200, str(status))
        status, body = anon.api("/api/v1/staff/", token=token)
        report.check("API annuaire du personnel", status == 200, str(status))
        status, body = anon.api("/api/v1/clinics/", token=token)
        report.check("API cliniques", status == 200, str(status))
        status, body = anon.api("/api/v1/clinics/current/", token=token)
        report.check("API clinique courante", status == 200, str(status))
        status, body = anon.api(
            "/api/v1/me/switch-clinic/",
            payload={"clinic": 2},
            token=token,
            method="POST",
        )
        report.check(
            "API bascule vers une clinique non autorisée refusée",
            status == 403,
            str(status),
        )

    status, _ = anon.api("/api/v1/me/")
    report.check("API /me/ sans jeton refusé", status == 401, str(status))

    print()
    if report.failures:
        print(f"{report.failures} vérification(s) en échec.")
        return 1
    print("Toutes les vérifications sont passées.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--password", default="Madara2026!")
    args = parser.parse_args()
    return run(args.base, args.password)


if __name__ == "__main__":
    sys.exit(main())
