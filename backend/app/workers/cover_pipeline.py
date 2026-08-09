"""Pipeline-ul complet declanșat la încărcarea unei coperți.

Modulul 2 conține doar scheletul asincron: starea unui job trece prin
`pending` → `running` → `done`, cu un rezultat placeholder. Modulele 3-5
(vision, data fetcher, RAG) vor înlocui corpul funcției `proceseaza_coperta`
cu OCR real, fetch de metadate și sinteză, păstrând aceeași semnătură.
"""

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.job import Job, JobStatus

logger = structlog.get_logger(__name__)


async def proceseaza_coperta(
    job_id: int,
    continut_imagine: bytes,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Rulează pipeline-ul de analiză a unei coperți și actualizează starea job-ului.

    Deschide propria sesiune de bază de date din `session_factory` — sesiunea
    request-ului care a creat job-ul s-a închis deja când task-ul de fundal
    pornește.

    Args:
        job_id: Id-ul job-ului de actualizat.
        continut_imagine: Conținutul brut al imaginii încărcate.
        session_factory: Fabrica de sesiuni de folosit (permite testelor să
            injecteze o bază de date izolată în loc de cea de producție).
    """
    async with session_factory() as db:
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("job_negasit_in_pipeline", job_id=job_id)
            return

        job.status = JobStatus.RUNNING.value
        await db.commit()

        job.result = {
            "mesaj": "Pipeline placeholder — Modulul 3 adaugă OCR și vision reale.",
            "dimensiune_imagine_bytes": len(continut_imagine),
        }
        job.status = JobStatus.DONE.value
        await db.commit()
