import asyncio


async def run_job(client, path, **params) -> dict:
    """Start a job and wait for it, as the page does by polling; returns the final job."""
    response = await client.post(path, params=params or None)
    job = response.json()
    assert response.status_code == 202, job
    while job["state"] in ("queued", "running"):
        await asyncio.sleep(0.01)
        job = (await client.get(f"/api/activity/{job['id']}")).json()
    return job
