#!/usr/bin/env python3
"""Publish the fixed DECISION-003 bundle with the authenticated Claude App token.

This is intentionally product-specific. Claude can only write the candidate JSON
through the separately reviewed in-memory MCP server. This publisher validates
that bundle, verifies the token's live viewer identity, and performs one
compare-and-swap commit before the action revokes the token.
"""

from base64 import b64decode, b64encode
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


REPOSITORY = "abdulazizmqbali/turwa-os"
BRANCH = "council/DECISION-003"
BASE_BRANCH = "main"
IDENTITY = {"id": 41898282, "login": "claude[bot]", "type": "Bot"}
PATHS = (
    "next-app/src/components/landing/DefaultFooter.tsx",
    "tests/fixtures/default-footer.tsx",
    "tests/visual/default-footer.spec.ts",
)
MAX_FILE = 128 * 1024
MAX_BODY = 2 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")


class Denied(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise Denied("GitHub endpoint redirected")


def require(condition, message):
    if not condition:
        raise Denied(message)


def validate_candidate(value, expected_head):
    require(isinstance(value, dict) and set(value) == {"artifact_sha", "files"},
            "candidate shape is invalid")
    require(value["artifact_sha"] == expected_head and SHA.fullmatch(expected_head),
            "candidate is not bound to the reserved head")
    files = value["files"]
    require(isinstance(files, dict) and tuple(sorted(files)) == PATHS,
            "candidate paths are outside the product contract")
    normalized = {}
    for path in PATHS:
        content = files[path]
        require(isinstance(content, str), "every final product path must contain text")
        encoded = content.encode("utf-8")
        require(len(encoded) <= MAX_FILE and b"\x00" not in encoded,
                "candidate file exceeds text bounds")
        normalized[path] = content
    return normalized


def validate_compare(value, expected_head, published_head):
    require(isinstance(value, dict) and value.get("base_commit", {}).get("sha") == expected_head,
            "published comparison base mismatch")
    commits = value.get("commits")
    require(value.get("total_commits") == 1 and isinstance(commits, list) and
            len(commits) == 1 and commits[0].get("sha") == published_head,
            "publication must be one direct commit")
    files = value.get("files")
    require(isinstance(files, list) and len(files) == len(PATHS) and
            tuple(sorted(item.get("filename") for item in files
                         if isinstance(item, dict))) == PATHS and
            all(item.get("status") in {"added", "modified"} for item in files),
            "published commit changed a path outside the product contract")


def validate_candidate_path(value, runner_temp, run_id, run_attempt):
    temporary = Path(runner_temp).resolve(strict=True)
    require(temporary.is_dir(), "runner temporary root is invalid")
    expected = temporary / f"council-product-candidate-{run_id}-{run_attempt}.json"
    supplied = Path(value)
    require(supplied.is_absolute() and supplied == expected,
            "candidate path is not the fixed run-owned location")
    resolved = supplied.resolve(strict=True)
    require(resolved == expected and resolved.parent == temporary and
            stat.S_ISREG(supplied.lstat().st_mode) and not supplied.is_symlink(),
            "candidate path escaped through traversal or a symlink")
    return resolved


class GitHub:
    def __init__(self, token):
        require(isinstance(token, str) and 20 <= len(token) <= 1024 and "\n" not in token,
                "invalid action token")
        self.token = token
        self.opener = build_opener(
            ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context())
        )

    def request(self, method, url, payload=None, allowed=(200,)):
        require(url.startswith("https://api.github.com/") and "#" not in url,
                "unsupported GitHub endpoint")
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        })
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            if error.code in allowed:
                return error.code, None
            raise
        with response:
            require(response.geturl() == url, "GitHub endpoint changed")
            raw = response.read(MAX_BODY + 1)
            require(len(raw) <= MAX_BODY, "GitHub response exceeded bound")
            status = response.status
        require(status in allowed, "unexpected GitHub status")
        return status, None if not raw else json.loads(raw)

    def viewer(self):
        _, value = self.request("GET", "https://api.github.com/user")
        require(isinstance(value, dict), "viewer response is invalid")
        actual = {key: value.get(key) for key in IDENTITY}
        require(actual == IDENTITY, "publisher is not the Claude implementation seat")

    def ref(self):
        path = quote("heads/" + BRANCH, safe="/")
        status, value = self.request(
            "GET", f"https://api.github.com/repos/{REPOSITORY}/git/ref/{path}", allowed=(200, 404)
        )
        if status == 404:
            return None
        require(isinstance(value, dict) and isinstance(value.get("object"), dict),
                "branch response is invalid")
        sha = value["object"].get("sha")
        require(isinstance(sha, str) and SHA.fullmatch(sha), "branch head is invalid")
        return sha

    def create_ref_once(self, expected_head):
        try:
            self.request("POST", f"https://api.github.com/repos/{REPOSITORY}/git/refs", {
                "ref": "refs/heads/" + BRANCH, "sha": expected_head,
            }, allowed=(201,))
        except (HTTPError, URLError, TimeoutError):
            require(self.ref() == expected_head, "branch creation was indeterminate")

    def commit_once(self, expected_head, files):
        additions = [{"path": path, "contents": b64encode(files[path].encode()).decode("ascii")}
                     for path in PATHS]
        query = """mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid url}}}"""
        payload = {"query": query, "variables": {"input": {
            "branch": {"repositoryNameWithOwner": REPOSITORY, "branchName": BRANCH},
            "message": {"headline": "council-product-DECISION-003"},
            "expectedHeadOid": expected_head,
            "fileChanges": {"additions": additions},
        }}}
        try:
            _, value = self.request("POST", "https://api.github.com/graphql", payload)
            require(isinstance(value, dict) and not value.get("errors"), "commit mutation failed")
            commit = value.get("data", {}).get("createCommitOnBranch", {}).get("commit", {})
            sha = commit.get("oid")
            require(isinstance(sha, str) and SHA.fullmatch(sha), "commit response is invalid")
            return sha
        except (HTTPError, URLError, TimeoutError):
            sha = self.ref()
            require(sha is not None and sha != expected_head,
                    "commit mutation was indeterminate")
            self.verify_commit(sha, expected_head, files)
            return sha

    def verify_commit(self, sha, expected_head, files):
        _, commit = self.request("GET", f"https://api.github.com/repos/{REPOSITORY}/git/commits/{sha}")
        require(isinstance(commit, dict) and commit.get("message") == "council-product-DECISION-003",
                "unexpected published commit")
        parents = commit.get("parents")
        require(isinstance(parents, list) and len(parents) == 1 and
                parents[0].get("sha") == expected_head, "published commit parent mismatch")
        _, comparison = self.request(
            "GET",
            f"https://api.github.com/repos/{REPOSITORY}/compare/{expected_head}...{sha}?per_page=2",
        )
        validate_compare(comparison, expected_head, sha)
        for path in PATHS:
            encoded_path = quote(path, safe="/")
            _, item = self.request(
                "GET", f"https://api.github.com/repos/{REPOSITORY}/contents/{encoded_path}?ref={sha}"
            )
            require(isinstance(item, dict) and item.get("encoding") == "base64" and
                    b64decode("".join(item.get("content", "").split()), validate=True).decode("utf-8") == files[path],
                    "published file mismatch")

    def find_pr(self, sha):
        params = urlencode({"state": "open", "head": "abdulazizmqbali:" + BRANCH,
                            "base": BASE_BRANCH, "per_page": "2"})
        _, values = self.request("GET", f"https://api.github.com/repos/{REPOSITORY}/pulls?{params}")
        require(isinstance(values, list) and len(values) <= 1, "ambiguous product pull request")
        if not values:
            return None
        value = values[0]
        self.verify_pr(value, sha)
        return value["number"]

    def verify_pr(self, value, sha):
        require(isinstance(value, dict) and value.get("state") == "open" and
                value.get("head", {}).get("sha") == sha and
                value.get("head", {}).get("ref") == BRANCH and
                value.get("base", {}).get("ref") == BASE_BRANCH and
                value.get("user", {}).get("login") == IDENTITY["login"] and
                type(value.get("number")) is int and value["number"] > 0,
                "product pull request identity or binding mismatch")

    def create_pr_once(self, sha):
        try:
            _, value = self.request("POST", f"https://api.github.com/repos/{REPOSITORY}/pulls", {
                "title": "Council product DECISION-003",
                "head": BRANCH,
                "base": BASE_BRANCH,
                "body": "Bounded live product task for DECISION-003. Owner merge remains required.",
            }, allowed=(201,))
            self.verify_pr(value, sha)
            return value["number"]
        except (HTTPError, URLError, TimeoutError):
            number = self.find_pr(sha)
            require(number is not None, "pull request creation was indeterminate")
            return number


def emit(name, value):
    require(re.fullmatch(r"[a-z_]+", name) is not None, "invalid output name")
    text = str(value)
    require("\n" not in text and "\r" not in text, "invalid output value")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"{name}={text}\n")


def main():
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY,
            "publisher is restricted to the qualified product repository")
    expected_head = os.environ.get("COUNCIL_PRODUCT_EXPECTED_HEAD", "")
    require(SHA.fullmatch(expected_head) is not None, "invalid expected product head")
    candidate_path = validate_candidate_path(
        os.environ.get("COUNCIL_PRODUCT_CANDIDATE", ""),
        os.environ.get("RUNNER_TEMP", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
        os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    )
    require(os.path.getsize(candidate_path) <= MAX_BODY, "candidate bundle exceeded bound")
    with open(candidate_path, encoding="utf-8") as candidate:
        files = validate_candidate(json.load(candidate), expected_head)

    github = GitHub(os.environ.get("ACTION_GITHUB_TOKEN", ""))
    github.viewer()
    head = github.ref()
    if head is None:
        github.create_ref_once(expected_head)
        head = expected_head
    require(head == expected_head, "product branch moved before deterministic publication")
    sha = github.commit_once(expected_head, files)
    github.verify_commit(sha, expected_head, files)
    number = github.find_pr(sha)
    if number is None:
        number = github.create_pr_once(sha)
    emit("published_sha", sha)
    emit("pull_request_number", number)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("council product publication denied: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
