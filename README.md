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

Chaque modèle métier hérite de `BaseModel` : horodatage, auteur, clinique et
archivage logique. Les managers `objects` filtrent automatiquement sur la clinique
courante ; `all_objects` sert aux vues transverses (administration plateforme,
tâches de fond). Un compte plateforme (`is_platform_staff`) contourne ce filtrage.

Sélection de la clinique courante :

- **web** : cookie de session (`madara_clinic_id`), modifiable depuis l'en-tête ;
- **API** : en-tête `X-Clinic` (slug ou identifiant), sinon la clinique par défaut.

## 6. API mobile (jetons)

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

## 7. Données de démonstration

`python manage.py seed_demo` crée deux cliniques, leurs équipes et des horaires.

| Clinique | Slug | Comptes |
|----------|------|---------|
| Clinique Al Amal | `clinique-al-amal` | `admin@`, `reception@`, `comptable@`, `dr.*`, `inf.*@clinique-al-amal.ma` |
| Clinique Essaouira | `clinique-essaouira` | mêmes comptes, autres identifiants |

Mot de passe commun : `Madara2026!`. Administrators plateforme : `admin@madara.ma`.
`--flush` repart de zéro.

## 8. Tests et qualité

```bash
pytest                       # suite complète (base de test PostgreSQL dédiée)
ruff check .                 # lint
ruff format --check .        # formatage
python manage.py makemigrations --check --dry-run
python scripts/smoke_test.py # parcours HTTP de bout en bout (serveur démarré)
```

`scripts/smoke_test.py` vérifie la santé du service, la connexion web et API, le
cloisonnement entre cliniques et les refus par rôle.

## 9. Structure du projet

```
apps/
  common/         modèles de base, tenancy, permissions, santé, seed
  accounts/       cliniques, utilisateurs, rôles, planning, auth web et API
  patients/       dossiers patients (jalon 2)
  appointments/   agenda et rendez-vous (jalon 3)
  consultations/  motifs d'admission (jalon 4)
  medical_records/ dossiers et documents médicaux (jalon 4)
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

## 10. Jalons

1. Socle multi-clinique, rôles, authentification web/API, dashboard minimal — **en cours**
2. Dossiers patients
3. Agenda et rendez-vous
4. Consultations et dossiers médicaux
5. Notifications et rappels
6. Facturation, stock et salaires
7. Tableaux de bord et reporting