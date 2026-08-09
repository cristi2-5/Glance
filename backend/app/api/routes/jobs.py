"""Rută pentru interogarea stării unui job asincron."""

from fastapi import APIRouter

from app.api.deps import DbSession, UtilizatorCurent
from app.core.exceptions import AcccesInterzis, ResursaNegasita
from app.models.job import Job
from app.schemas.job import JobPublic

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobPublic)
async def citeste_job(job_id: int, db: DbSession, utilizator_curent: UtilizatorCurent) -> Job:
    """Returnează starea curentă a unui job, dacă aparține utilizatorului curent.

    Args:
        job_id: Identificatorul job-ului cerut.
        db: Sesiunea de bază de date curentă.
        utilizator_curent: Utilizatorul autentificat.

    Returns:
        Starea job-ului (`pending`, `running`, `done` sau `failed`), plus
        rezultatul sau eroarea, dacă sunt disponibile.

    Raises:
        ResursaNegasita: Dacă nu există niciun job cu acest id.
        AcccesInterzis: Dacă job-ul aparține altui utilizator.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise ResursaNegasita("Job-ul nu a fost găsit.")
    if job.user_id != utilizator_curent.id:
        raise AcccesInterzis("Nu ai acces la acest job.")
    return job
