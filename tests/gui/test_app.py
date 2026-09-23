async def test_the_page_is_served_at_the_root(client):
    response = await client.get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "Market Oracle" in response.text


async def test_static_modules_are_served_and_do_not_shadow_the_api(client):
    assert (await client.get("/static/app.js")).status_code == 200
    response = await client.get("/api/status")
    assert response.headers["content-type"].startswith("application/json")
