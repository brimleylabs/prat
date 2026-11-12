from __future__ import annotations

import pytest

from prat.changes import (
    BUILTIN_CATEGORIES,
    ChangedPath,
    ChangeSet,
    Risk,
    builtin_categories,
    classify,
    normalise_path,
)
from prat.projects import ChangeRule, Changes, parse_spec


def categories(path: str, *, binary: bool = False) -> set[str]:
    found, _reasons = builtin_categories(path, binary=binary)
    return set(found)


def one(path: str, status: str = "M", **kwargs: object) -> ChangeSet:
    return classify([ChangedPath(path, status)], **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # security
        ("src/app/auth.py", {"security"}),
        ("app/Http/Middleware/Authenticate.php", {"security"}),
        ("src/prat/policy/engine.py", {"security"}),
        ("src/prat/web/sessions.py", {"security"}),
        ("lib/token_store.go", {"security"}),
        ("internal/crypto/sign.go", {"security"}),
        ("app/permissions/roles.rb", {"security"}),
        ("src/acl/rules.ts", {"security"}),
        # dependencies
        ("pyproject.toml", {"dependencies"}),
        ("requirements-dev.txt", {"dependencies"}),
        ("requirements/base.txt", {"dependencies"}),
        ("uv.lock", {"dependencies"}),
        ("web/package.json", {"dependencies"}),
        ("web/pnpm-lock.yaml", {"dependencies"}),
        ("composer.json", {"dependencies"}),
        ("composer.lock", {"dependencies"}),
        ("go.mod", {"dependencies"}),
        ("go.sum", {"dependencies"}),
        ("Cargo.toml", {"dependencies"}),
        ("Cargo.lock", {"dependencies"}),
        ("Gemfile", {"dependencies"}),
        ("Gemfile.lock", {"dependencies"}),
        # CI and deploy
        (".github/workflows/ci.yml", {"ci_config"}),
        (".gitlab-ci.yml", {"ci_config"}),
        (".prat/project.toml", {"ci_config"}),
        ("deploy/production.yaml", {"ci_config"}),
        ("Jenkinsfile", {"ci_config"}),
        # schema
        ("migrations/0007_add_users.py", {"schema"}),
        ("src/prat/db/alembic/versions/0003_self_improvement.py", {"schema"}),
        ("db/schema.rb", {"schema"}),
        ("prisma/schema.prisma", {"schema"}),
        ("sql/report.sql", {"schema"}),
        # configuration
        ("config/database.yaml", {"configuration"}),
        ("settings.py", {"configuration"}),
        (".env.example", {"configuration"}),
        ("Dockerfile", {"configuration"}),
        ("docker-compose.yml", {"configuration"}),
        ("infra/main.tf", {"configuration"}),
        (".gitignore", {"configuration"}),
        # tests
        ("tests/unit/test_paths.py", {"tests"}),
        ("src/pkg/widget_test.go", {"tests"}),
        ("web/src/App.test.tsx", {"tests"}),
        ("tests/Feature/OrderTest.php", {"tests"}),
        ("spec/models/order_spec.rb", {"tests"}),
        ("tests/conftest.py", {"tests"}),
        # docs
        ("README.md", {"docs"}),
        ("docs/ARCHITECTURE.md", {"docs"}),
        ("docs/images/board.png", {"docs"}),
        ("LICENSE", {"docs"}),
        ("AUTHORS", {"docs"}),
        # code
        ("src/prat/store.py", {"code"}),
        ("web/src/App.tsx", {"code"}),
        ("src/prat/web/templates/board.html", {"code"}),
        ("src/prat/web/static/app.css", {"code"}),
        ("src/app/authors.py", {"code"}),
        # unknown
        ("data/blob.bin", {"unknown"}),
        ("notes.xyz", {"unknown"}),
        ("Thing", {"unknown"}),
    ],
)
def test_builtin_categories(path: str, expected: set[str]) -> None:
    assert categories(path) == expected


def test_every_builtin_category_has_an_example() -> None:
    examples = {
        "security": "src/auth/login.py",
        "dependencies": "poetry.lock",
        "ci_config": ".github/workflows/ci.yml",
        "schema": "migrations/001.sql",
        "configuration": "config/app.toml",
        "tests": "tests/test_x.py",
        "docs": "docs/guide.md",
        "code": "src/main.py",
        "unknown": "payload.dat",
    }
    assert set(examples) == set(BUILTIN_CATEGORIES)
    for category, path in examples.items():
        assert category in categories(path), path


def test_a_file_can_have_several_categories() -> None:
    assert categories("tests/unit/test_policy.py") == {"tests", "security"}
    assert categories("migrations/0004_session_tokens.py") == {"schema", "security"}
    assert categories("config/security.yaml") == {"security", "configuration"}
    assert categories("AGENTS.md") == {"docs", "configuration"}
    assert categories("docs/CLAUDE.md") == {"docs", "configuration"}


def test_binary_files_are_unknown_unless_they_are_known_media() -> None:
    assert categories("src/prat/web/static/logo.png", binary=True) == {"code"}
    assert categories("docs/screenshot.png", binary=True) == {"docs"}
    assert categories("src/module.py", binary=True) == {"unknown"}
    assert categories("tools/helper.exe", binary=True) == {"unknown"}
    assert "unknown" in categories("src/auth/keys.dat", binary=True)


def test_builtin_risk_levels() -> None:
    assert one("docs/guide.md").risk is Risk.LOW
    assert one("tests/test_x.py").risk is Risk.LOW
    assert one("src/main.py").risk is Risk.MEDIUM
    assert one("config/app.toml").risk is Risk.MEDIUM
    for path in ("src/auth.py", "uv.lock", ".github/workflows/ci.yml", "migrations/1.sql", "x.dat"):
        assert one(path).risk is Risk.HIGH, path


def test_overall_risk_is_the_highest_of_the_files() -> None:
    change = classify(
        [ChangedPath("README.md"), ChangedPath("src/main.py"), ChangedPath("src/prat/policy/x.py")]
    )
    assert change.risk is Risk.HIGH
    assert change.categories == {"code": Risk.MEDIUM, "docs": Risk.LOW, "security": Risk.HIGH}
    assert classify([]).risk is Risk.LOW


@pytest.mark.parametrize(
    "variant",
    [
        "src/prat/policy/engine.py",
        "src\\PRAT\\policy\\engine.py",
        "SRC/PRAT/POLICY/Engine.PY",
        ".\\Src\\PRAT\\Policy\\ENGINE.py",
        "./src//PRAT/policy/engine.py",
    ],
)
def test_separator_and_case_do_not_change_the_result(variant: str) -> None:
    assert normalise_path(variant) == "src/prat/policy/engine.py"
    assert categories(variant) == {"security"}
    rules = Changes(rule=[ChangeRule(paths=["src/prat/policy/**"], category="billing")])
    assert set(one(variant, changes=rules).files[0].categories) == {"security", "billing"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("PYPROJECT.TOML", "dependencies"),
        (".GitHub\\Workflows\\CI.yml", "ci_config"),
        ("Tests\\Unit\\Test_Paths.py", "tests"),
        ("DB\\Migrations\\001_Init.SQL", "schema"),
        ("Docs\\Guide.MD", "docs"),
    ],
)
def test_builtin_matching_is_case_and_separator_insensitive(path: str, expected: str) -> None:
    assert expected in categories(path)


def test_project_rule_adds_a_category() -> None:
    rules = Changes(rule=[ChangeRule(paths=["billing/**"], category="payments", risk="high")])
    change = one("billing/invoice.py", changes=rules)
    assert change.files[0].categories == {"code": Risk.MEDIUM, "payments": Risk.HIGH}
    assert change.risk is Risk.HIGH
    assert any("project rule" in reason for reason in change.files[0].reasons)
    # A path the rule does not match is untouched.
    assert set(one("src/main.py", changes=rules).files[0].categories) == {"code"}


def test_project_rule_raises_a_risk() -> None:
    rules = Changes(rule=[ChangeRule(paths=["docs/legal/**"], category="docs", risk="high")])
    assert one("docs/legal/terms.md", changes=rules).files[0].categories == {"docs": Risk.HIGH}
    assert one("docs/guide.md", changes=rules).files[0].categories == {"docs": Risk.LOW}


def test_project_rule_cannot_lower_a_risk_or_remove_a_category() -> None:
    rules = Changes(
        rule=[
            ChangeRule(paths=["**"], category="security", risk="low"),
            ChangeRule(paths=["src/auth/**"], category="docs", risk="low"),
            ChangeRule(paths=["uv.lock"], category="dependencies", risk="low"),
        ]
    )
    auth = one("src/auth/login.py", changes=rules).files[0]
    assert auth.categories["security"] is Risk.HIGH
    assert auth.categories["docs"] is Risk.LOW
    assert auth.risk is Risk.HIGH
    assert one("uv.lock", changes=rules).files[0].categories["dependencies"] is Risk.HIGH
    # Naming a built-in category for an unrelated file gives at least its built-in risk.
    assert one("src/main.py", changes=rules).files[0].categories["security"] is Risk.HIGH


def test_require_person_lists_only_categories_present() -> None:
    wanted = Changes(require_person=["security", "dependencies", "schema"])
    change = classify([ChangedPath("src/auth.py"), ChangedPath("README.md")], changes=wanted)
    assert change.require_person == ["security"]
    assert change.needs_person
    assert any("person" in flag for flag in change.flags)
    quiet = classify([ChangedPath("README.md")], changes=wanted)
    assert quiet.require_person == []
    assert not quiet.needs_person
    assert not one("src/auth.py").needs_person  # empty by default


def test_require_person_sees_categories_added_by_rules() -> None:
    wanted = Changes(
        rule=[ChangeRule(paths=["billing/**"], category="payments")],
        require_person=["payments"],
    )
    assert one("Billing\\Invoice.py", changes=wanted).require_person == ["payments"]


def test_deleted_test_file_is_flagged() -> None:
    change = classify(
        [ChangedPath("tests/test_orders.py", "D", deleted=40), ChangedPath("src/orders.py")]
    )
    assert change.tests_deleted == ["tests/test_orders.py"]
    assert change.tests_reduced
    assert change.files[0].categories["tests"] is Risk.HIGH
    assert change.risk is Risk.HIGH
    assert any("deleted" in flag for flag in change.flags)
    kept = classify([ChangedPath("tests/test_orders.py", "M")])
    assert not kept.tests_reduced
    assert kept.flags == []


DIFF_FEWER_TESTS = """\
diff --git a/tests/test_orders.py b/tests/test_orders.py
index 1111111..2222222 100644
--- a/tests/test_orders.py
+++ b/tests/test_orders.py
@@ -1,12 +1,6 @@
 import orders

-def test_total():
-    assert orders.total([1, 2]) == 3
-
-async def test_refund():
-    assert await orders.refund(1)
-
+def test_everything():
+    assert True
diff --git a/src/orders.py b/src/orders.py
--- a/src/orders.py
+++ b/src/orders.py
@@ -1,2 +1,3 @@
+def test_helper():
+    return 1
-def test_old_helper():
"""


def test_dropping_test_functions_is_flagged() -> None:
    files = [ChangedPath("tests/test_orders.py", "M", 2, 6), ChangedPath("src/orders.py", "M")]
    change = classify(files, diff=DIFF_FEWER_TESTS)
    # Only definitions inside test files count.
    assert (change.test_functions_added, change.test_functions_removed) == (1, 2)
    assert change.tests_reduced
    assert change.files[0].categories["tests"] is Risk.HIGH
    assert any("test functions dropped" in flag for flag in change.flags)


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("tests/test_a.py", "    def test_method(self):"),
        ("web/src/App.test.ts", "  it('renders the board', () => {"),
        ("web/src/App.test.ts", 'test("adds a card", async () => {'),
        ("pkg/widget_test.go", "func TestWidget(t *testing.T) {"),
        ("src/test/java/OrderTest.java", "    @Test"),
        ("tests/Feature/OrderTest.php", "    public function testTotal(): void"),
        ("spec/order_spec.rb", "  it 'adds up' do"),
        ("tests/order_test.rs", "#[test]"),
    ],
)
def test_test_definitions_are_counted_across_languages(path: str, line: str) -> None:
    diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,3 +1,2 @@\n-{line}\n"
    change = classify([ChangedPath(path, "M", 0, 1)], diff=diff)
    assert change.test_functions_removed == 1, line
    assert change.tests_reduced


def test_adding_tests_is_not_flagged() -> None:
    path = "tests/test_new.py"
    diff = (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        "@@ -0,0 +1,2 @@\n+def test_new():\n+    assert True\n"
    )
    change = classify([ChangedPath(path, "A", 2, 0)], diff=diff)
    assert change.test_functions_added == 1
    assert not change.tests_reduced
    assert change.risk is Risk.LOW


def test_rename_keeps_the_categories_of_the_old_path() -> None:
    change = classify([ChangedPath("src/helpers.py", "R", old_path="src/auth/helpers.py")])
    assert set(change.files[0].categories) == {"code", "security"}


def test_change_set_round_trips_through_json() -> None:
    wanted = Changes(
        rule=[ChangeRule(paths=["billing/**"], category="payments", risk="high")],
        require_person=["payments"],
    )
    change = classify(
        [
            ChangedPath("billing/invoice.py", "A", 10, 0),
            ChangedPath("tests/test_old.py", "D", 0, 9),
            ChangedPath("assets/logo.bin", "A", binary=True),
        ],
        changes=wanted,
        base="a" * 40,
        head="b" * 40,
    )
    data = change.to_dict()
    assert data["risk"] == "high"
    assert data["head"] == "b" * 40
    assert data["require_person"] == ["payments"]
    assert data["tests_reduced"] is True
    again = ChangeSet.from_dict(data)
    assert again == change
    assert again.to_dict() == data


def test_project_file_changes_section() -> None:
    spec = parse_spec(
        """
name = "x"

[[changes.rule]]
paths = ["billing/**", "app\\\\Payments\\\\**"]
category = "security"
risk = "high"

[changes]
require_person = ["security", "dependencies"]
"""
    )
    assert spec.changes.rule[0].paths == ["billing/**", "app/Payments/**"]
    assert spec.changes.require_person == ["security", "dependencies"]
    assert one("APP/payments/Charge.php", changes=spec.changes).require_person == ["security"]
    assert parse_spec('name = "x"\n').changes == Changes()
