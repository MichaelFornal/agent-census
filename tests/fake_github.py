"""A local stand-in for the three GitHub endpoints S1 and S2 use, so whole stages can run as real processes
and be killed (CLAUDE.md: test resume by killing it, not by reasoning).

The corpus is synthetic and deterministic. Some repos fail once per token, so a run meets transient failures
the way a real one does: a meta query that answers null without NOT_FOUND, and a REST tree that answers 502.
"""
import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

N_REPOS = 1300
SECRET = "Zq8Xv2Lm9Pw4Rt7Ky3Nb"
ALIAS = re.compile(r'(r\d+): repository\(owner: "([^"]+)", name: "([^"]+)"\) \{')
EXPR = re.compile(r'(f\d+): object\(expression: "HEAD:([^"]+)"\)')
OID = re.compile(r'(b\d+): object\(oid: "([0-9a-f]{40})"\)')
TREE = re.compile(r"/repos/([^/]+/[^/]+)/git/trees/([0-9a-f]{40})")


def sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def repo_files(i: int) -> list[tuple[str, str]]:
    files = [("CLAUDE.md", f"# Project {i}\n\nUses Claude to maintain project {i}.\n" + "x" * (37 * (i % 11)))]
    if i % 3 == 0:
        files.append(("docs/CLAUDE.md", f"# Docs {i}\n" + "y" * (i % 5)))
    if i % 2 == 0:
        skill = i % 4
        settings = '{"env": {"CLOUD_API_KEY": "%s"}}\n' % SECRET if i % 40 == 0 else '{"model": "sonnet"}\n'
        files += [(".claude/settings.json", settings),
                  (f".claude/skills/s{skill}/SKILL.md", f"---\nname: s{skill}\n---\nUses Claude to run skill {skill}.\n"),
                  (f".claude/skills/s{skill}/scripts/run.sh", "#!/bin/sh\necho run\n")]
    if i % 5 == 0:
        files.append((".mcp.json", '{"mcpServers": {"db": {"command": "db-mcp"}}}\n'))
    return files


class FakeGitHub:
    def __init__(self, delay: float = 0.004) -> None:
        self.delay = delay
        self.repos: dict[str, dict] = {}
        self.files: list[dict] = []
        self.blobs: dict[str, str] = {}
        self.failures: dict[tuple, int] = {}
        self.lock = threading.Lock()
        for i in range(N_REPOS):
            name = f"o{i % 50}/r{i}"
            repo = {"i": i, "fork": i % 10 == 9, "gone": i % 97 == 0, "files": dict(repo_files(i))}
            self.repos[name] = repo
            for path, text in repo["files"].items():
                self.blobs[sha(text)] = text
                self.files.append({"repo": name, "fork": repo["fork"], "path": path, "sha": sha(text),
                                   "size": len(text)})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def fail_once(self, token: str, kind: str, name: str, times: int) -> bool:
        with self.lock:
            n = self.failures.get((token, kind, name), 0)
            if n >= times:
                return False
            self.failures[(token, kind, name)] = n + 1
            return True

    def search(self, q: str, page: int, per_page: int) -> dict:
        terms = q.split()
        forks = "fork:only" in terms
        out = [f for f in self.files if f["fork"] == forks]
        for t in terms:
            k, _, v = t.partition(":")
            if k == "filename":
                out = [f for f in out if f["path"].rsplit("/", 1)[-1] == v]
            elif k == "path" and v == "/":
                out = [f for f in out if "/" not in f["path"]]
            elif k == "path":
                out = [f for f in out if f["path"].startswith(v + "/")]
            elif k == "extension":
                out = [f for f in out if f["path"].endswith("." + v)]
            elif k == "size":
                lo, hi = v.split("..")
                out = [f for f in out if int(lo) <= f["size"] <= int(hi)]
        items = [{"name": f["path"].rsplit("/", 1)[-1], "path": f["path"], "sha": f["sha"],
                  "repository": {"full_name": f["repo"], "fork": f["fork"]}}
                 for f in out[:1000][(page - 1) * per_page: page * per_page]]
        return {"total_count": len(out), "incomplete_results": False, "items": items}

    def _claude(self, name: str) -> dict | None:
        files = {p[len(".claude/"):]: t for p, t in self.repos[name]["files"].items() if p.startswith(".claude/")}
        if not files:
            return None
        entries, dirs = [], set()
        for rel, text in files.items():
            if "/" in rel:
                dirs.add(rel.split("/", 1)[0])
            else:
                entries.append({"name": rel, "type": "blob", "oid": sha(text),
                                "object": {"byteSize": len(text), "isBinary": False}})
        entries += [{"name": d, "type": "tree", "oid": sha(f"dir:{name}:{d}"), "object": {}} for d in sorted(dirs)]
        return {"oid": sha(f"tree:{name}"), "entries": entries}

    def graphql(self, token: str, q: str) -> dict:
        marks = list(ALIAS.finditer(q))
        if not marks:
            return {"data": {"rateLimit": {"remaining": 4999}}}
        data, errors = {}, []
        for n, m in enumerate(marks):
            alias, name = m[1], f"{m[2]}/{m[3]}"
            block = q[m.end(): marks[n + 1].start() if n + 1 < len(marks) else len(q)]
            repo = self.repos.get(name)
            is_meta = "claude: object(" in block
            if repo is None or repo["gone"]:
                data[alias] = None
                errors.append({"type": "NOT_FOUND", "path": [alias], "message": "Could not resolve to a Repository"})
                continue
            if is_meta and repo["i"] % 61 == 1 and self.fail_once(token, "meta", name, 1):
                data[alias] = None
                errors.append({"type": "SERVICE_UNAVAILABLE", "path": [alias], "message": "try again"})
                continue
            node: dict = {}
            if is_meta:
                node = {"nameWithOwner": name, "stargazerCount": repo["i"] % 7, "isFork": repo["fork"],
                        "isTemplate": False, "createdAt": "2025-01-01T00:00:00Z", "pushedAt": "2026-01-01T00:00:00Z",
                        "primaryLanguage": {"name": "Python"}, "licenseInfo": None,
                        "defaultBranchRef": {"target": {"oid": sha(f"head:{name}")}}, "claude": self._claude(name)}
            for a, path in EXPR.findall(block):
                text = repo["files"].get(path)
                node[a] = None if text is None else {"oid": sha(text), "byteSize": len(text), "isBinary": False}
            for a, oid in OID.findall(block):
                node[a] = ({"oid": oid, "isBinary": False, "isTruncated": False, "text": self.blobs[oid]}
                           if oid in self.blobs else None)
            data[alias] = node
        return {"data": data, **({"errors": errors} if errors else {})}

    def tree(self, token: str, name: str) -> tuple[int, dict]:
        repo = self.repos[name]
        if repo["i"] % 53 == 2 and self.fail_once(token, "tree", name, 4):  # the client's four attempts all fail
            return 502, {"message": "Bad Gateway"}
        tree = [{"path": p[len(".claude/"):], "type": "blob", "sha": sha(t), "size": len(t)}
                for p, t in repo["files"].items() if p.startswith(".claude/")]
        return 200, {"sha": sha(f"tree:{name}"), "tree": tree, "truncated": False}

    def _handler(self):
        gh = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # keep test output clean
                pass

            def _send(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                time.sleep(gh.delay)
                url = urlparse(self.path)
                token = self.headers.get("Authorization", "")
                if url.path == "/search/code":
                    qs = parse_qs(url.query)
                    self._send(200, gh.search(qs["q"][0], int(qs["page"][0]), int(qs["per_page"][0])))
                elif m := TREE.fullmatch(url.path):
                    self._send(*gh.tree(token, m[1]))
                else:
                    self._send(404, {"message": "Not Found"})

            def do_POST(self) -> None:
                time.sleep(gh.delay)
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self._send(200, gh.graphql(self.headers.get("Authorization", ""), body["query"]))

        return Handler
