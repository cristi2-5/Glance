"""Teste pentru scheletul de job-uri: upload copertă, polling, izolare pe proprietar."""

from httpx import AsyncClient

EMAIL_A = "cititor.a@example.com"
EMAIL_B = "cititor.b@example.com"
PAROLA = "parola-super-secreta"

O_IMAGINE_MICA = b"\xff\xd8\xff\xe0" + b"continut-fals-de-jpeg" * 10


async def _inregistreaza_si_autentifica(client: AsyncClient, email: str) -> str:
    await client.post("/auth/register", json={"email": email, "password": PAROLA})
    response = await client.post("/auth/login", json={"email": email, "password": PAROLA})
    access_token: str = response.json()["access_token"]
    return access_token


def _antet(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_analyze_cover_creeaza_job_care_ajunge_done(client: AsyncClient) -> None:
    token = await _inregistreaza_si_autentifica(client, EMAIL_A)

    creare = await client.post(
        "/books/analyze-cover",
        headers=_antet(token),
        files={"file": ("cover.jpg", O_IMAGINE_MICA, "image/jpeg")},
    )
    assert creare.status_code == 202
    job_id = creare.json()["job_id"]

    citire = await client.get(f"/jobs/{job_id}", headers=_antet(token))
    assert citire.status_code == 200
    body = citire.json()
    assert body["status"] == "done"
    assert body["result"]["dimensiune_imagine_bytes"] == len(O_IMAGINE_MICA)
    assert body["error"] is None


async def test_analyze_cover_respinge_tip_de_fisier_nesuportat(client: AsyncClient) -> None:
    token = await _inregistreaza_si_autentifica(client, EMAIL_A)

    response = await client.post(
        "/books/analyze-cover",
        headers=_antet(token),
        files={"file": ("cover.txt", b"nu sunt o imagine", "text/plain")},
    )

    assert response.status_code == 415


async def test_analyze_cover_respinge_fisier_prea_mare(client: AsyncClient) -> None:
    token = await _inregistreaza_si_autentifica(client, EMAIL_A)
    imagine_uriasa = b"\xff" * (8 * 1024 * 1024 + 1)

    response = await client.post(
        "/books/analyze-cover",
        headers=_antet(token),
        files={"file": ("cover.jpg", imagine_uriasa, "image/jpeg")},
    )

    assert response.status_code == 413


async def test_analyze_cover_fara_autentificare_este_respins(client: AsyncClient) -> None:
    response = await client.post(
        "/books/analyze-cover",
        files={"file": ("cover.jpg", O_IMAGINE_MICA, "image/jpeg")},
    )

    assert response.status_code == 401


async def test_get_job_inexistent_intoarce_404(client: AsyncClient) -> None:
    token = await _inregistreaza_si_autentifica(client, EMAIL_A)

    response = await client.get("/jobs/999", headers=_antet(token))

    assert response.status_code == 404


async def test_get_job_fara_autentificare_este_respins(client: AsyncClient) -> None:
    response = await client.get("/jobs/1")

    assert response.status_code == 401


async def test_get_job_al_altui_utilizator_este_interzis(client: AsyncClient) -> None:
    token_a = await _inregistreaza_si_autentifica(client, EMAIL_A)
    token_b = await _inregistreaza_si_autentifica(client, EMAIL_B)

    creare = await client.post(
        "/books/analyze-cover",
        headers=_antet(token_a),
        files={"file": ("cover.jpg", O_IMAGINE_MICA, "image/jpeg")},
    )
    job_id = creare.json()["job_id"]

    citire_straina = await client.get(f"/jobs/{job_id}", headers=_antet(token_b))

    assert citire_straina.status_code == 403
