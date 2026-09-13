"""Artefatti di evidenza: manifest firmato e audit log a catena di hash.

Questi moduli non rendono nessuno conforme ad alcunché. Producono gli artefatti
che una valutazione di conformità richiede: provenienza dei dati (art. 10 AI Act),
registrazione degli eventi (art. 12), marcatura del contenuto generato (art. 50).
La qualificazione giuridica resta interamente all'utente.
"""

from anton_ocr.compliance.audit import AuditLog
from anton_ocr.compliance.manifest import Manifest, SigningKey

__all__ = ["AuditLog", "Manifest", "SigningKey"]
