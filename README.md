# Madara — système de gestion de clinique

Application de gestion de clinique médicale : patients, rendez-vous, consultations,
dossiers médicaux, transferts, notifications et finances.

- **Back-office web** : Django + HTMX, en français, session Django.
- **API mobile** : Django REST Framework + JWT, cloisonnée par clinique.
- **Multi-clinique** : chaque donnée appartient à une clinique ; l'accès est filtré
  par rôle et par clinique courante (session web ou en-tête `X-Clinic` côté mobile).
- **Données** : PostgreSQL. **Tâches de fond** : Celery + Redis.

---

## 1. Prérequis

| Outil | Version | Rôle |
|-------|---------|------|
| Python | 3.10+ | back-end |
| PostgreSQL | 14+ | base de données |
| Redis | 6+ | broker Celery (optionnel en développement) |

Aucun Docker n'est nécessaire : PostgreSQL et Redis tournent en service local.

## 2. Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # puis adapter DATABASE_URL / POSTGRES_*
python manage.py migrate
python manage.py seed_demo     # optionnel : données de démonstration
```

## 3. Démarrage

```bash
python manage.py runserver 8765          # http://127.0.0.1:8765/
celery -A config worker -l info          # dans un second terminal (optionnel)
```

| URL | Rôle |
|-----|------|
| `/` | back-office (connexion requise) |
| `/connexion/` | connexion e-mail + mot de passe |
| `/admin/` | administration Django (réservée à la plateforme) |
| `/health/`, `/health/db/`, `/health/ready/` | sondes de santé |
| `/api/v1/docs/` | documentation OpenAPI (Swagger UI) |
| `/api/v1/schema/` | schéma OpenAPI |

## 4. Variables d'environnement

Tout est piloté par `.env` (voir `.env.example`) :

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` — sécurité.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`.
- `REDIS_URL` — broker Celery.
- `TIME_ZONE`, `DEFAULT_TAX_RATE` — localisation et fiscalité par défaut.
- `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL` — envois d'e-mail.
- `WHATSAPP_PROVIDER` — `console` (journal), `meta` ou `twilio`.
- `CORS_ALLOWED_ORIGINS` — origines autorisées pour l'API mobile.

## 5. Rôles et cloisonnement

| Rôle | Périmètre |
|------|-----------|
| `ADMIN` | administrateur de clinique |
| `RECEPTION` | accueil |
| `DOCTOR` | médecin |
| `NURSE` | infirmier |
| `ACCOUNTANT` | comptabilité |

Les dossiers patients sont accessibles en lecture et en écriture par `ADMIN`,
`RECEPTION`, `DOCTOR` et `NURSE` ; la comptabilité est exclue. L'archivage et la
restauration sont réservés à l'administrateur de clinique. La suppression est
impossible (l'historique médical est conservé) : `DELETE` renvoie `405`.

Chaque modèle métier hérite de `BaseModel` : horodatage, auteur, clinique et
archivage logique. Les managers `objects` filtrent automatiquement sur la clinique
courante ; `all_objects` sert aux vues transverses (administration plateforme,
tâches de fond). Un compte plateforme (`is_platform_staff`) contourne ce filtrage.

Sélection de la clinique courante :

- **web** : cookie de session (`madara_clinic_id`), modifiable depuis l'en-tête ;
- **API** : en-tête `X-Clinic` (slug ou identifiant), sinon la clinique par défaut.

## 6. Dossiers patients (jalon 2)

Chaque dossier porte une référence automatique `PT-AAAA-00001` par clinique et couvre
l'identité (CIN, passeport), le contact, la couverture sociale (assureur, validité) et
les repères médicaux (groupe sanguin, allergies, maladies chroniques, traitement en
cours). Les consultations, ordonnances, pièces jointes et partages sont décrits
au § 8.

| Écran | Route |
|-------|-------|
| Liste, recherche, filtres, statistiques | `/patients/` |
| Fiche détaillée | `/patients/{id}/` |
| Création / modification | `/patients/nouveau/`, `/patients/{id}/modifier/` |
| Archivage / restauration | `/patients/{id}/archiver/`, `/patients/{id}/restaurer/` |
| Fiche imprimable | `/patients/{id}/fiche/` |
| Recherche rapide (HTMX) | `/patients/recherche/?q=` |

## 7. Agenda et rendez-vous (jalon 3)

Chaque rendez-vous porte une référence automatique `RDV-AAAA-00001` par clinique et
relie un patient à un praticien (médecin ou infirmier) sur un créneau horodaté.
La règle métier vit dans `apps/appointments/services.py` : créneaux disponibles,
contrôle des conflits, transitions de statut — le formulaire web et l'API passent
tous deux par elle.

| Règle | Comportement |
|-------|--------------|
| Créneaux | déduits des horaires d'ouverture du praticien (jalon 1), moins ses rendez-vous et ses indisponibilités |
| Conflits | un créneau ne peut être pris qu'une fois (praticien), et un patient ne peut avoir deux rendez-vous simultanés |
| Hors horaires | toute écriture hors des horaires d'ouverture est refusée (web, API, console) |
| Statuts | à confirmer → confirmé → arrivé → en consultation → terminé ; annulé et absent sont repris |
| Annulation | motif + auteur journalisés ; le créneau est immédiatement libéré |
| Terminé | une consultation terminée est définitive (pas de retour arrière) |

| Écran | Route |
|-------|-------|
| Journée (par praticien) | `/agenda/` |
| Semaine | `/agenda/semaine/` |
| Salle d'attente | `/agenda/file/` |
| Historique filtrable | `/rendez-vous/` |
| Fiche (parcours, motif, notes) | `/rendez-vous/{id}/` |
| Prise / modification | `/rendez-vous/nouveau/`, `/rendez-vous/{id}/modifier/` |
| Changement de statut / action rapide | `/rendez-vous/{id}/statut/`, `/rendez-vous/{id}/statut/{statut}/` |
| Créneaux (fragment HTMX) | `/rendez-vous/creneaux/` |

API : `/api/v1/appointments/` (+ `?date=`, `?du=&au=`, `?practitioner=`, `?status=`),
`…/{id}/status/`, `…/{id}/cancel/`, `/availability/`, `/patients/?q=`.

Cloisonnement : un médecin ou un infirmier ne voit et ne modifie que ses propres
rendez-vous ; l'accueil et l'administrateur gèrent toute la clinique. Les rappels
automatiques arrivent au jalon 5 (le champ `reminder_sent_at` est déjà en place).

## 8. Consultations et dossiers médicaux (jalon 4)

La consultation est l'acte médical : elle prolonge un rendez-vous de la salle
d'attente (« Démarrer la consultation ») ou se crée librement pour un patient. Elle
porte le motif, l'examen, les constantes, le diagnostic, la conduite à tenir et ses
ordonnances. La règle métier vit dans `apps/consultations/services.py` (démarrage,
clôture, annulation) et `apps/medical_records/services.py` (pièces, partages).

| Règle | Comportement |
|-------|--------------|
| Références | `CONS-AAAA-00001` (consultation), `ORD-AAAA-00001` (ordonnance), `DM-AAAA-00001` (dossier médical) |
| Démarrage depuis la salle d'attente | le rendez-vous passe « en consultation » ; un double clic renvoie la consultation déjà ouverte |
| Clôture | la consultation passe « terminée » et le rendez-vous associé passe « terminé » |
| Annulation | la consultation est conservée (`CANCELLED`), le motif rejoint les notes ; elle n'est plus modifiable |
| Écriture des actes | médecin, infirmier, administrateur — l'accueil lit et gère les dossiers mais ne rédige pas de consultation |
| Périmètre | un médecin ou un infirmier ne voit et n'agit que sur ses propres consultations |
| Dossier médical | un par patient, créé à la volée ; chaque pièce est validée (extension et poids maximum) |
| Partage | lien public en lecture seule, durée bornée (30 jours maximum, `RECORD_SHARE_MAX_DAYS`) et révocable |

| Écran | Route |
|-------|-------|
| Historique filtrable + compteurs | `/consultations/` |
| Fiche (compte rendu, constantes, ordonnances) | `/consultations/{id}/` |
| Démarrage (depuis la salle d'attente ou libre) | `/rendez-vous/{id}/consultation/`, `/consultations/nouveau/` |
| Compte rendu / clôture / annulation | `/consultations/{id}/modifier/`, `/consultations/{id}/terminer/`, `/consultations/{id}/annuler/` |
| Ordonnance : rédaction et version imprimable | `/consultations/{id}/ordonnance/`, `/ordonnances/{id}/imprimer/` |
| Dossier médical du patient | `/patients/{id}/dossier/` |
| Antécédents structurés | `/patients/{id}/dossier/modifier/` |
| Pièces jointes | `/patients/{id}/dossier/document/`, `/dossiers/documents/{id}/retirer/` |
| Partage temporaire | `/patients/{id}/dossier/partage/`, `/dossiers/partages/{id}/revoquer/` |
| Accès public en lecture seule (jeton) | `/dossier/partage/{uuid}/` |

API : `/api/v1/consultations/` (+ `?status=`, `?kind=`, `?patient=`, `?practitioner=`),
`…/{id}/complete/`, `…/{id}/cancel/`, `/api/v1/prescriptions/` (+ `?consultation=`),
`/api/v1/medical-records/` (+ `?patient=`, `?search=`),
`/api/v1/medical-documents/` (+ `?record=`, `?category=`) et
`/api/v1/medical-shares/` (`…/{id}/revoke/`).

Cloisonnement : la lecture est ouverte à `ADMIN`, `RECEPTION`, `DOCTOR` et `NURSE` ;
l'écriture des actes médicaux (consultation, ordonnance) est réservée à `DOCTOR`,
`NURSE` et `ADMIN`, celle du dossier (antécédents, pièces, partages) inclut
`RECEPTION`. `ACCOUNTANT` est exclu partout.

## 9. API mobile (jetons)

```bash
# Connexion
curl -X POST http://127.0.0.1:8765/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email": "dr.amine.benali@clinique-al-amal.ma", "password": "Madara2026!"}'

# Profil + clinique courante
curl http://127.0.0.1:8765/api/v1/me/ -H 'Authorization: Bearer <access>'

# Autre clinique
curl http://127.0.0.1:8765/api/v1/clinics/current/ \
  -H 'Authorization: Bearer <access>' -H 'X-Clinic: clinique-essaouira'
```

| Méthode | Route | Description |
|---------|-------|-------------|
| `POST` | `/api/v1/auth/login/` | jetons + profil + cliniques |
| `POST` | `/api/v1/auth/refresh/` | renouvellement du jeton d'accès |
| `POST` | `/api/v1/auth/logout/` | déconnexion (côté client) |
| `GET`/`PATCH` | `/api/v1/me/` | profil de l'utilisateur connecté |
| `POST` | `/api/v1/me/switch-clinic/` | change de clinique courante |
| `GET` | `/api/v1/clinics/` | cliniques accessibles |
| `GET` | `/api/v1/clinics/current/` | clinique courante |
| `GET` | `/api/v1/staff/` | annuaire du personnel |
| `GET` | `/api/v1/memberships/` | rôles par clinique |
| `GET`/`POST` | `/api/v1/opening-hours/` | horaires de travail |
| `GET`/`POST` | `/api/v1/blocked-slots/` | créneaux bloqués |
| `GET`/`POST` | `/api/v1/patients/` | dossiers patients (recherche `?q=`) |
| `GET`/`PATCH` | `/api/v1/patients/{id}/` | fiche patient |
| `GET` | `/api/v1/patients/search/?q=` | recherche rapide (10 résultats) |
| `GET` | `/api/v1/patients/statistics/` | compteurs de dossiers |
| `POST` | `/api/v1/patients/{id}/archive/` | archivage (administrateur) |
| `POST` | `/api/v1/patients/{id}/restore/` | restauration (administrateur) |
| `GET`/`POST` | `/api/v1/appointments/` | rendez-vous (voir § 7) |
| `GET`/`POST` | `/api/v1/consultations/` | consultations (voir § 8) |
| `GET`/`POST` | `/api/v1/prescriptions/` | ordonnances et leurs lignes |
| `GET`/`PATCH` | `/api/v1/medical-records/` | antécédents du dossier médical |
| `GET`/`POST` | `/api/v1/medical-documents/` | pièces jointes (multipart) |
| `GET`/`POST` | `/api/v1/medical-shares/` | liens de partage temporaires |

## 10. Données de démonstration

`python manage.py seed_demo` crée deux cliniques, leurs équipes, des horaires,
40 dossiers patients et 7 jours d'agenda par clinique (l'un des dossiers est archivé
pour illustrer le filtre ; l'agenda respecte les horaires, les conflits et affiche
des rendez-vous déjà terminés pour que la journée soit vivante). Il remplit ensuite
les dossiers médicaux (antécédents, pièces scannées, un partage), les consultations
clôturées adossées aux rendez-vous honorés et une partie de leurs ordonnances.
Options utiles : `--patients-per-clinic`, `--staff-per-clinic`, `--agenda-days`, `--flush`.

| Clinique | Slug | Comptes |
|----------|------|---------|
| Clinique Al Amal | `clinique-al-amal` | `admin@`, `reception@`, `comptable@`, `dr.*`, `inf.*@clinique-al-amal.ma` |
| Clinique Essaouira | `clinique-essaouira` | mêmes comptes, autres identifiants |

Mot de passe commun : `Madara2026!`. Administrators plateforme : `admin@madara.ma`.
`--flush` repart de zéro.

## 11. Tests et qualité

```bash
pytest                       # suite complète (base de test PostgreSQL dédiée)
ruff check .                 # lint
ruff format --check .        # formatage
python manage.py makemigrations --check --dry-run
python scripts/smoke_test.py # parcours HTTP de bout en bout (serveur démarré)
```

`scripts/smoke_test.py` (107 vérifications) vérifie la santé du service, la connexion
web et API, le cloisonnement entre cliniques, les refus par rôle, le parcours dossier
patient (création web, recherche, fiche, API CRUD, archivage refusé au médecin), l'agenda
(jour, semaine, salle d'attente, historique, cloisonnement par rôle, prise de rendez-vous
refusée sans créneau valide, API créneaux/statuts/transitions) et le jalon 4 (menu
consultations, dossier médical depuis la fiche patient, démarrage d'acte refusé à
l'accueil, API consultations/ordonnances/dossiers médicaux).

## 12. Structure du projet

```
apps/
  common/         modèles de base, tenancy, permissions, santé, seed
  accounts/       cliniques, utilisateurs, rôles, planning, auth web et API
  patients/       dossiers patients (jalon 2 — livré)
  appointments/   agenda et rendez-vous (jalon 3 — livré)
  consultations/  consultations, ordonnances (jalon 4 — livré)
  medical_records/ dossiers, pièces jointes et partages (jalon 4 — livré)
  notifications/  e-mails, WhatsApp, rappels (jalon 5)
  billing/        factures, règlements, impayés (jalon 6)
  inventory/      produits et stock (jalon 6)
  payroll/        salaires et primes (jalon 6)
  dashboard/      tableaux de bord (jalon 7)
config/           réglages, URL, WSGI/ASGI, Celery
templates/        gabarits HTMX
tests/            tests pytest
scripts/          outils (smoke test)
```

## 13. Jalons

1. Socle multi-clinique, rôles, authentification web/API, dashboard minimal — **terminé**
2. Dossiers patients — **terminé** (web + API, recherche, impression, archivage)
3. Agenda et rendez-vous — **terminé** (web + API, créneaux, conflits, salle d'attente)
4. Consultations et dossiers médicaux — **terminé** (web + API, salle d'attente → consultation → clôture, ordonnances, pièces jointes, partage de dossier)
5. Notifications et rappels
6. Facturation, stock et salaires
7. Tableaux de bord et reporting