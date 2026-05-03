from __future__ import annotations

import io
import json
import os
import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skill_guardian.runtime import (  # noqa: E402
    PathAudit,
    RemoteState,
    audit_skill_path,
    compute_report,
    entrypoint,
    parse_frontmatter,
)


TEST_TEMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp-tests"
TEST_TEMP_ROOT.mkdir(exist_ok=True)


def write_skill(skill_dir: Path, *, name: str, with_openai_yaml: bool = True, script_body: str | None = None) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: Test skill {name}",
                "---",
                "",
                f"# {name}",
                "",
                "Safe test content.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if with_openai_yaml:
        agents = skill_dir / "agents"
        agents.mkdir(parents=True, exist_ok=True)
        (agents / "openai.yaml").write_text(
            'interface:\n  display_name: "Test"\n  short_description: "Test description for skill use"\n',
            encoding="utf-8",
        )
    if script_body is not None:
        scripts = skill_dir / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "tool.py").write_text(script_body, encoding="utf-8")


class SkillGuardianTests(unittest.TestCase):
    def make_temp_dir(self) -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT)

    def test_config_init_and_roots_list_json(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            config_path = base / "config.json"
            custom_root = base / "custom-skills"
            custom_root.mkdir()

            config_stdout = io.StringIO()
            code = entrypoint(
                ["config", "init", "--config-file", str(config_path), "--force"],
                stream=config_stdout,
            )
            self.assertEqual(code, 0)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["roots"] = [str(custom_root)]
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

            roots_stdout = io.StringIO()
            code = entrypoint(
                ["roots", "list", "--config-file", str(config_path), "--format", "json"],
                stream=roots_stdout,
            )
            self.assertEqual(code, 0)
            payload = json.loads(roots_stdout.getvalue())
            self.assertTrue(any(item["path"] == str(custom_root) for item in payload["roots"]))

    def test_audit_offline_writes_lock_and_history(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            codex_root = base / ".codex" / "skills"
            agents_root = base / ".agents" / "skills"
            write_skill(codex_root / "alpha", name="alpha")
            write_skill(agents_root / "beta", name="beta", with_openai_yaml=False)

            config_path = base / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [],
                        "upstreams": {},
                        "policy": "balanced",
                        "source_allowlist": [],
                        "source_blocklist": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            lock_path = base / "skills.lock.json"
            history_path = base / "history.jsonl"

            stdout = io.StringIO()
            code = entrypoint(
                [
                    "audit",
                    "--offline",
                    "--format",
                    "json",
                    "--write-lock",
                    "--roots",
                    str(codex_root),
                    str(agents_root),
                    "--config-file",
                    str(config_path),
                    "--lock-file",
                    str(lock_path),
                    "--history-file",
                    str(history_path),
                ],
                stream=stdout,
            )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(len(payload["roots"]), 2)
            self.assertEqual(len(payload["reports"]), 2)
            self.assertTrue(lock_path.exists())
            self.assertTrue(history_path.exists())

    def test_docs_only_remote_diff_recommends_update(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            root = base / "skills"
            skill_dir = root / "gamma"
            write_skill(skill_dir, name="gamma")
            local_audit = audit_skill_path(skill_dir)
            remote_hashes = dict(local_audit.file_hashes)
            remote_hashes["README.md"] = "different-doc-hash"
            remote_audit = PathAudit(
                fingerprint="remote-doc-fingerprint",
                frontmatter_name=local_audit.frontmatter_name,
                frontmatter_description=local_audit.frontmatter_description,
                frontmatter_valid=True,
                has_openai_yaml=True,
                file_hashes=remote_hashes,
                total_files=local_audit.total_files + 1,
                script_files=local_audit.script_files,
                executable_files=local_audit.executable_files,
                binary_files=local_audit.binary_files,
                issues=[],
            )
            entry = {
                "skill_id": f"{str(root.resolve()).lower()}::gamma",
                "name": "gamma",
                "path": skill_dir,
                "root": str(root),
                "host_adapter": "custom",
                "local_audit": local_audit,
            }
            with patch(
                "skill_guardian.runtime.inspect_remote",
                return_value=RemoteState(
                    latest_commit="abcdef1234567890",
                    latest_commit_short="abcdef12",
                    latest_commit_date="2026-05-02T00:00:00Z",
                    latest_tag=None,
                    path_audit=remote_audit,
                ),
            ):
                report = compute_report(
                    entry=entry,
                    config={
                        "upstreams": {"gamma": {"repo": "owner/repo", "path": "skills/gamma", "ref": "main"}},
                        "source_allowlist": [],
                        "source_blocklist": [],
                    },
                    lock_data={"skills": {}},
                    default_upstreams={},
                    policy_name="balanced",
                    offline=False,
                )
            self.assertEqual(report["update_recommendation"], "update")

    def test_script_change_blocks_in_conservative_policy(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            root = base / "skills"
            skill_dir = root / "delta"
            write_skill(skill_dir, name="delta", script_body="print('hello')\n")
            local_audit = audit_skill_path(skill_dir)
            remote_hashes = dict(local_audit.file_hashes)
            remote_hashes["scripts/tool.py"] = "different-script-hash"
            remote_audit = PathAudit(
                fingerprint="remote-script-fingerprint",
                frontmatter_name=local_audit.frontmatter_name,
                frontmatter_description=local_audit.frontmatter_description,
                frontmatter_valid=True,
                has_openai_yaml=True,
                file_hashes=remote_hashes,
                total_files=local_audit.total_files,
                script_files=local_audit.script_files,
                executable_files=local_audit.executable_files,
                binary_files=local_audit.binary_files,
                issues=[],
            )
            entry = {
                "skill_id": f"{str(root.resolve()).lower()}::delta",
                "name": "delta",
                "path": skill_dir,
                "root": str(root),
                "host_adapter": "custom",
                "local_audit": local_audit,
            }
            with patch(
                "skill_guardian.runtime.inspect_remote",
                return_value=RemoteState(
                    latest_commit="1234567890abcdef",
                    latest_commit_short="12345678",
                    latest_commit_date="2026-05-02T00:00:00Z",
                    latest_tag=None,
                    path_audit=remote_audit,
                ),
            ):
                report = compute_report(
                    entry=entry,
                    config={
                        "upstreams": {"delta": {"repo": "owner/repo", "path": "skills/delta", "ref": "main"}},
                        "source_allowlist": [],
                        "source_blocklist": [],
                    },
                    lock_data={"skills": {}},
                    default_upstreams={},
                    policy_name="conservative",
                    offline=False,
                )
            self.assertEqual(report["update_recommendation"], "block")

    def test_roots_list_does_not_create_state_directory(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            state_dir = base / "nonexistent-state-dir"
            roots_stdout = io.StringIO()
            with patch.dict(os.environ, {"SKILL_GUARDIAN_STATE_DIR": str(state_dir)}, clear=False):
                code = entrypoint(
                    ["roots", "list", "--format", "json", "--roots", str(base)],
                    stream=roots_stdout,
                )
            self.assertEqual(code, 0)
            self.assertFalse(state_dir.exists())

    def test_write_command_fails_when_state_dir_is_unwritable_target(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            cwd_before = Path.cwd()
            codex_root = base / "skills"
            write_skill(codex_root / "alpha", name="alpha")
            blocked_path = base / "blocked-state"
            blocked_path.write_text("not a directory", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            os.chdir(base)
            try:
                with patch.dict(os.environ, {"SKILL_GUARDIAN_STATE_DIR": str(blocked_path)}, clear=False):
                    with patch("sys.stderr", stderr):
                        code = entrypoint(
                            ["audit", "--offline", "--write-lock", "--roots", str(codex_root), "--format", "json"],
                            stream=stdout,
                        )
            finally:
                os.chdir(cwd_before)
            self.assertEqual(code, 1)
            self.assertIn("No writable state directory", stderr.getvalue())
            self.assertFalse((base / "skills.lock.json").exists())

    def test_parse_frontmatter_accepts_multiline_yaml_description(self) -> None:
        text = "\ufeff---\nname: sample-skill\ndescription: |\n  First line.\n  Second line.\n---\n\n# Title\n"
        parsed = parse_frontmatter(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "sample-skill")
        self.assertIn("First line.", parsed["description"])
        self.assertIn("Second line.", parsed["description"])

    def test_readme_zh_cn_exists(self) -> None:
        zh_readme = Path(__file__).resolve().parents[1] / "README.zh-CN.md"
        self.assertTrue(zh_readme.exists())
        content = zh_readme.read_text(encoding="utf-8")
        self.assertIn("Skill Guardian 技能审计与更新治理", content)
        self.assertIn("gh skill install Adenine-AGCT/skill-guardian", content)

    def test_quick_mode_keeps_lightweight_path_when_gap_is_small(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            root = base / "skills"
            write_skill(root / "epsilon", name="epsilon")
            config_path = base / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [],
                        "upstreams": {"epsilon": {"repo": "owner/repo", "path": "skills/epsilon", "ref": "main"}},
                        "policy": "balanced",
                        "source_allowlist": [],
                        "source_blocklist": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            lock_path = base / "skills.lock.json"
            lock_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "skills": {
                            f"{str(root.resolve()).lower()}::epsilon": {
                                "current_tag": "v1.0.0",
                                "current_commit_date": "2026-05-01T00:00:00Z",
                                "baseline": {"fingerprint": audit_skill_path(root / "epsilon").fingerprint},
                                "last_remote_checked_at": "2026-05-02T00:00:00Z",
                            }
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with patch(
                "skill_guardian.runtime.inspect_remote_metadata",
                return_value=RemoteState(
                    latest_commit="abc123456789",
                    latest_commit_short="abc12345",
                    latest_commit_date="2026-05-15T00:00:00Z",
                    latest_tag="v1.0.1",
                ),
            ):
                with patch("skill_guardian.runtime.inspect_remote", side_effect=AssertionError("full remote check should not run")):
                    code = entrypoint(
                        [
                            "audit",
                            "--format",
                            "json",
                            "--mode",
                            "quick",
                            "--roots",
                            str(root),
                            "--config-file",
                            str(config_path),
                            "--lock-file",
                            str(lock_path),
                        ],
                        stream=stdout,
                    )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["mode"], "quick")
            self.assertEqual(payload["reports"][0]["version_gap_level"], "small")

    def test_quick_mode_large_gap_upgrades_to_standard(self) -> None:
        with self.make_temp_dir() as temp_dir:
            base = Path(temp_dir)
            root = base / "skills"
            skill_dir = root / "zeta"
            write_skill(skill_dir, name="zeta")
            config_path = base / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "roots": [],
                        "upstreams": {"zeta": {"repo": "owner/repo", "path": "skills/zeta", "ref": "main"}},
                        "policy": "balanced",
                        "source_allowlist": [],
                        "source_blocklist": [],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            skill_id = f"{str(root.resolve()).lower()}::zeta"
            local_audit = audit_skill_path(skill_dir)
            lock_path = base / "skills.lock.json"
            lock_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "skills": {
                            skill_id: {
                                "current_tag": "v1.0.0",
                                "current_commit_date": "2026-01-01T00:00:00Z",
                                "baseline": {"fingerprint": local_audit.fingerprint},
                                "last_remote_checked_at": "2026-02-01T00:00:00Z",
                            }
                        },
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with patch(
                "skill_guardian.runtime.inspect_remote_metadata",
                return_value=RemoteState(
                    latest_commit="fedcba9876543210",
                    latest_commit_short="fedcba98",
                    latest_commit_date="2026-05-03T00:00:00Z",
                    latest_tag="v2.0.0",
                ),
            ):
                with patch(
                    "skill_guardian.runtime.inspect_remote",
                    return_value=RemoteState(
                        latest_commit="fedcba9876543210",
                        latest_commit_short="fedcba98",
                        latest_commit_date="2026-05-03T00:00:00Z",
                        latest_tag="v2.0.0",
                        path_audit=local_audit,
                    ),
                ) as remote_check:
                    code = entrypoint(
                        [
                            "audit",
                            "--format",
                            "json",
                            "--mode",
                            "quick",
                            "--roots",
                            str(root),
                            "--config-file",
                            str(config_path),
                            "--lock-file",
                            str(lock_path),
                        ],
                        stream=stdout,
                    )
            self.assertEqual(code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["mode"], "standard")
            self.assertEqual(payload["skills_with_large_version_gap"], ["zeta"])
            self.assertTrue(remote_check.called)

    def test_sync_skill_runtime_script_keeps_runtime_in_sync(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script_path = repo_root / "scripts" / "sync_skill_runtime.py"
        argv_before = sys.argv[:]
        try:
            sys.argv = [str(script_path), "--check"]
            with self.assertRaises(SystemExit) as exc:
                runpy.run_path(str(script_path), run_name="__main__")
        finally:
            sys.argv = argv_before
        self.assertEqual(exc.exception.code, 0)
        src_runtime = (repo_root / "src" / "skill_guardian" / "runtime.py").read_text(encoding="utf-8")
        bundled_runtime = (
            repo_root / "skills" / "skill-guardian" / "assets" / "python" / "skill_guardian_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(src_runtime, bundled_runtime)

    def test_skill_wrapper_repo_mode_invokes_cli_main(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        wrapper_path = repo_root / "skills" / "skill-guardian" / "scripts" / "skill_guardian.py"
        cli_module = types.ModuleType("skill_guardian.cli")
        package_module = types.ModuleType("skill_guardian")
        package_module.__path__ = []  # type: ignore[attr-defined]
        captured: dict[str, list[str]] = {}

        def fake_main(argv: list[str] | None = None) -> int:
            captured["argv"] = list(argv or [])
            return 0

        cli_module.main = fake_main  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"skill_guardian": package_module, "skill_guardian.cli": cli_module}, clear=False):
            with patch.object(sys, "argv", [str(wrapper_path), "--offline"]):
                with self.assertRaises(SystemExit) as exc:
                    runpy.run_path(str(wrapper_path), run_name="__main__")

        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(
            captured["argv"][:10],
            ["audit", "--format", "markdown", "--write-lock", "--mode", "quick", "--max-detailed-skills", "5", "--max-remote-checks", "0"],
        )
        self.assertIn("--offline", captured["argv"])

    def test_skill_wrapper_bundled_mode_invokes_bundled_runtime(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        wrapper_path = repo_root / "skills" / "skill-guardian" / "scripts" / "skill_guardian.py"
        wrapper_repo_src = wrapper_path.resolve().parents[3] / "src"
        bundled_module = types.ModuleType("skill_guardian_runtime")
        captured: dict[str, list[str]] = {}

        def fake_entrypoint(argv: list[str] | None = None) -> int:
            captured["argv"] = list(argv or [])
            return 0

        bundled_module.entrypoint = fake_entrypoint  # type: ignore[attr-defined]
        original_exists = Path.exists

        def fake_exists(path: Path) -> bool:
            if path == wrapper_repo_src:
                return False
            return original_exists(path)

        with patch.dict(sys.modules, {"skill_guardian_runtime": bundled_module}, clear=False):
            with patch("pathlib.Path.exists", fake_exists):
                with patch.object(sys, "argv", [str(wrapper_path), "--offline"]):
                    with self.assertRaises(SystemExit) as exc:
                        runpy.run_path(str(wrapper_path), run_name="__main__")

        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(
            captured["argv"][:10],
            ["audit", "--format", "markdown", "--write-lock", "--mode", "quick", "--max-detailed-skills", "5", "--max-remote-checks", "0"],
        )
        self.assertIn("--offline", captured["argv"])


if __name__ == "__main__":
    unittest.main()
