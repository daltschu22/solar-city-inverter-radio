import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import container


class ContainerTests(unittest.TestCase):
    def test_default_and_false_run_only_collector(self):
        for value in (None, "false", "0", " FALSE "):
            settings = {} if value is None else {"SOLAR_DASHBOARD": value}
            with self.subTest(value=value), patch.dict(os.environ, settings, clear=True):
                with patch("container.os.execv") as execute, patch("combined.main") as combined_main:
                    container.main()
                executable, command = execute.call_args.args
                self.assertEqual(executable, sys.executable)
                self.assertEqual(command[0], sys.executable)
                self.assertEqual(Path(command[1]).name, "collector.py")
                combined_main.assert_not_called()

    def test_true_starts_both_and_preserves_exit_code(self):
        for value in ("true", "1", " TRUE "):
            with self.subTest(value=value), patch.dict(os.environ, {"SOLAR_DASHBOARD": value}, clear=True):
                with patch("container.os.execv") as execute, patch("combined.main", return_value=7) as combined_main:
                    self.assertEqual(container.main(), 7)
                execute.assert_not_called()
                combined_main.assert_called_once_with()

    def test_invalid_flag_fails_before_starting_any_service(self):
        for value in ("", "yes", "2", "treu"):
            with self.subTest(value=value), patch.dict(os.environ, {"SOLAR_DASHBOARD": value}, clear=True):
                with patch("container.os.execv") as execute, patch("combined.main") as combined_main:
                    with self.assertRaisesRegex(SystemExit, "SOLAR_DASHBOARD must"):
                        container.main()
                execute.assert_not_called()
                combined_main.assert_not_called()
