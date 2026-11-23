"""A page judged as a document, without a browser (prat.pagecheck)."""

from __future__ import annotations

from typing import Any

from prat import pagecheck, screens, standards


def clean(**changes: Any) -> dict[str, Any]:
    """What the page script returns for a page with nothing wrong, with ``changes`` on top."""
    facts: dict[str, Any] = {
        "url": "https://shop.example/tents",
        "title": "Tents",
        "title_count": 1,
        "lang": "en",
        "description": ["Every tent we sell."],
        "canonical": ["https://shop.example/tents"],
        "robots": "",
        "viewport": "width=device-width, initial-scale=1",
        "zoom_blocked": False,
        "icon": True,
        "og": {
            "title": "Tents",
            "image": "https://shop.example/t.png",
            "type": "website",
            "url": "https://shop.example/tents",
        },
        "hreflang": ["en", "en-GB", "x-default"],
        "h1": ["Tents"],
        "headings": [1, 2, 3, 2],
        "main": 1,
        "images": {"total": 2, "no_alt": [], "no_size": [], "eager_below": []},
        "jsonld": {"blocks": 1, "bad": 0, "types": ["Product"], "dates": []},
        "time_tags": 0,
        "text_chars": 2000,
        "dom_nodes": 300,
        "perf": {
            "lcp_ms": 900,
            "cls": 0.01,
            "fcp_ms": 500,
            "ttfb_ms": 80,
            "tbt_ms": 20,
            "requests": 12,
            "script_bytes": 90_000,
            "style_bytes": 20_000,
            "image_bytes": 150_000,
            "font_bytes": 30_000,
            "total_bytes": 300_000,
            "protocols": ["h2"],
        },
        "fetched": {
            "status": 200,
            "x_robots": "",
            "raw_chars": 1900,
            "robots_txt": "User-agent: *\nDisallow: /admin\nSitemap: https://shop.example/sitemap.xml\n"
            + "".join(f"User-agent: {a}\nAllow: /\n" for a in pagecheck.AI_TRAINING_AGENTS),
            "uncompressed": [],
            "uncached": [],
        },
    }
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(facts.get(key), dict):
            facts[key] = {**facts[key], **value}
        else:
            facts[key] = value
    return facts


def rules(found: list[screens.Fault]) -> set[str]:
    return {fault.rule for fault in found}


def test_a_page_with_nothing_wrong_has_no_faults() -> None:
    assert pagecheck.faults(clean()) == []
    assert pagecheck.slow(pagecheck.metrics(clean())) == []


def test_accessibility_faults_name_the_element_and_the_criterion() -> None:
    found = pagecheck.faults(
        clean(
            lang="",
            main=0,
            zoom_blocked=True,
            images={"no_alt": ["img.hero"]},
            buttons_unnamed=["button.close"],
            links_unnamed=["a.icon"],
            frames_untitled=["iframe"],
            tables_headless=["table.prices"],
            tabindex_positive=["div.first"],
            hidden_focusable=["div > button"],
            roles_invalid=['div (role="buton")'],
            duplicate_ids=["email"],
            autoplay=["video"],
        ),
        ("accessibility",),
    )
    assert rules(found) == {
        "a11y.image_alt",
        "a11y.control_name",
        "a11y.page_lang",
        "a11y.main_landmark",
        "a11y.frame_title",
        "a11y.table_headers",
        "a11y.tabindex",
        "a11y.hidden_focusable",
        "a11y.role",
        "a11y.duplicate_id",
        "a11y.zoom",
        "a11y.autoplay",
    }
    alt = next(f for f in found if f.rule == "a11y.image_alt")
    assert alt.element == "img.hero" and alt.standard == "WCAG 2.2 1.1.1 Non-text Content"
    assert len([f for f in found if f.rule == "a11y.control_name"]) == 2


def test_only_the_sets_asked_for_are_judged() -> None:
    page = clean(title="", description=[], lang="", main=0)
    assert rules(pagecheck.faults(page, ("seo",))) == {
        "a11y.page_title",
        "a11y.page_lang",
        "seo.description",
    }
    assert rules(pagecheck.faults(page, ("accessibility",))) == {
        "a11y.page_lang",
        "a11y.page_title",
        "a11y.main_landmark",
    }
    assert pagecheck.faults(page, ()) == []


def test_design_rules_read_from_the_document() -> None:
    found = pagecheck.faults(
        clean(
            headings=[1, 3], fields_unlabelled=["input.q"], fields_no_purpose=["input#e (email)"]
        ),
        ("design",),
    )
    assert rules(found) == {"design.heading_order", "design.field_label", "design.field_purpose"}
    assert "follows an h1" in next(f for f in found if f.rule == "design.heading_order").detail


def test_search_faults() -> None:
    found = pagecheck.faults(
        clean(
            title_count=2,
            h1=[],
            canonical=["/tents", "/tents?x"],
            robots="noindex",
            viewport="",
            fake_links=["span.more"],
            links_vague=['a ("click here")'],
            jsonld={"bad": 1},
            hreflang=["english"],
            og={"title": "", "image": ""},
            icon=False,
            fetched={"status": 404, "robots_txt": "User-agent: *\nDisallow:\n"},
        ),
        ("seo",),
    )
    assert rules(found) == {
        "seo.title",
        "seo.h1",
        "seo.canonical",
        "seo.noindex",
        "seo.viewport",
        "seo.links",
        "seo.link_text",
        "seo.jsonld",
        "seo.hreflang",
        "seo.social",
        "seo.icon",
        "seo.status",
        "seo.sitemap",
    }
    relative = pagecheck.faults(clean(canonical=["/tents"]), ("seo",))
    assert [f.detail for f in relative] == ["is '/tents'; use the full address"]
    header = pagecheck.faults(clean(fetched={"x_robots": "noindex, nofollow"}), ("seo",))
    assert rules(header) == {"seo.noindex"}


def test_robots_txt_is_read_by_group() -> None:
    groups = pagecheck.robots_groups(
        "# comment\nUser-agent: GPTBot\nUser-agent: CCBot\nDisallow: /\n\n"
        "User-agent: *\nDisallow: /private\n\nUser-agent: Claude-User\nDisallow: /\nAllow: /\n"
        "Sitemap: https://a.example/sitemap.xml\n"
    )
    assert pagecheck.shut_out(groups, "gptbot") and pagecheck.shut_out(groups, "CCBot")
    assert not pagecheck.shut_out(groups, "*")
    assert not pagecheck.shut_out(groups, "PerplexityBot")  # falls under *
    assert not pagecheck.shut_out(groups, "Claude-User")  # an Allow of / undoes it
    assert groups["sitemap"] == ["https://a.example/sitemap.xml"]

    missing = pagecheck.faults(clean(fetched={"robots_txt": None}), ("seo",))
    assert [f.detail for f in missing] == ["is missing"]
    closed = pagecheck.faults(
        clean(fetched={"robots_txt": "User-agent: *\nDisallow: /\n"}), ("seo",)
    )
    assert rules(closed) == {"seo.robots_txt"}


def test_ai_assistant_faults() -> None:
    robots = (
        "User-agent: *\nDisallow:\nSitemap: https://a.example/s.xml\n\n"
        "User-agent: ChatGPT-User\nDisallow: /\n"
    )
    found = pagecheck.faults(
        clean(
            robots="max-snippet:0",
            text_chars=5000,
            og={"type": "article"},
            fetched={"raw_chars": 300, "robots_txt": robots},
        ),
        ("llm",),
    )
    assert rules(found) == {
        "llm.raw_content",
        "llm.agents",
        "llm.training_agents",
        "llm.snippet",
        "llm.dates",
    }
    assert "ChatGPT-User" in next(f for f in found if f.rule == "llm.agents").detail
    told = next(f for f in found if f.rule == "llm.training_agents").detail
    assert all(agent in told for agent in pagecheck.AI_TRAINING_AGENTS)
    # An article with a date a machine can read, and a short page, are left alone.
    dated = clean(og={"type": "article"}, jsonld={"dates": ["2026-11-01"]})
    assert pagecheck.faults(dated, ("llm",)) == []
    assert pagecheck.faults(clean(text_chars=200, fetched={"raw_chars": 10}), ("llm",)) == []


def test_performance_causes_and_what_a_server_decides() -> None:
    page = clean(
        dom_nodes=2000,
        images={"no_size": ["img.hero"], "eager_below": ["img.a", "img.b"]},
        perf={
            "cls": 0.31,
            "lcp_is_image": True,
            "lcp_lazy": True,
            "lcp_priority": False,
            "lcp_element": "img.hero",
            "blocking_scripts": ["https://shop.example/app.js"],
            "font_faces": 3,
            "font_faces_blocking": 2,
            "heavy_old_images": ["https://shop.example/big.png"],
            "protocols": ["http/1.1"],
            "total_bytes": 6_000_000,
        },
        fetched={
            "uncompressed": ["https://shop.example/app.js"],
            "uncached": ["https://shop.example/a.css"],
        },
    )
    found = pagecheck.faults(page, ("performance",))
    assert rules(found) == {
        "perf.cls",
        "perf.image_size",
        "perf.lcp_lazy",
        "perf.lazy_offscreen",
        "perf.blocking_script",
        "perf.font_display",
        "perf.dom_size",
        "perf.image_format",
        "perf.compression",
        "perf.cache",
        "perf.protocol",
        "perf.weight",
    }
    # On this machine the server's own choices are not the code's to answer for.
    local = pagecheck.faults({**page, "url": "http://127.0.0.1:5173/"}, ("performance",))
    assert not rules(local) & {"perf.compression", "perf.cache", "perf.protocol"}
    # The largest image, loaded eagerly, is asked to say it comes first.
    eager = clean(
        perf={
            "lcp_is_image": True,
            "lcp_lazy": False,
            "lcp_priority": False,
            "lcp_element": "img.hero",
        }
    )
    assert rules(pagecheck.faults(eager, ("performance",))) == {"perf.lcp_priority"}


def test_timings_are_handed_back_only_when_also_worse_than_before() -> None:
    now = pagecheck.metrics(clean(perf={"lcp_ms": 3200, "tbt_ms": 450}))
    alone = pagecheck.slow(now)
    assert {f.rule for f in alone} == {"perf.lcp", "perf.tbt"}
    assert "3.20 s; the limit is 2.5 s" in alone[0].detail
    already = pagecheck.metrics(clean(perf={"lcp_ms": 3100, "tbt_ms": 200}))
    worse = pagecheck.slow(now, already)
    assert {f.rule for f in worse} == {"perf.tbt"}  # the page was already that slow to paint
    assert "it was 0.20 s before" in worse[0].detail
    assert pagecheck.slow(now, already, ("seo",)) == []


def test_a_page_that_got_heavier_is_said_with_both_numbers() -> None:
    before = pagecheck.metrics(clean())
    now = pagecheck.metrics(
        clean(dom_nodes=320, perf={"script_bytes": 190_000, "total_bytes": 400_000, "requests": 13})
    )
    found = pagecheck.heavier(now, before)
    assert [f.detail for f in found] == [
        "JavaScript: 88 KB before, 186 KB now",
        "the whole page: 293 KB before, 391 KB now",
    ]
    assert pagecheck.heavier(before, before) == []
    table = pagecheck.table(now, before)
    assert "Largest Contentful Paint: 0.90 s (before: 0.90 s); limit 2.50 s" in table
    assert "JavaScript: 186 KB (before: 88 KB)" in table


def test_what_the_browser_was_sent_is_reduced_to_facts() -> None:
    html = (
        "<html><head><title>T</title><style>p{}</style></head><body><!-- c -->"
        "<p>Hello  there</p><script>var a = 'not text';</script></body></html>"
    )
    assert pagecheck.text_chars(html) == len("Hello there")
    sent = [
        {
            "url": "https://a.example/",
            "status": 200,
            "bytes": 9000,
            "headers": {"content-type": "text/html", "x-robots-tag": "noindex"},
        },
        {
            "url": "https://a.example/app.js",
            "status": 200,
            "bytes": 50_000,
            "headers": {
                "content-type": "text/javascript",
                "content-encoding": "br",
                "cache-control": "max-age=31536000",
            },
        },
        {
            "url": "https://a.example/a.css",
            "status": 200,
            "bytes": 800,
            "headers": {"content-type": "text/css", "cache-control": "no-cache"},
        },
        {
            "url": "https://a.example/gone.js",
            "status": 404,
            "bytes": 5000,
            "headers": {"content-type": "text/javascript"},
        },
    ]
    got = pagecheck.fetched_facts(sent, main_url="https://a.example/", html=html, robots=None)
    assert got["x_robots"] == "noindex" and got["raw_chars"] == 11 and got["robots_txt"] is None
    assert got["uncompressed"] == ["https://a.example/"]
    assert got["uncached"] == ["https://a.example/a.css"]
    assert "robots_txt" not in pagecheck.fetched_facts(
        sent, main_url="https://a.example/", html=None, robots=...
    )


def test_which_sets_a_project_is_held_to() -> None:
    assert pagecheck.sets_for(website=False) == ("design", "accessibility", "performance")
    assert pagecheck.sets_for(website=True) == pagecheck.SETS
    assert pagecheck.sets_for(website=False, chosen=["seo", "design"]) == ("design", "seo")
    assert pagecheck.is_local("http://localhost:3000/x") and pagecheck.is_local("http://127.0.0.1/")
    assert not pagecheck.is_local("https://shop.example/")


def test_every_check_stands_for_a_rule_prat_ships() -> None:
    """A fault always names a published rule, and nothing is called measured that is not."""
    for name, check in pagecheck.CHECKS.items():
        for set_name, rule_id in ((check.set, check.rule_id), *check.also):
            rule = standards.find(rule_id, set_name)
            assert rule is not None, f"{name}: no rule {rule_id!r} in the {set_name} set"
            assert check.partial or standards.measured(rule), name
    for set_name in pagecheck.SETS:
        for rule in standards.load(set_name):
            if standards.measured(rule):
                assert rule.check == "mechanical", (
                    f"{set_name}/{rule.id} is measured but not marked so"
                )


def test_a_load_is_weighed_from_what_the_browser_was_sent() -> None:
    sent = [("document", 9000), ("script", 40_000), ("script", 2000), ("image", 700), ("xhr", 50)]
    assert pagecheck.weights(sent) == {
        "script_bytes": 42_000,
        "style_bytes": 0,
        "image_bytes": 700,
        "font_bytes": 0,
        "total_bytes": 51_750,
        "requests": 5,
    }
