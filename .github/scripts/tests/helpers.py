"""Test helpers: throwaway git repositories independent of global git config."""
import os
import subprocess
from pathlib import Path


class GitRepo:
    def __init__(self, tmpdir):
        self.root = Path(tmpdir)
        self._env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        self.git("init", "--initial-branch=main", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")

    def git(self, *args):
        result = subprocess.run(
            ["git", *args], cwd=self.root, env=self._env,
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message, *paths):
        self.git("add", *(paths or ["-A"]))
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def tag(self, name):
        self.git("tag", name)

    def checkout(self, branch, create=False):
        self.git("checkout", "-q", *(["-b"] if create else []), branch)

    def merge(self, branch, message):
        self.git("merge", "-q", "--no-ff", "-m", message, branch)
        return self.git("rev-parse", "HEAD")
