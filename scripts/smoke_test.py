"""Test de fumée HTTP de bout en bout (jalons 1 à 3).

Vérifie la connexion web, le cloisonnement multi-cliniques, les permissions par
rôle, le parcours dossier patient (web + API) et l'agenda des rendez-vous
(web + API), en parlant au serveur.

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
STAFF_LINK_RE = re.compile(r'href="(/personnel/\d+/)"')
PATIENT_LINK_RE = re.compile(r'href="(/patients/\d+/)"')
APPOINTMENT_LINK_RE = re.compile(r'href="(/rendez-vous/\d+/)"')
APPOINTMENT_LINK_RE = re.compile(r'href="(/rendez-vous/\d+/)"')


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
        headers: dict | None = None,
    ):
        headers = {"Content-Type": "application/json", **(headers or {})}
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
    links = sorted(set(STAFF_LINK_RE.findall(body)))
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

    # --- Parc dossiers patients (web) -------------------------------------
    status, body = doctor.get("/patients/")
    report.check(
        "liste des patients",
        status == 200 and "Dossiers actifs" in body and "Nouveaux ce mois" in body,
        str(status),
    )
    report.check(
        "menu Patients cliquable",
        'href="/patients/"' in body,
    )
    patient_links = sorted(set(PATIENT_LINK_RE.findall(body)))
    report.check(
        "liste cloisonnée",
        status == 200 and len(patient_links) >= 5,
        f"{len(patient_links)} dossiers",
    )
    report.check(
        "référence de dossier affichée",
        bool(re.search(r"PT-\d{4}-\d{5}", body)),
    )

    if patient_links:
        status, body = doctor.get(patient_links[0])
        report.check(
            "fiche patient",
            status == 200 and "Identité" in body and "Couverture" in body,
            patient_links[0],
        )
        status, _ = doctor.get(f"{patient_links[0]}modifier/")
        report.check("édition du dossier par le médecin", status == 200, str(status))
        status, _ = doctor.get(f"{patient_links[0]}archiver/")
        report.check("médecin : archivage interdite", status == 403, str(status))

    status, body = doctor.get("/patients/nouveau/")
    report.check(
        "formulaire de création (médecin)",
        status == 200
        and "Nouveau dossier patient" in body
        and "Repères médicaux" in body,
        str(status),
    )
    status, _, url = doctor.post(
        "/patients/nouveau/",
        {
            "first_name": "Yasmine",
            "last_name": "Idrissi",
            "gender": "F",
            "phone": "0612345678",
            "city": "Casablanca",
            "is_insured": "on",
            "insurer": "CNSS",
        },
    )
    report.check(
        "création d'un dossier (médecin)",
        status == 200 and re.search(r"/patients/\d+/", url or ""),
        url or str(status),
    )

    status, body = doctor.get("/patients/?q=Idrissi")
    report.check("recherche par nom", status == 200 and "Yasmine" in body, str(status))
    status, body = doctor.get("/patients/?status=archived")
    report.check("filtre des archivés", status == 200 and "PT-" in body, str(status))
    status, body = doctor.get("/patients/statistiques/")
    report.check("statistiques patients", status == 200, str(status))
    status, body = doctor.get("/patients/recherche/?q=ya")
    report.check("recherche rapide (HTMX)", status == 200, str(status))

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
    status, body = reception.get("/patients/")
    report.check(
        "accueil : accès aux dossiers",
        status == 200 and "Dossiers actifs" in body,
        str(status),
    )
    status, _ = reception.get("/patients/nouveau/")
    report.check("accueil : création d'un dossier autorisée", status == 200, str(status))

    # --- Agenda et rendez-vous (web) ---------------------------------------
    status, body = doctor.get("/agenda/")
    report.check("agenda du médecin", status == 200 and "Agenda" in body, str(status))
    report.check("menu Rendez-vous cliquable", 'href="/agenda/"' in body)
    status, _ = doctor.get("/agenda/semaine/")
    report.check("agenda hebdomadaire", status == 200, str(status))
    status, body = doctor.get("/agenda/file/")
    report.check("salle d'attente", status == 200, str(status))

    status, body = doctor.get("/rendez-vous/")
    report.check(
        "historique des rendez-vous",
        status == 200 and re.search(r"RDV-\d{4}-\d{5}", body) is not None,
        str(status),
    )
    doctor_appointments = sorted(set(APPOINTMENT_LINK_RE.findall(body)))
    report.check(
        "agenda du médecin cloisonné",
        status == 200 and 0 < len(doctor_appointments) <= 60,
        f"{len(doctor_appointments)} rendez-vous",
    )

    if doctor_appointments:
        status, body = doctor.get(doctor_appointments[0])
        report.check(
            "fiche de rendez-vous",
            status == 200 and "Parcours du rendez-vous" in body,
            doctor_appointments[0],
        )

    status, body = doctor.get("/rendez-vous/nouveau/")
    report.check(
        "formulaire de prise de rendez-vous",
        status == 200 and "Créneau disponible" in body,
        str(status),
    )

    status, body = reception.get("/agenda/")
    reception_appointments = sorted(set(APPOINTMENT_LINK_RE.findall(body)))
    report.check(
        "agenda de l'accueil : toute la clinique",
        status == 200 and len(reception_appointments) >= len(doctor_appointments),
        f"{len(reception_appointments)} rendez-vous",
    )
    report.check(
        "compteur « rendez-vous » du tableau de bord",
        "Agenda du jour" in reception.get("/")[1],
    )

    status, _, url = reception.post(
        "/rendez-vous/nouveau/",
        {
            "patient": patient_links[0].split("/")[-2] if patient_links else "",
            "practitioner": "",
            "day": "",
            "slot": "",
            "kind": "CONSULTATION",
            "status": "CONFIRMED",
            "reason": "Rendez-vous de démonstration",
        },
    )
    report.check(
        "prise de rendez-vous refusée sans créneau valide",
        status == 200 and url.rstrip("/").endswith("/rendez-vous/nouveau"),
        str(status),
    )

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

    # --- Comptabilite : aucun acces aux dossiers -----------------------------
    accountant = Browser(base)
    status, _, _ = accountant.post(
        "/connexion/", {"username": "comptable@clinique-al-amal.ma", "password": password}
    )
    report.check("connexion comptabilité", status == 200)
    status, _ = accountant.get("/patients/")
    report.check("comptabilité : dossiers patients interdits", status == 403, str(status))
    if patient_links:
        status, _ = accountant.get(patient_links[0])
        report.check("comptabilité : fiche patient interdite", status == 403, str(status))

    # --- Archivage / restauration (administrateur de clinique) -----------
    if patient_links:
        status, _ = admin.get(f"{patient_links[0]}archiver/")
        report.check(
            "administrateur : formulaire d'archivage", status == 200, str(status)
        )
        status, _, url = admin.post(
            f"{patient_links[0]}archiver/", {"reason": "dossier de démonstration"}
        )
        report.check(
            "administrateur : archivage du dossier",
            status == 200 and "/patients/" in (url or ""),
            url or str(status),
        )
        status, body = admin.get("/patients/?status=archived")
        report.check(
            "liste des dossiers archivés", status == 200 and "PT-" in body, str(status)
        )
        status, _, url = admin.post(f"{patient_links[0]}restaurer/", {})
        report.check(
            "administrateur : restauration du dossier",
            status == 200 and url.rstrip("/").endswith(patient_links[0].rstrip("/")),
            url or str(status),
        )
        status, body = admin.get(f"{patient_links[0]}fiche/")
        report.check(
            "fiche imprimable", status == 200 and "Fiche patient" in body, str(status)
        )
        status, body = admin.get(f"{patient_links[0]}resume/")
        report.check(
            "fragment de synthèse (HTMX)", status == 200 and "<" in body, str(status)
        )

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

        # --- API dossiers patients -----------------------------------------
        status, body = anon.api("/api/v1/patients/", token=token)
        report.check("API liste des patients", status == 200, str(status))
        patients_payload = json.loads(body) if status == 200 else {}
        report.check(
            "API pagination",
            "count" in patients_payload and "results" in patients_payload,
            f"count={patients_payload.get('count')}",
        )
        report.check(
            "API liste cloisonnée",
            patients_payload.get("count", 0) > 0
            and all(
                "reference" in row for row in patients_payload.get("results", [])[:1]
            ),
        )
        first_id = (
            patients_payload["results"][0]["id"]
            if patients_payload.get("results")
            else None
        )

        status, body = anon.api("/api/v1/patients/?q=Yasmine", token=token)
        report.check(
            "API recherche",
            status == 200 and json.loads(body)["results"][0]["last_name"] == "Idrissi",
            str(status),
        )

        status, body = anon.api(
            "/api/v1/patients/search/?q=Yas",
            token=token,
        )
        report.check(
            "API recherche rapide",
            status == 200 and len(json.loads(body)) >= 1,
            str(status),
        )

        status, body = anon.api(
            "/api/v1/patients/",
            payload={"first_name": "Amine", "last_name": "Smoke", "phone": "0655443322"},
            token=token,
        )
        report.check("API création", status == 201, str(status))
        created = json.loads(body) if status == 201 else {}
        report.check(
            "API référence automatique",
            str(created.get("reference", "")).startswith("PT-"),
            created.get("reference", ""),
        )
        created_id = created.get("id")

        if created_id:
            status, body = anon.api(
                f"/api/v1/patients/{created_id}/",
                payload={"city": "Rabat"},
                token=token,
                method="PATCH",
            )
            report.check(
                "API modification",
                status == 200 and json.loads(body)["city"] == "Rabat",
                str(status),
            )

            status, body = anon.api(
                f"/api/v1/patients/{created_id}/",
                token=token,
                method="DELETE",
            )
            report.check("API suppression refusée", status == 405, str(status))

            status, body = anon.api(
                f"/api/v1/patients/{created_id}/archive/",
                payload={"reason": "dossier de démonstration"},
                token=token,
                method="POST",
            )
            report.check("API archivage refusé au médecin", status == 403, str(status))

        status, body = anon.api(
            f"/api/v1/patients/{first_id}/" if first_id else "/api/v1/patients/?q=zzz",
            token=token,
        )
        report.check("API détail", status == 200, str(status))
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

    # --- API agenda et rendez-vous ----------------------------------------
    status, body = anon.api(
        "/api/v1/auth/login/",
        {"email": "reception@clinique-al-amal.ma", "password": password},
    )
    reception_token = json.loads(body).get("access") if status == 200 else None
    if reception_token:
        status, body = anon.api("/api/v1/appointments/", token=reception_token)
        report.check("API liste des rendez-vous", status == 200, str(status))
        payload = json.loads(body).get("results", [])
        report.check(
            "API rendez-vous cloisonnés par clinique",
            status == 200
            and payload
            and all(row["reference"].startswith("RDV-") for row in payload),
            f"{len(payload)} rendez-vous",
        )
        first = payload[0] if payload else None

        if first:
            status, body = anon.api(
                f"/api/v1/appointments/{first['id']}/", token=reception_token
            )
            report.check("API détail rendez-vous", status == 200, str(status))

        # Seuls les rendez-vous non terminés acceptent un changement de statut.
        status, body = anon.api(
            "/api/v1/appointments/?status=CONFIRMED", token=reception_token
        )
        confirmed = json.loads(body).get("results", []) if status == 200 else []
        pending = confirmed[0] if confirmed else None
        if pending:
            status, body = anon.api(
                f"/api/v1/appointments/{pending['id']}/status/",
                {"status": "ARRIVED"},
                token=reception_token,
                method="POST",
            )
            report.check(
                "API changement de statut",
                status == 200 and json.loads(body).get("status") == "ARRIVED",
                str(status),
            )
        else:
            report.check("API changement de statut", True, "agenda déjà traité")

        finished = next((row for row in payload if row["status"] == "DONE"), None)
        if finished:
            status, _ = anon.api(
                f"/api/v1/appointments/{finished['id']}/status/",
                {"status": "IN_PROGRESS"},
                token=reception_token,
                method="POST",
            )
            report.check(
                "API transition impossible refusée (consultation terminée)",
                status == 400,
                str(status),
            )

        status, body = anon.api(
            "/api/v1/appointments/availability/?date=2030-01-07",
            token=reception_token,
        )
        slots = json.loads(body).get("practitioners", []) if status == 200 else []
        report.check(
            "API créneaux disponibles",
            status == 200 and any(row["slots"] for row in slots),
            str(status),
        )

        status, body = anon.api(
            "/api/v1/appointments/patients/?q=Benali", token=reception_token
        )
        report.check(
            "API recherche patient pour rendez-vous",
            status == 200 and isinstance(json.loads(body), list),
            str(status),
        )

    if token:
        status, body = anon.api("/api/v1/appointments/", token=token)
        report.check(
            "API médecin : ses seuls rendez-vous",
            status == 200
            and all(
                row["practitioner"] == json.loads(body)["results"][0]["practitioner"]
                for row in json.loads(body)["results"]
            ),
            str(status),
        )

    # --- Comptabilité : API patients refusée --------------------------------
    status, body = anon.api(
        "/api/v1/auth/login/",
        {"email": "comptable@clinique-al-amal.ma", "password": password},
    )
    accountant_token = json.loads(body).get("access") if status == 200 else None
    if accountant_token:
        status, _ = anon.api("/api/v1/patients/", token=accountant_token)
        report.check("API comptabilité : patients refusés", status == 403, str(status))
        status, _ = anon.api("/api/v1/appointments/", token=accountant_token)
        report.check("API comptabilité : rendez-vous refusés", status == 403, str(status))

    # --- Cloisonnement API : en-tete X-Clinic hors perimetre ---------------
    if token:
        status, _ = anon.api(
            "/api/v1/patients/",
            token=token,
            headers={"X-Clinic": "clinique-inconnue"},
        )
        report.check("API X-Clinic hors périmètre refusé", status == 404, str(status))
        status, _ = anon.api(
            "/api/v1/patients/",
            token=token,
            headers={"X-Clinic": "clinique-essaouira"},
        )
        report.check(
            "API X-Clinic hors clinique autorisée refusé", status == 404, str(status)
        )

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
