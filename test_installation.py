"""Offline bootstrap/configuration checks; no dependency downloads or real tokens."""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from configuration import load_env
import setup_memlo


class ConfigurationTests(unittest.TestCase):
    def load(self, text, environ=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(text, encoding="utf-8")
            result = {} if environ is None else environ
            load_env(path, result)
            return result

    def test_values_quotes_comments_and_existing_environment(self):
        result = self.load('A=one\nB="two words # literal"\nEMPTY=\nexport C=three # comment\n', {"A": "exported"})
        self.assertEqual(result, {"A": "exported", "B": "two words # literal", "EMPTY": "", "C": "three"})

    def test_missing_file_is_harmless(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {}
            load_env(Path(directory) / "missing", environment)
            self.assertEqual(environment, {})

    def test_values_are_literal_not_shell_commands(self):
        result = self.load("VALUE='$(do-not-run-anything)'\nOTHER='${VALUE}'\n")
        self.assertEqual(result["VALUE"], "$(do-not-run-anything)")
        self.assertEqual(result["OTHER"], "${VALUE}")

    def test_bad_file_is_atomic_and_error_does_not_echo_secret(self):
        environment = {}
        with self.assertRaises(ValueError) as error:
            self.load('TOKEN=do-not-print-this\nBAD="unterminated-secret\n', environment)
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(environment, {})

    def test_invalid_names_and_nulls_are_rejected(self):
        for text in ("NOT A KEY=secret", "a-key=secret", "missing-equals-secret", "KEY=abc\x00def"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)

    def test_import_and_startup_check_work_without_ai_key(self):
        environment = dict(os.environ)
        environment.pop("ANTHROPIC_API_KEY", None)
        environment.pop("DISCORD_TOKEN", None)
        result = subprocess.run([sys.executable, "-c",
            "import bot; assert bot.claude is None; assert not bot.intents.message_content; "
            "assert not bot.ai_available(); print('no-key import OK')"],
            cwd=Path(__file__).parent, env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "no-key import OK")

    def test_check_never_connects_or_prints_token(self):
        import bot
        output = io.StringIO()
        with patch.dict(os.environ, {"DISCORD_TOKEN": "do-not-print-this", "ANTHROPIC_API_KEY": ""}), \
                patch.object(bot.bot, "run") as run, patch.object(bot, "init_db") as initialize, \
                contextlib.redirect_stdout(output):
            self.assertEqual(bot.main(["--check"]), 0)
        self.assertNotIn("do-not-print-this", output.getvalue())
        self.assertIn("deadlines only", output.getvalue())
        run.assert_not_called()
        initialize.assert_not_called()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / ".env.example").write_text("DISCORD_TOKEN=\nANTHROPIC_API_KEY=\n", encoding="utf-8")
        (self.root / "requirements.txt").write_text("discord.py\n", encoding="utf-8")
        self.python = self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def existing_environment(self):
        self.python.parent.mkdir(parents=True)
        self.python.touch()

    def test_existing_env_file_is_never_overwritten_or_printed(self):
        self.existing_environment()
        config = self.root / ".env"
        config.write_text("DISCORD_TOKEN=existing-private-value\n", encoding="utf-8")
        output = io.StringIO()
        with patch.object(setup_memlo.subprocess, "run") as install, contextlib.redirect_stdout(output):
            self.assertEqual(setup_memlo.bootstrap(self.root), self.python)
        self.assertEqual(config.read_text(), "DISCORD_TOKEN=existing-private-value\n")
        self.assertNotIn("existing-private-value", output.getvalue())
        self.assertEqual(install.call_args.args[0][1:4], ["-m", "pip", "install"])

    def test_new_env_created_from_template(self):
        self.existing_environment()
        with patch.object(setup_memlo.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            setup_memlo.bootstrap(self.root)
        self.assertEqual((self.root / ".env").read_text(), (self.root / ".env.example").read_text())
        if os.name != "nt":
            self.assertEqual((self.root / ".env").stat().st_mode & 0o777, 0o600)

    def test_missing_environment_is_created(self):
        builder = Mock()
        builder.create.side_effect = lambda path: self.existing_environment()
        with patch.object(setup_memlo.venv, "EnvBuilder", return_value=builder), \
                patch.object(setup_memlo.subprocess, "run"), contextlib.redirect_stdout(io.StringIO()):
            setup_memlo.bootstrap(self.root)
        builder.create.assert_called_once_with(self.root / ".venv")

    def test_incomplete_existing_environment_fails_safely(self):
        (self.root / ".venv").mkdir()
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            setup_memlo.bootstrap(self.root)
        self.assertFalse((self.root / ".env").exists())


if __name__ == "__main__":
    unittest.main()
