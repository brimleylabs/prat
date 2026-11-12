"""Adding a project from a repository link."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import git, make_repo
from prat import onboard


@pytest.mark.parametrize(
    ("value", "link"),
    [
        ("https://github.com/acme/shop.git", True),
        ("git@github.com:acme/shop.git", True),
        ("ssh://git@host/acme/shop", True),
        ("file:///srv/repos/shop", True),
        ("/home/me/code/shop", False),
        ("C:/code/shop", False),
        ("ext::sh -c touch% /tmp/x", False),
        ("--upload-pack=touch x", False),
    ],
)
def test_links_and_paths_are_told_apart(value: str, link: bool) -> None:
    assert onboard.is_link(value) is link


def test_a_link_can_never_be_read_as_an_option() -> None:
    with pytest.raises(onboard.OnboardError):
        onboard.check_link("-u https://example.com/x")


@pytest.mark.parametrize(
    ("link", "slug"),
    [
        ("https://github.com/acme/Shop-Front.git", "shop-front"),
        ("git@github.com:acme/api", "api"),
        ("https://github.com/acme/thing/", "thing"),
    ],
)
def test_the_short_name_comes_from_the_repository(link: str, slug: str) -> None:
    assert onboard.slug_for(link) == slug


@pytest.mark.integration
def test_branches_are_listed_and_the_chosen_one_is_cloned(tmp_path: Path) -> None:
    origin = make_repo(tmp_path, "origin", files={"app.py": "print('main')\n"})
    git(origin, "checkout", "-q", "-b", "develop")
    (origin / "app.py").write_text("print('develop')\n", encoding="utf-8", newline="\n")
    git(origin, "commit", "-q", "-am", "develop work")
    git(origin, "checkout", "-q", "main")
    link = origin.resolve().as_uri()

    found = onboard.list_branches(link)
    assert found.default == "main" and found.names == ["main", "develop"]

    clone = onboard.clone(link, "develop", tmp_path / "projects" / "shop")
    assert (clone / "app.py").read_text(encoding="utf-8") == "print('develop')\n"
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "develop"
    with pytest.raises(onboard.OnboardError, match="already exists"):
        onboard.clone(link, "main", clone)
    with pytest.raises(onboard.OnboardError):
        onboard.list_branches((tmp_path / "missing").resolve().as_uri())
