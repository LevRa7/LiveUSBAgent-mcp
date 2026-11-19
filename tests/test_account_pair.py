"""Cabinet: confirming a pairing code in the browser.

Pairing is the one join flow where nobody copies a link key: the device asks the
gateway for a short code, the owner opens ``/account/pair?code=...``, sees which
machine is asking and confirms it. These tests pin the route guards (session,
CSRF, Origin), the 404/409 answers and the two writes a successful approval
makes - the registry entry (through the fake bridge) and the ``account.db`` row.

The fake bridge is the one from ``tests/test_account_control.py``: one stand-in
for ``gateway.CabinetNodeControl`` keeps both files honest about the contract.
"""

import pytest
from starlette.testclient import TestClient

from account.app import create_app
from tests.test_account_control import OWN_SUBJECT, FakeBridge, login, make_config

CODE = "AB12-CD34"


def pair_metadata(**overrides):
    """What ``CabinetNodeControl.pair`` reports: metadata only, never a key."""
    metadata = {
        "hostname": "desk-pc",
        "os": "Linux",
        "arch": "x86_64",
        "created_at": 1700000000.0,
        "expires_in": 480,
        "approved": False,
        "owner_subject": "",
    }
    metadata.update(overrides)
    return metadata


@pytest.fixture()
def cabinet(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    bridge = FakeBridge()
    bridge.pairs[CODE] = pair_metadata()
    app = create_app(cfg, node_control=bridge)
    client = TestClient(app)
    session = login(client, app, cfg, monkeypatch)
    return client, app, bridge, session


def device_rows(app, session):
    return app.state.account_store.list_devices(session["tenant_id"])


def approve(client, session, code=CODE, **kwargs):
    data = {"csrf": session["csrf"]}
    data.update(kwargs.pop("data", {}))
    return client.post("/pair/%s/approve" % code, data=data, follow_redirects=False,
                       **kwargs)


# ---------------------------------------------------------------------------
# The confirmation page
# ---------------------------------------------------------------------------

def test_the_pair_page_redirects_to_the_login_and_keeps_the_code(tmp_path):
    cfg = make_config(tmp_path)
    bridge = FakeBridge()
    bridge.pairs[CODE] = pair_metadata()
    client = TestClient(create_app(cfg, node_control=bridge))

    response = client.get("/pair?code=%s" % CODE, follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/account/login?next=")
    # The device is polling and the person must land back on this confirmation.
    assert CODE in location


def test_the_pair_page_names_the_device_and_offers_the_button(cabinet):
    client, app, bridge, session = cabinet

    page = client.get("/pair?code=%s" % CODE)

    assert page.status_code == 200
    assert "desk-pc" in page.text
    assert "Linux" in page.text
    assert "/account/pair/%s/approve" % CODE in page.text
    assert session["csrf"] in page.text
    # The browser never receives a credential: the device collects its own.
    assert "lk_" not in page.text
    assert "link_key" not in page.text


def test_an_unknown_code_is_a_404(cabinet):
    client = cabinet[0]
    assert client.get("/pair?code=ZZZZ-ZZZZ").status_code == 404


def test_a_code_without_the_query_parameter_is_a_404(cabinet):
    client = cabinet[0]
    assert client.get("/pair").status_code == 404


def test_an_approved_code_is_a_409(cabinet):
    client, app, bridge, session = cabinet
    bridge.pairs[CODE]["approved"] = True

    assert client.get("/pair?code=%s" % CODE).status_code == 409


# ---------------------------------------------------------------------------
# Approving
# ---------------------------------------------------------------------------

def test_approving_links_the_device_to_the_signed_in_owner(cabinet):
    client, app, bridge, session = cabinet

    response = approve(client, session)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/account?notice=")
    assert "lk_" not in response.headers["location"]
    assert bridge.approve_calls == [{
        "code": CODE,
        "owner_subject": OWN_SUBJECT,
        "account_id": session["id"],
    }]
    rows = device_rows(app, session)
    assert [row["node_name"] for row in rows] == ["desk-pc"]
    assert rows[0]["display_name"] == "desk-pc"
    assert rows[0]["claimed_at"]


def test_the_bridge_metadata_names_the_node_but_not_a_key(cabinet):
    """The username comes from the bridge; the cabinet never invents one."""
    client, app, bridge, session = cabinet
    bridge.approve_answer = {"ok": True, "username": "desk-pc-2"}

    approve(client, session)

    rows = device_rows(app, session)
    assert [row["node_name"] for row in rows] == ["desk-pc-2"]
    # No hostname in the answer: the metadata from the pair page is the fallback.
    assert rows[0]["display_name"] == "desk-pc"


def test_an_unknown_code_links_nothing(cabinet):
    client, app, bridge, session = cabinet

    response = approve(client, session, code="ZZZZ-ZZZZ")

    assert response.status_code == 404
    assert device_rows(app, session) == []
    assert bridge.approve_calls == []


def test_a_second_approval_is_a_409_and_links_nothing_new(cabinet):
    client, app, bridge, session = cabinet
    assert approve(client, session).status_code == 303

    again = approve(client, session)

    assert again.status_code == 409
    assert len(device_rows(app, session)) == 1
    assert len(bridge.approve_calls) == 1


def test_a_code_that_expired_before_the_post_is_a_404(cabinet):
    """The page was open when the code died: the bridge is the authority."""
    client, app, bridge, session = cabinet
    bridge.approve_answer = {"ok": False, "error": "unknown_pair"}

    response = approve(client, session)

    assert response.status_code == 404
    assert device_rows(app, session) == []


def test_a_losing_race_with_another_tab_is_a_409(cabinet):
    client, app, bridge, session = cabinet
    bridge.approve_answer = {"ok": False, "error": "already_approved"}

    assert approve(client, session).status_code == 409
    assert device_rows(app, session) == []


def test_a_node_owned_by_another_tenant_is_not_relinked(cabinet):
    client, app, bridge, session = cabinet
    store = app.state.account_store
    other = store.upsert_account({
        "iss": "https://accounts.google.com", "sub": "other-sub", "aud": "aud",
        "email": "other@example.com", "email_verified": True, "name": "Other",
    })
    assert store.claim_device(other["tenant_id"], "desk-pc") is not None

    response = approve(client, session)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert device_rows(app, session) == []
    assert [row["tenant_id"] for row in store.list_devices(other["tenant_id"])] \
        == [other["tenant_id"]]


# ---------------------------------------------------------------------------
# Guards: a public page must not be triggerable from anywhere
# ---------------------------------------------------------------------------

def test_approve_without_a_csrf_token_is_refused(cabinet):
    client, app, bridge, session = cabinet

    response = client.post("/pair/%s/approve" % CODE, data={})

    assert response.status_code == 403
    assert device_rows(app, session) == []
    assert bridge.approve_calls == []


def test_approve_from_another_origin_is_refused(cabinet):
    client, app, bridge, session = cabinet

    response = approve(client, session, headers={"origin": "https://evil.example"})

    assert response.status_code == 403
    assert bridge.approve_calls == []


def test_approve_without_a_session_redirects_to_the_login(tmp_path):
    cfg = make_config(tmp_path)
    bridge = FakeBridge()
    bridge.pairs[CODE] = pair_metadata()
    client = TestClient(create_app(cfg, node_control=bridge))

    response = client.post("/pair/%s/approve" % CODE, data={"csrf": "x"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/account/login?next=")
    assert bridge.approve_calls == []


def test_approve_without_a_bridge_creates_no_device_row(cabinet):
    client, app, bridge, session = cabinet

    def failing(code, owner_subject="", account_id=""):
        bridge.approve_calls.append({"code": code, "owner_subject": owner_subject,
                                     "account_id": account_id})
        return {"ok": False, "error": "registry_unavailable"}

    bridge.approve_pair = failing
    response = approve(client, session)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert device_rows(app, session) == []
