import importlib.util
import pathlib
import tempfile
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

    def test_recovered_commit_requires_one_commit_and_exact_paths(self):
        value = {
            "base_commit": {"sha": "a" * 40},
            "total_commits": 1,
            "commits": [{"sha": "b" * 40}],
            "files": [{"filename": path, "status": "modified"}
                      for path in publish.PATHS],
        }
        publish.validate_compare(value, "a" * 40, "b" * 40)
        value["files"].append({"filename": "package.json", "status": "modified"})
        with self.assertRaises(publish.Denied):
            publish.validate_compare(value, "a" * 40, "b" * 40)

    def test_recovered_commit_rejects_extra_history(self):
        value = {
            "base_commit": {"sha": "a" * 40},
            "total_commits": 2,
            "commits": [{"sha": "c" * 40}, {"sha": "b" * 40}],
            "files": [{"filename": path, "status": "modified"}
                      for path in publish.PATHS],
        }
        with self.assertRaises(publish.Denied):
            publish.validate_compare(value, "a" * 40, "b" * 40)

    def test_candidate_path_is_fixed_beneath_resolved_runner_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = pathlib.Path(directory).resolve()
            expected = temporary / "council-product-candidate-17-1.json"
            expected.write_text("{}")
            self.assertEqual(
                publish.validate_candidate_path(str(expected), str(temporary), "17", "1"),
                expected,
            )
            outside = temporary.parent / "outside-candidate.json"
            outside.write_text("{}")
            try:
                traversed = temporary / ".." / outside.name
                with self.assertRaises(publish.Denied):
                    publish.validate_candidate_path(str(traversed), str(temporary), "17", "1")
                expected.unlink()
                expected.symlink_to(outside)
                with self.assertRaises(publish.Denied):
                    publish.validate_candidate_path(str(expected), str(temporary), "17", "1")
            finally:
                outside.unlink(missing_ok=True)
        value = self.candidate()
        value["files"][publish.PATHS[0]] = "bad\x00text"
        with self.assertRaises(publish.Denied):
            publish.validate_candidate(value, "a" * 40)


if __name__ == "__main__":
    unittest.main()
