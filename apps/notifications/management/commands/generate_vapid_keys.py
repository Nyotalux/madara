"""Génération de clés VAPID pour le Web Push (jalon 5).

```bash
python manage.py generate_vapid_keys
# WEBPUSH_VAPID_PRIVATE_KEY=… (à coller dans .env)
```

La clé publique du couple est dérivée automatiquement côté serveur ;
le navigateur la récupère via ``/api/v1/webpush/cle/`` ou ``/sw.js``.
"""

from __future__ import annotations

from base64 import urlsafe_b64encode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Génère un couple de clés VAPID (base64url) pour le Web Push."

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        private = key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        public = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        private_b64 = urlsafe_b64encode(private).rstrip(b"=").decode()
        public_b64 = urlsafe_b64encode(public).rstrip(b"=").decode()
        self.stdout.write("Collez la clé privée dans votre .env :")
        self.stdout.write(f"WEBPUSH_VAPID_PRIVATE_KEY={private_b64}")
        self.stdout.write(self.style.NOTICE(f"Clé publique (dérivée) : {public_b64}"))
