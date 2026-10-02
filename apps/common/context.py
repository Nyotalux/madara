"""Variables de contexte partagées (clinique courante, utilisateur courant).

Utilisées par les managers de modèles, les middleware et les tâches Celery pour
savoir dans quel périmètre travailler.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

current_clinic: ContextVar = ContextVar("madara_current_clinic", default=None)
current_user: ContextVar = ContextVar("madara_current_user", default=None)


@contextmanager
def clinic_context(clinic):
    """Execute un bloc dans le périmètre d'une clinique donnée."""
    token = current_clinic.set(clinic)
    try:
        yield clinic
    finally:
        current_clinic.reset(token)


@contextmanager
def user_context(user):
    token = current_user.set(user)
    try:
        yield user
    finally:
        current_user.reset(token)
