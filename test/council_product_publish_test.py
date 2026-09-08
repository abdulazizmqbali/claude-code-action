import importlib.util
import pathlib
import unittest


MODULE = pathlib.Path(__file__).parents[1] / "scripts" / "council-product-publish.py"
SPEC = importlib.util.spec_from_file_location("council_product_publish", MODULE)
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)


class CandidateTests(unittest.TestCase):
    def candidate(self):
        return {
            "artifact_sha": "a" * 40,
            "files": {path: "content for " + path for path in publish.PATHS},
        }

    def test_exact_candidate_is_accepted(self):
        value = self.candidate()
        self.assertEqual(publish.validate_candidate(value, "a" * 40), value["files"])

    def test_candidate_cannot_change_reserved_head(self):
        with self.assertRaises(publish.Denied):
            publish.validate_candidate(self.candidate(), "b" * 40)

    def test_candidate_requires_every_and_only_product_path(self):
        value = self.candidate()
        value["files"]["package.json"] = "{}"
        with self.assertRaises(publish.Denied):
            publish.validate_candidate(value, "a" * 40)

    def test_candidate_rejects_missing_or_binary_content(self):
        value = self.candidate()
        value["files"][publish.PATHS[0]] = None
        with self.assertRaises(publish.Denied):
            publish.validate_candidate(value, "a" * 40)
        value = self.candidate()
        value["files"][publish.PATHS[0]] = "bad\x00text"
        with self.assertRaises(publish.Denied):
            publish.validate_candidate(value, "a" * 40)


if __name__ == "__main__":
    unittest.main()
