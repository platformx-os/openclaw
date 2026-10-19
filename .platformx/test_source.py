"""Exercise rejection boundaries without application imports, network, images or real state."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("source_contract_check", HERE / "check_source.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
BASE = json.loads((HERE / "source.json").read_text())


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = copy.deepcopy(BASE)
        self.temp = tempfile.TemporaryDirectory(prefix="contract-test-", dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = HERE.parent
        for name in [BASE["build"]["dockerfile"], BASE["license"]["path"], BASE["version_validation"]["metadata_file"]]:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / name).read_bytes())

    def test_current_contract_validates(self):
        CHECK.validate(self.contract, self.root)

    def test_refuses_escaping_dockerfile(self):
        self.contract["build"]["dockerfile"] = "../Dockerfile"
        with self.assertRaisesRegex(ValueError, "relative"):
            CHECK.validate(self.contract, self.root)

    def test_refuses_dockerfile_drift(self):
        path = self.root / BASE["build"]["dockerfile"]
        path.write_text(path.read_text() + "\n# incompatible candidate\n")
        with self.assertRaisesRegex(ValueError, "Dockerfile changed"):
            CHECK.validate(self.contract, self.root)

    def test_refuses_entrypoint_override(self):
        self.contract["runtime"]["entrypoint"] = ["sh"]
        with self.assertRaisesRegex(ValueError, "entrypoint"):
            CHECK.validate(self.contract, self.root)

    def test_refuses_baseline_as_build_revision(self):
        self.contract["provenance"]["required_oci_labels"]["org.opencontainers.image.revision"] = self.contract["upstream_commit"]
        with self.assertRaisesRegex(ValueError, "actual integration"):
            CHECK.validate(self.contract, self.root)

    def test_refuses_runtime_or_legacy_patch_claim(self):
        self.contract["patches"]["legacy_imported"] = True
        with self.assertRaisesRegex(ValueError, "implicit"):
            CHECK.validate(self.contract, self.root)

    def test_refuses_version_drift(self):
        metadata = self.root / BASE["version_validation"]["metadata_file"]
        if BASE["version_validation"]["mode"] == "package_json":
            obj = json.loads(metadata.read_text())
            obj["version"] = "0.0.0"
            metadata.write_text(json.dumps(obj))
        else:
            metadata.write_text(metadata.read_text().replace('version = "0.0.0"', 'version = "999.0.0"', 1))
        with self.assertRaisesRegex(ValueError, "version|metadata"):
            CHECK.validate(self.contract, self.root)


if __name__ == "__main__":
    unittest.main()
