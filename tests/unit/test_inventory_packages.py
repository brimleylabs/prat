"""Only the project's own ecosystems become dependency knowledge."""

from __future__ import annotations

from prat.knowledge.inventory import FileInfo, own_ecosystems, own_packages


def info(path: str) -> FileInfo:
    return FileInfo(path=path, kind="config", language="", bytes=10, lines=1)


def test_packages_found_inside_installed_packages_are_not_the_projects() -> None:
    files = [
        info("package.json"),
        info("server/requirements.txt"),
        info(".github/workflows/ci.yml"),
        info("vendor/thing/Package.swift"),
    ]
    ecosystems = own_ecosystems(files)
    assert ecosystems == {"npm", "python", "github-action"}
    packages = [
        {
            "name": "expo",
            "version": "58.0.0",
            "type": "npm",
            "where": "/node_modules/expo/package.json",
        },
        {
            "name": "pillow",
            "version": "13.0.0",
            "type": "python",
            "where": "/server/.venv/lib/site-packages/pillow",
        },
        {
            "name": "folly",
            "version": "1.0",
            "type": "dotnet",
            "where": "/node_modules/react-native-windows/x.csproj",
        },
        {
            "name": "swift-syntax",
            "version": "5.9",
            "type": "swift",
            "where": "/node_modules/expo/Package.resolved",
        },
        {
            "name": "actions/checkout",
            "version": "v4",
            "type": "github-action",
            "where": "/.github/workflows/ci.yml",
        },
        {
            "name": "actions/setup-node",
            "version": "v3",
            "type": "github-action",
            "where": "/node_modules/x/.github/workflows/a.yml",
        },
    ]
    kept = [p["name"] for p in own_packages(packages, ecosystems)]
    assert kept == ["expo", "pillow", "actions/checkout"]
