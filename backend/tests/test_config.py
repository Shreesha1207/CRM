import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:secret@db.example.com:5432/railway",
        "postgres://postgres:secret@db.example.com:5432/railway",
    ],
)
def test_plain_postgres_urls_use_the_installed_driver(url: str) -> None:
    assert Settings(database_url=url).database_url == "postgresql+psycopg://postgres:secret@db.example.com:5432/railway"


def test_a_url_that_names_its_driver_is_kept() -> None:
    url = "postgresql+psycopg://booking:booking@localhost:5432/booking"
    assert Settings(database_url=url).database_url == url
