from fastapi.testclient import TestClient

from mio_core.main import app


def test_invite_csrf_and_conversation_isolation():
    with TestClient(app) as admin:
        response = admin.post(
            "/api/auth/bootstrap",
            json={
                "token": "test-bootstrap-token",
                "username": "Mizusumi",
                "password": "a-secure-test-password",
            },
        )
        assert response.status_code == 200
        admin_csrf = response.json()["csrfToken"]

        assert admin.post("/api/conversations").status_code == 403
        conversation = admin.post(
            "/api/conversations", headers={"X-CSRF-Token": admin_csrf}
        )
        assert conversation.status_code == 200

        invite = admin.post(
            "/api/admin/invites",
            headers={"X-CSRF-Token": admin_csrf},
            json={"role": "member", "expires_hours": 2},
        )
        assert invite.status_code == 200
        token = invite.json()["url"].rsplit("/", 1)[-1]

        with TestClient(app) as member:
            accepted = member.post(
                "/api/auth/invites/accept",
                json={
                    "token": token,
                    "username": "listener",
                    "password": "another-secure-password",
                },
            )
            assert accepted.status_code == 200
            member_csrf = accepted.json()["csrfToken"]
            assert member.get("/api/admin/jobs").status_code == 403
            assert member.get(f"/api/conversations/{conversation.json()['id']}").status_code == 404
            own = member.post(
                "/api/conversations", headers={"X-CSRF-Token": member_csrf}
            )
            assert own.status_code == 200
