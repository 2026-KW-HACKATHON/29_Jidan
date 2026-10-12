import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "demo" / "prepare_env.py"
spec = importlib.util.spec_from_file_location("demo_env", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DemoEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "dev-base.env"
        self.base.write_text("DB_USER=jidan_dev\nDB_PASSWORD=test-$-literal\nOPENAI_API_KEY=test-only\n"
                             "ADMIN_PASSWORD_HASH=test-hash\nDB_NAME=jidan_dev\nOPENAI_MODEL=existing-model\n")
        self.destination = self.root / "demo"

    def test_reuses_values_and_isolates_database_preserving_root_on_repeat(self):
        module.prepare(self.base, self.destination)
        runtime = module.raw_values(self.destination / "runtime.env")
        mysql = module.raw_values(self.destination / "mysql.env")
        self.assertEqual(runtime["DB_NAME"], "jidan_demo")
        self.assertEqual(runtime["DB_PASSWORD"], mysql["MYSQL_PASSWORD"])
        self.assertEqual(runtime["OPENAI_API_KEY"], "test-only")
        self.assertEqual(runtime["OPENAI_MODEL"], "existing-model")
        self.assertNotIn("MYSQL_ROOT_PASSWORD", runtime)
        module.prepare(self.base, self.destination)
        self.assertEqual(module.raw_values(self.destination / "mysql.env")["MYSQL_ROOT_PASSWORD"], mysql["MYSQL_ROOT_PASSWORD"])
        for file in ("runtime.env", "mysql.env"):
            self.assertEqual(stat.S_IMODE((self.destination / file).stat().st_mode), 0o600)
        self.assertEqual(module.raw_values(self.base)["DB_NAME"], "jidan_dev")

    def test_missing_credentials_and_root_account_are_rejected_without_values(self):
        for content in ("DB_USER=root\nDB_PASSWORD=private-value\nOPENAI_API_KEY=test\nADMIN_PASSWORD_HASH=test\n", "DB_USER=user\n"):
            self.base.write_text(content)
            with self.assertRaises(ValueError) as error:
                module.prepare(self.base, self.destination)
            self.assertNotIn("private-value", str(error.exception))
            self.assertFalse((self.destination / "runtime.env").exists())

    def test_rotation_and_symlink_destination_are_rejected(self):
        module.prepare(self.base, self.destination)
        self.base.write_text(self.base.read_text().replace("test-$-literal", "new-password"))
        with self.assertRaises(ValueError):
            module.prepare(self.base, self.destination)
        runtime = self.destination / "runtime.env"
        self.base.write_text(self.base.read_text().replace("new-password", "test-$-literal"))
        runtime.unlink()
        runtime.symlink_to(self.base)
        with self.assertRaises(ValueError):
            module.prepare(self.base, self.destination)
        self.assertEqual(module.raw_values(self.base)["DB_NAME"], "jidan_dev")


if __name__ == "__main__":
    unittest.main()
