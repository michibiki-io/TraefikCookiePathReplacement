"""Exercise a local plugin through Traefik and a loopback HTTP backend."""

import http.client
import http.server
import pathlib
import socket
import subprocess
import tempfile
import threading
import time


MODULE = "github.com/michibiki-io/TraefikCookiePathReplacement"
TRAEFIK_IMAGE = "traefik:v3.7.19"
ROOT = pathlib.Path(__file__).resolve().parents[1]


class CookieBackend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Set-Cookie", "test-session=one; Path=/before/old/after")
        self.send_header("Set-Cookie", "unrelated=two; Path=/before/old/after")
        self.end_headers()
        self.wfile.write(b"backend")


def verify_response(port, path):
    for attempt in range(30):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read()
            if response.status != 404:
                assert response.status == 200 and body == b"backend", (response.status, body)
                cookies = response.headers.get_all("Set-Cookie") or []
                expected = [
                    "test-session=one; Path=/before/new/after",
                    "unrelated=two; Path=/before/old/after",
                ]
                assert sorted(cookies) == sorted(expected), (cookies, expected)
                print(f"{path}: Traefik rewrote only the cookie whose name matched.")
                return
        except OSError:
            if attempt == 29:
                raise
        finally:
            connection.close()
        time.sleep(1)
    raise RuntimeError(f"Traefik never served the plugin router for {path}")


def main():
    with (
        http.server.ThreadingHTTPServer(("127.0.0.1", 0), CookieBackend) as backend,
        tempfile.TemporaryDirectory() as directory,
    ):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        dynamic = pathlib.Path(directory) / "dynamic.yml"
        replacement = '{{ `{{before}}/new/{{after}}` }}'
        dynamic.write_text(f"""http:
  routers:
    smoke:
      rule: Path(`/`)
      middlewares: [cookie-path]
      service: backend
    legacy:
      rule: Path(`/legacy`)
      middlewares: [legacy-cookie-path]
      service: backend
  middlewares:
    cookie-path:
      plugin:
        cookie-path:
          replacements:
            - name_regex: "test-.*"
              original: "(?P<before>.*)/old/(?P<after>.*)"
              replacement: "{replacement}"
    legacy-cookie-path:
      plugin:
        cookie-path:
          replacements:
            - name: "test-.*"
              original: "(?P<before>.*)/old/(?P<after>.*)"
              replacement: "{replacement}"
  services:
    backend:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:{backend.server_port}"
""", encoding="utf-8")

        worker = threading.Thread(target=backend.serve_forever)
        worker.start()
        try:
            container = subprocess.check_output(
                ["docker", "run", "--detach", "--network", "host",
                 "--volume", f"{ROOT}:/plugins-local/src/{MODULE}:ro",
                 "--volume", f"{dynamic}:/test/dynamic.yml:ro",
                 TRAEFIK_IMAGE,
                 f"--entrypoints.web.address=127.0.0.1:{port}",
                 "--providers.file.filename=/test/dynamic.yml",
                 "--experimental.abortonpluginfailure=true",
                 f"--experimental.localplugins.cookie-path.modulename={MODULE}"],
                text=True,
            ).strip()
            try:
                verify_response(port, "/")
                verify_response(port, "/legacy")
            except Exception:
                subprocess.run(["docker", "logs", container], check=False)
                raise
            finally:
                subprocess.run(["docker", "rm", "--force", container], check=True, stdout=subprocess.DEVNULL)
        finally:
            backend.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
