#!/usr/bin/env python3
"""Verify checked-in proxy configs and callback query privacy in disposable Nginx."""
import json
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

IMAGE = "nginx:1.28-alpine"
ROOT = Path(__file__).resolve().parents[2]


def docker(*args):
    return subprocess.run(["docker", *args], text=True, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()


def verify():
    with tempfile.TemporaryDirectory(prefix="jidan-nginx-") as directory:
        config = Path(directory)
        for environment in ("dev", "production"):
            (config / f"{environment}.conf").write_text((ROOT / "deploy/nginx" / f"{environment}.conf").read_text())
        # The dev upstream responds; the production upstream refuses connections. Both
        # success and proxy-error callback requests must suppress their query logs.
        (config / "upstream.conf").write_text("server { listen 3021; access_log off; error_log /dev/null crit; location / { return 200 'ok'; } }\n")
        mount = f"{config}:/etc/nginx/conf.d:ro"
        docker("run", "--rm", "--volume", mount, IMAGE, "nginx", "-t")
        container = docker("run", "--detach", "--publish", "127.0.0.1::80", "--volume", mount, IMAGE)
        try:
            port = json.loads(docker("inspect", container))[0]["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"]
            def request(host, path):
                req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host})
                try:
                    with urllib.request.urlopen(req, timeout=3) as response:
                        return response.status
                except urllib.error.HTTPError as exc:
                    return exc.code
            for _ in range(30):
                try:
                    assert request("dev-jidan.leehyowon14.dev", "/api/health?control=LOG_CONTROL") == 200
                    break
                except (OSError, AssertionError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Nginx did not become ready")
            for host, status in (("dev-jidan.leehyowon14.dev", 200), ("jidan.leehyowon14.dev", 502)):
                assert request(host, "/api/auth/google/callback?code=CALLBACK_SECRET&state=STATE_SECRET") == status
            logs = docker("logs", container)
            assert "LOG_CONTROL" in logs, "control access log missing; privacy check would be vacuous"
            assert "CALLBACK_SECRET" not in logs and "STATE_SECRET" not in logs, "callback query leaked"
            print("Nginx dev/production syntax and callback success/error query privacy verified")
        finally:
            docker("rm", "--force", container)


if __name__ == "__main__":
    verify()
