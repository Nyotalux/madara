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
celery -A config beat -l info            # planificateur des rappels (jalon 5, optionnel)
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
automatiques sont décrits au § 11 (le champ `reminder_sent_at` est renseigné au
premier envoi).

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
| `GET` | `/api/v1/notifications/` | notifications de l'utilisateur connecté |
| `POST` | `/api/v1/notifications/{id}/read/` | marquer une notification lue |
| `POST` | `/api/v1/notifications/read_all/` | tout marquer comme lu |
| `GET`/`POST` | `/api/v1/invoices/` | factures (voir § 11) |
| `POST` | `/api/v1/invoices/{id}/pay/` | encaisser un règlement |
| `POST` | `/api/v1/invoices/{id}/cancel/` | annuler une facture |
| `GET`/`POST` | `/api/v1/payments/` | règlements (lecture) |

## 10. Notifications et rappels (jalon 5)

Chaque notification porte une file d'attente (canal, catégorie, heure d'envoi
souhaitée) et passe par `apps/notifications/services.py` : mise en file, envoi
dû, rappels de rendez-vous. `queue_notification` résout le destinataire (membre
du personnel ou patient) et ignore poliment un canal sans contact connu.

| Canal | Destinataire | Usage |
|-------|--------------|-------|
| `WHATSAPP` | patient | rappel J-1 (24 h avant le rendez-vous) |
| `EMAIL` | patient ou personnel | rappels J-1 / H-1 au patient, agenda du jour au praticien |
| `IN_APP` | personnel | casier du back-office (badge non lues dans l'en-tête) |
| Web Push | personnel | la notification système du navigateur, assise sur `IN_APP` |

| Règle | Comportement |
|-------|--------------|
| Planification | `APPOINTMENT_REMINDER_SCHEDULE` (réglage) : J-1 → WhatsApp + e-mail patient, H-1 → in-app praticien + e-mail patient |
| Réservation / déplacement | les rappels sont créés ou replanifiés par `book_appointment` / `reschedule` (jamais bloquant) |
| Annulation | les rappels en attente partent du file (`CANCELLED`), le créneau est libéré |
| Premier envoi | `reminder_sent_at` est renseigné ; un rendez-vous déjà rappelé n'est jamais re-rappelé |
| Filet de sécurité | toutes les 10 min, les rendez-vous proches sans rappel couvert reçoivent un message in-app + e-mail patient |
| Ancienneté | un échec d'envoi est conservé (`FAILED`, `last_error`) pour une nouvelle tentative |
| Lecture | seul le destinataire marque ses messages « lus » (individuel ou tout-lire) |
| Push navigateur | chaque message in-app livré est aussi poussé vers les navigateurs abonnés (Web Push, best effort) ; un endpoint mort (404/410) désactive la souscription |
| Cloisonnement | chaque notification appartient à la clinique concernée ; l'API et le back-office ne montrent que les siennes |

Fournisseurs (`.env`) : `WHATSAPP_PROVIDER` = `console` (journal, développement),
`meta` (WhatsApp Cloud API) ou `twilio` ; les e-mails partent via `EMAIL_BACKEND`
(console en développement, SMTP en production).

**Web Push (navigateur, personnel)** — service worker servi à la racine (`/sw.js`),
abonnement automatique à la première visite du back-office (permission demandée
une seule fois). Configuration :

```bash
python manage.py generate_vapid_keys   # colle WEBPUSH_VAPID_PRIVATE_KEY dans .env
```

Les notifications système fonctionnent sur Android et tous les navigateurs de
bureau ; sur iPhone/iPad, Safari ≥ 16.4 et l'installation du site sur l'écran
d'accueil. Sans HTTPS ni clés configurées, le site dégrade proprement vers le
badge in-app.

API d'abonnement : `POST/DELETE /api/v1/webpush/` et `/api/v1/webpush/cle/`.

## 11. Facturation (jalon 6)

La facture rattache un patient (et, le cas échéant, la consultation qui a
motivé l'acte) à des lignes de prestations. Les règles vivent dans
`apps/billing/services.py` ; les montants sont calculés à la lecture à partir
des lignes et du taux de TVA (celui de la clinique par défaut, modifiable).

| Règle | Comportement |
|-------|--------------|
| Référence | `FAC-AAAA-00001` par clinique |
| Cycle | brouillon → émise → (partiellement payée) → payée ; une facture payée est définitive |
| Émission | fixe l'échéance (30 jours par défaut) et verrouille les lignes |
| Règlement | partiel ou solde ; refuse tout dépassement du reste à payer (verrou pessimiste) |
| Annulation | réservée à l'administration et à la comptabilité ; impossible dès le premier règlement |
| Suppression | impossible (`DELETE` refusé) : l'historique comptable est conservé, on annule |
| Impayés | factures émises/partielles au-delà de l'échéance (`due_at`) |
| Relance | tâche Celery à 09:00 : WhatsApp puis e-mail au patient (1er canal disponible), synthèse in-app à l'administration et à la comptabilité ; cadence pilotée par `clinic.dunning_days` puis tous les 7 jours |
| Périmètre | lecture/écriture : ADMIN, RECEPTION, ACCOUNTANT — les praticiens et infirmiers n'ont pas accès à la facturation |

| Écran | Route |
|-------|-------|
| Liste filtrable + compteurs | `/factures/` |
| Fiche (lignes, règlements, actions) | `/factures/{id}/` |
| Création / modification de brouillon | `/factures/nouveau/`, `/factures/{id}/modifier/` |
| Émission / annulation | `/factures/{id}/emettre/`, `/factures/{id}/annuler/` |
| Encaissement / retrait d'un règlement | `/factures/{id}/paiement/`, `/factures/{id}/paiement/{id}/retirer/` |
| Version imprimable | `/factures/{id}/imprimer/` |

API : `/api/v1/invoices/` (+ `?status=`, `?q=`, `?overdue=1`), `…/{id}/pay/`,
`…/{id}/cancel/`, `…/{id}/payments/`, `/api/v1/payments/` (+ `?invoice=`). La
création accepte les lignes imbriquées et l'émission en un appel (`emit: true`).

| Écran | Route |
|-------|-------|
| Casier du personnel (filtres, pagination) | `/notifications/` |
| Marquer une notification lue | `/notifications/{id}/lue/` |
| Tout marquer comme lu | `/notifications/tout-lire/` |

Tâches Celery (cadenas dans `config/celery.py`) : `notifications.dispatch_pending`
(toutes les 2 min), `appointments.tasks.send_daily_agenda` (07:00),
`appointments.tasks.send_upcoming_reminders` (toutes les 10 min).

API : `/api/v1/notifications/` (+ `?channel=`, `?category=`, `?status=`, `?unread=1`),
`…/{id}/read/`, `…/read_all/`.

## 11. Données de démonstration

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

## 12. Tests et qualité

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

Suite pytest : `tests/test_notifications.py` (jalon 5 : file d'attente, canaux,
dispatch, rappels, écran web et API, cloisonnement), `tests/test_webpush.py`
(push navigateur) et `tests/test_billing.py` (jalon 6 : facturation, montants,
paiements, impayés, relances, rôles, web et API).

## 13. Structure du projet

```
apps/
  common/         modèles de base, tenancy, permissions, santé, seed
  accounts/       cliniques, utilisateurs, rôles, planning, auth web et API
  patients/       dossiers patients (jalon 2 — livré)
  appointments/   agenda et rendez-vous (jalon 3 — livré)
  consultations/  consultations, ordonnances (jalon 4 — livré)
  medical_records/ dossiers, pièces jointes et partages (jalon 4 — livré)
  notifications/  e-mails, WhatsApp, rappels (jalon 5 — livré)
  billing/        factures, règlements, impayés (jalon 6 — facturation livrée)
  inventory/      produits et stock (jalon 6)
  payroll/        salaires et primes (jalon 6)
  dashboard/      tableaux de bord (jalon 7)
config/           réglages, URL, WSGI/ASGI, Celery
templates/        gabarits HTMX
tests/            tests pytest
scripts/          outils (smoke test)
```

## 14. Jalons

1. Socle multi-clinique, rôles, authentification web/API, dashboard minimal — **terminé**
2. Dossiers patients — **terminé** (web + API, recherche, impression, archivage)
3. Agenda et rendez-vous — **terminé** (web + API, créneaux, conflits, salle d'attente)
4. Consultations et dossiers médicaux — **terminé** (web + API, salle d'attente → consultation → clôture, ordonnances, pièces jointes, partage de dossier)
5. Notifications et rappels — **terminé** (web + API, file d'attente, WhatsApp/e-mail patient, in-app praticien, agenda du jour, tâches Celery)
6. Facturation, stock et salaires — **partiel** (facturation livrée : web + API, règlements, impayés, relances ; stock et salaires à venir)
7. Tableaux de bord et reporting