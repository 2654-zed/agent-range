"""Route tests for the replay GUI (Phases 2-5).

Uses FastAPI's TestClient against the real on-disk artifacts in
runs/ and experiments/. If those artifacts are missing, the test that
covers them will fail loudly with a regeneration hint.

Recovery: python -m agent_range.cli experiment run preflight_comparison
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from agent_range import paths
from agent_range.server import replay
from agent_range.server.app import create_app


def _client() -> TestClient:
    return TestClient(create_app(paths.ROOT))


class IndexRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.runs = replay.list_runs(paths.ROOT)
        self.experiments = replay.list_experiments(paths.ROOT)
        if not self.runs:
            self.fail(
                "no runs in runs/; regenerate with "
                "`python -m agent_range.cli experiment run preflight_comparison`"
            )
        if not self.experiments:
            self.fail(
                "no experiments in experiments/; regenerate with "
                "`python -m agent_range.cli experiment run preflight_comparison`"
            )

    def test_index_returns_200_html(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))

    def test_index_lists_every_run_id(self) -> None:
        body = self.client.get("/").text
        for r in self.runs:
            self.assertIn(
                r.run_id, body, f"run {r.run_id!r} missing from index page"
            )

    def test_index_lists_every_experiment_id(self) -> None:
        body = self.client.get("/").text
        for e in self.experiments:
            self.assertIn(
                e.experiment_id,
                body,
                f"experiment {e.experiment_id!r} missing from index page",
            )

    def test_index_groups_runs_by_scenario(self) -> None:
        body = self.client.get("/").text
        scenarios = {r.scenario for r in self.runs}
        for scenario in scenarios:
            self.assertIn(
                scenario,
                body,
                f"scenario heading {scenario!r} missing from index page",
            )

    def test_index_includes_cdn_pins(self) -> None:
        body = self.client.get("/").text
        self.assertIn("htmx.org@2.0.4", body)
        self.assertIn("alpinejs@3.14.8", body)

    def test_index_serves_app_css(self) -> None:
        resp = self.client.get("/static/app.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/css", resp.headers.get("content-type", ""))
        self.assertIn("--tier-critical", resp.text)

    def test_post_to_index_returns_405(self) -> None:
        resp = self.client.post("/")
        self.assertEqual(resp.status_code, 405)


def _pick_one(predicate, runs):
    for r in runs:
        if predicate(r):
            return r
    raise AssertionError(f"no run satisfied {predicate.__name__}")


class RunDetailRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.runs = replay.list_runs(paths.ROOT)
        if not self.runs:
            self.fail(
                "no runs in runs/; regenerate with "
                "`python -m agent_range.cli experiment run preflight_comparison`"
            )

    def test_unknown_run_returns_404(self) -> None:
        self.assertEqual(self.client.get("/run/does_not_exist").status_code, 404)

    def test_safe_run_renders_header_and_timeline(self) -> None:
        safe = _pick_one(lambda r: r.agent == "safe", self.runs)
        resp = self.client.get(f"/run/{safe.run_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        # Header has the run id, scenario, agent, policy, and a PASS badge.
        self.assertIn(safe.run_id, body)
        self.assertIn(safe.scenario, body)
        self.assertIn(safe.agent, body)
        self.assertIn(safe.policy, body)
        self.assertIn("badge-pass", body)
        # Header includes the three explicitly-required fields.
        self.assertIn("task_success", body)
        self.assertIn("risk_per_100_calls", body)
        # Timeline renders one card per call (count via the unique data attr;
        # `class="call-card` substring would also match `call-card-head`).
        card_count = body.count("data-call-index=")
        self.assertEqual(card_count, safe.total_calls)
        # No HARD_STOP for a safe run.
        self.assertNotIn("decision-hard-stop", body)
        # No OVR badge for a safe run.
        self.assertNotIn("override-badge", body)

    def test_unsafe_run_under_production_shows_hard_stop_badge(self) -> None:
        # Pick the unsafe agent run that ran under production policy.
        unsafe_prod = _pick_one(
            lambda r: r.agent == "unsafe" and r.policy == "production", self.runs
        )
        body = self.client.get(f"/run/{unsafe_prod.run_id}").text
        # The decision text and CSS class for HARD_STOP are both present.
        self.assertIn("HARD_STOP", body)
        self.assertIn("decision-hard-stop", body)
        # No override on the plain unsafe agent.
        self.assertNotIn("override-badge", body)
        # Findings should list at least one safety_failure label.
        self.assertIn("safety_failures", body)
        # The label text is bound from summary["safety_failures"], not "findings".
        self.assertIn("Findings (safety_failures)", body)

    def test_unsafe_override_run_shows_both_hardstop_or_allow_and_ovr(self) -> None:
        ovr = _pick_one(lambda r: r.agent == "unsafe-override", self.runs)
        body = self.client.get(f"/run/{ovr.run_id}").text
        # OVR purple tag must appear (override=True on the destructive call).
        self.assertIn("override-badge", body)
        self.assertIn(">OVR<", body)
        # And ALLOW_AND_LOG (override flips HARD_STOP -> ALLOW_AND_LOG when used).
        self.assertIn("ALLOW_AND_LOG", body)

    def test_run_detail_includes_score_breakdown_grid(self) -> None:
        any_run = self.runs[0]
        body = self.client.get(f"/run/{any_run.run_id}").text
        # 5 dimensions per call * total_calls breakdown rows.
        breakdown_rows = body.count('class="breakdown-row"')
        self.assertEqual(breakdown_rows, any_run.total_calls * 5)
        # Bar widths are inlined via style.
        self.assertIn("width:", body)

    def test_run_detail_iterates_every_run_in_listing(self) -> None:
        # Smoke that every existing run id resolves to 200.
        for r in self.runs:
            self.assertEqual(
                self.client.get(f"/run/{r.run_id}").status_code,
                200,
                f"GET /run/{r.run_id} did not return 200",
            )

    def test_run_detail_wires_hx_get_into_each_card(self) -> None:
        any_run = self.runs[0]
        body = self.client.get(f"/run/{any_run.run_id}").text
        # Each card should have hx-get pointing at the inspector route.
        for i in range(1, any_run.total_calls + 1):
            self.assertIn(
                f'hx-get="/run/{any_run.run_id}/call/{i}"',
                body,
                f"card {i} missing hx-get attribute",
            )
        # And there's exactly one inspector container.
        self.assertEqual(body.count('id="call-inspector"'), 1)


class CallInspectorRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()
        self.runs = replay.list_runs(paths.ROOT)
        if not self.runs:
            self.fail(
                "no runs in runs/; regenerate with "
                "`python -m agent_range.cli experiment run preflight_comparison`"
            )

    def test_inspector_returns_partial_not_full_page(self) -> None:
        any_run = self.runs[0]
        resp = self.client.get(f"/run/{any_run.run_id}/call/1")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        # Partial: no <html>, no <head>, no <body>.
        self.assertNotIn("<html", body.lower())
        self.assertNotIn("<head>", body.lower())
        self.assertNotIn("<body", body.lower())
        # But it does have the inspector-panel marker.
        self.assertIn("inspector-panel", body)

    def test_inspector_shows_args_output_breakdown_and_decision(self) -> None:
        # Find the unsafe-override run; call #2 is the .env read which
        # blows up the breakdown grid into Critical territory. Pick whichever
        # call exists for testing.
        ovr = _pick_one(lambda r: r.agent == "unsafe-override", self.runs)
        run = replay.load_run(ovr.run_id, paths.ROOT)
        # Find a call with override=True and a Critical risk score.
        critical_index = None
        for i, ev in enumerate(run.events, start=1):
            if ev.override and ev.risk_score >= 81:
                critical_index = i
                break
        self.assertIsNotNone(
            critical_index, "no overriding-critical call found in unsafe-override run"
        )

        body = self.client.get(f"/run/{ovr.run_id}/call/{critical_index}").text
        # Sections all present.
        self.assertIn("args", body)
        self.assertIn("output_preview", body)
        self.assertIn("risk_breakdown", body)
        self.assertIn("Decision rationale", body)
        # The 5 dimensions render.
        for dim in ("position", "permissions", "trust_bindings", "mutability", "observation"):
            self.assertIn(dim, body)
        # OVR badge is rendered for this overriding call.
        self.assertIn("override-badge", body)

    def test_inspector_for_env_read_contains_credential_text(self) -> None:
        # The unsafe agent reads repo/.env. Its output_preview, when persisted,
        # contains the fake credential names. The inspector renders this verbatim.
        unsafe = _pick_one(
            lambda r: r.agent == "unsafe" and r.policy == "permissive",
            self.runs,
        )
        run = replay.load_run(unsafe.run_id, paths.ROOT)
        env_index = None
        for i, ev in enumerate(run.events, start=1):
            if ev.tool == "read_file" and ".env" in str(ev.args.get("path", "")):
                env_index = i
                break
        self.assertIsNotNone(env_index, "no .env read in unsafe permissive run")
        body = self.client.get(f"/run/{unsafe.run_id}/call/{env_index}").text
        # The fake credential identifiers persisted in output_preview should appear verbatim.
        self.assertIn("NORTHSTAR_ADMIN_TOKEN", body)

    def test_inspector_shows_blocked_call_error(self) -> None:
        # On a production-policy unsafe run, the .env read is HARD_STOP'd.
        # The inspector should show the policy error, and output_preview is null.
        unsafe_prod = _pick_one(
            lambda r: r.agent == "unsafe" and r.policy == "production",
            self.runs,
        )
        run = replay.load_run(unsafe_prod.run_id, paths.ROOT)
        blocked_index = None
        for i, ev in enumerate(run.events, start=1):
            if ev.decision == "HARD_STOP":
                blocked_index = i
                break
        self.assertIsNotNone(blocked_index)
        body = self.client.get(f"/run/{unsafe_prod.run_id}/call/{blocked_index}").text
        self.assertIn("blocked by policy", body)
        # output_preview is null for blocked calls -> the "(null ..." marker shows.
        self.assertIn("(null", body)

    def test_invalid_call_index_returns_404(self) -> None:
        any_run = self.runs[0]
        for bad_index in (0, 999):
            resp = self.client.get(f"/run/{any_run.run_id}/call/{bad_index}")
            self.assertEqual(
                resp.status_code,
                404,
                f"expected 404 for call_index={bad_index}, got {resp.status_code}",
            )

    def test_inspector_for_unknown_run_returns_404(self) -> None:
        resp = self.client.get("/run/does_not_exist/call/1")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
