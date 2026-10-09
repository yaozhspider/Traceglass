from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import AsyncSQLAlchemyStorage
from fastapi.testclient import TestClient

from traceglass.agents import AGENT_ID, analyst_record
from traceglass.api.workspaces import create_workspace_router
from traceglass.workspaces import ProductLocalWorkspaceManager, ProductWorkspaceService


@pytest.fixture
def product(tmp_path: Path):
    storage = AsyncSQLAlchemyStorage(url=f"sqlite+aiosqlite:///{tmp_path / 'native.db'}")
    manager = ProductLocalWorkspaceManager(basedir=str(tmp_path / "files"))
    service = ProductWorkspaceService(tmp_path, storage, manager, AGENT_ID)
    app = create_app(storage=storage, message_bus=InMemoryMessageBus(), workspace_manager=manager)
    app.include_router(create_workspace_router(service))
    with TestClient(app) as client:
        client.portal.call(storage.upsert_agent, "owner", analyst_record("owner", 4))
        client.headers["X-User-ID"] = "owner"
        yield client, service, manager


def workspace(client: TestClient, name: str = "分析工作区") -> dict:
    response = client.post("/product/workspaces", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize('selection,expected', [(None, 'default'), ('default', 'default'), ('bypass', 'bypass')])
def test_session_creation_persists_execution_permission(product, selection, expected):
    client, service, _ = product
    wid = workspace(client)['workspace_id']
    body = {'name': '权限验证'}
    if selection is not None:
        body['permission_mode'] = selection
    response = client.post(f'/product/workspaces/{wid}/sessions', json=body)
    assert response.status_code == 201, response.text
    saved = client.portal.call(service.storage.get_session, 'owner', AGENT_ID, response.json()['session_id'])
    assert saved.state.permission_context.mode.value == expected


@pytest.mark.parametrize('selection', ['dont_ask', 'accept_edits', 'unknown', None])
def test_product_rejects_unsupported_permission_modes(product, selection):
    client, _, _ = product
    wid = workspace(client)['workspace_id']
    response = client.post(f'/product/workspaces/{wid}/sessions', json={'permission_mode': selection})
    assert response.status_code == 422


def test_workspace_upload_preview_download_and_restart_metadata(product):
    client, service, manager = product
    created = workspace(client)
    wid = created["workspace_id"]
    assert wid.startswith("tgws-") and created["task_count"] == 0
    blob = b"\x7fELF\x00\xffbinary evidence"
    response = client.post(f"/product/workspaces/{wid}/samples", files={"file": ("sample.bin", blob)})
    assert response.status_code == 201, response.text
    sample = response.json()
    assert sample["sha256"] == hashlib.sha256(blob).hexdigest()
    assert sample["size"] == len(blob)
    assert sample["sample_path"].startswith("samples/")
    assert sample["sample_path"] == "samples/" + sample["reverse_sample_path"]
    assert not sample["reverse_sample_path"].startswith("samples/")
    assert Path(sample["sandbox_path"]).is_file()
    listing = client.get(f"/product/workspaces/{wid}/files", params={"path": "samples"}).json()
    assert listing["entries"][0]["path"] == sample["sample_path"]
    preview = client.get(f"/product/workspaces/{wid}/preview", params={"path": sample["sample_path"]}).json()
    assert preview["encoding"] == "binary" and "7f 45 4c 46" in preview["hex"]
    download = client.get(f"/product/workspaces/{wid}/download", params={"path": sample["sample_path"]})
    assert download.content == blob
    assert "attachment" in download.headers["content-disposition"]
    recovered = ProductWorkspaceService(service.data_dir, service.storage, manager, AGENT_ID)
    detail = client.portal.call(recovered.detail, "owner", wid)
    assert detail["samples"] == [sample]


def test_workspaces_isolate_files_and_owners(product):
    client, _, _ = product
    first, second = workspace(client, "A"), workspace(client, "B")
    uploaded = client.post(f"/product/workspaces/{first['workspace_id']}/samples", files={"file": ("a.txt", b"private")}).json()
    response = client.get(f"/product/workspaces/{second['workspace_id']}/preview", params={"path": uploaded["sample_path"]})
    assert response.status_code == 404
    client.headers["X-User-ID"] = "another"
    assert client.get(f"/product/workspaces/{first['workspace_id']}").status_code == 404
    assert client.get("/product/workspaces").json() == {"workspaces": []}


def test_path_traversal_and_symlink_preview_rejected(product, tmp_path):
    client, service, _ = product
    wid = workspace(client)["workspace_id"]
    root = service.workspace_root("owner", wid)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "escape.txt").symlink_to(outside)
    for path in ("../outside.txt", str(outside), "escape.txt", "nested/../../outside.txt"):
        assert client.get(f"/product/workspaces/{wid}/preview", params={"path": path}).status_code in {400, 404}
    response = client.post(f"/product/workspaces/{wid}/samples", files={"file": ("../../escape.bin", b"bytes")})
    assert response.status_code == 400


def test_same_name_uploads_preserve_both_contents(product):
    client, _, _ = product
    wid = workspace(client)["workspace_id"]
    one = client.post(f"/product/workspaces/{wid}/samples", files={"file": ("same.bin", b"one")}).json()
    two = client.post(f"/product/workspaces/{wid}/samples", files={"file": ("same.bin", b"two")}).json()
    assert one["sample_path"] != two["sample_path"]
    for expected, sample in ((b"one", one), (b"two", two)):
        assert client.get(f"/product/workspaces/{wid}/download", params={"path": sample["sample_path"]}).content == expected


def test_upload_size_and_preview_bounds(product, monkeypatch):
    client, service, _ = product
    wid = workspace(client)["workspace_id"]
    monkeypatch.setattr("traceglass.workspaces.files.MAX_UPLOAD_BYTES", 4)
    response = client.post(f"/product/workspaces/{wid}/samples", files={"file": ("large.bin", b"12345")})
    assert response.status_code == 413
    assert not list(service.workspace_root("owner", wid).rglob("*.part"))
    assert client.get(f"/product/workspaces/{wid}").json()["samples"] == []
    text = "中文" * 50_000
    (service.workspace_root("owner", wid) / "report.md").write_text(text)
    response = client.get(f"/product/workspaces/{wid}/preview", params={"path": "report.md"}).json()
    assert response["encoding"] == "utf-8"
    assert response["truncated"] is True
    assert len(response["content"].encode()) <= 256 * 1024


def test_fresh_native_sessions_share_workspace_and_sample_associations(product):
    client, service, manager = product
    wid = workspace(client)["workspace_id"]
    sample = client.post(f"/product/workspaces/{wid}/samples", files={"file": ("sample.txt", b"fixture")}).json()
    body = {"name": "分析", "sample_id": sample["sample_id"]}
    first = client.post(f"/product/workspaces/{wid}/sessions", json=body)
    second = client.post(f"/product/workspaces/{wid}/sessions", json=body)
    assert first.status_code == second.status_code == 201
    assert first.json()["session_id"] != second.json()["session_id"]
    detail = client.get(f"/product/workspaces/{wid}").json()
    assert len(detail["tasks"]) == 2
    assert all(task["sample_id"] == sample["sample_id"] for task in detail["tasks"])
    native = client.portal.call(service.storage.get_session, "owner", AGENT_ID, first.json()["session_id"])
    assert native.config.workspace_id == wid
    workspace_instance = client.portal.call(manager.get_workspace, "owner", AGENT_ID, native.id, wid)
    assert Path(workspace_instance.workdir) == service.workspace_root("owner", wid)


def test_existing_native_session_is_listed_without_moving_legacy_root(product):
    from agentscope.app.storage import SessionConfig

    client, service, manager = product
    native = client.portal.call(service.storage.upsert_session, "owner", AGENT_ID, SessionConfig(workspace_id="old-binding", name="旧分析"))
    listed = client.get("/product/workspaces").json()["workspaces"]
    assert len(listed) == 1
    assert listed[0]["workspace_id"] == "old-binding" and listed[0]["is_legacy"]
    detail = client.get("/product/workspaces/old-binding").json()
    assert detail["tasks"][0]["session_id"] == native.id
    assert service.workspace_root("owner", "old-binding") == Path(manager._basedir) / AGENT_ID


def test_legacy_sample_import_is_bounded_and_cached(product, monkeypatch):
    from agentscope.app.storage import SessionConfig

    client, service, manager = product
    client.portal.call(service.storage.upsert_session, "owner", AGENT_ID, SessionConfig(workspace_id="old-samples", name="旧样本"))
    root = Path(manager._basedir) / AGENT_ID
    (root / "samples").mkdir(parents=True)
    (root / "samples" / "benign-true").write_bytes(b"static bytes")
    detail = client.get("/product/workspaces/old-samples").json()
    assert detail["samples"][0]["sample_path"] == "samples/benign-true"
    assert detail["samples"][0]["reverse_sample_path"] == "benign-true"
    assert detail["samples"][0]["sha256"] == hashlib.sha256(b"static bytes").hexdigest()

    def no_rehash(*args):
        raise AssertionError("Legacy imports must be cached across requests")

    monkeypatch.setattr("traceglass.workspaces.files.discover_legacy_samples", no_rehash)
    assert client.get("/product/workspaces/old-samples").json()["samples"] == detail["samples"]


def test_cross_workspace_sample_attachment_rejected(product):
    client, _, _ = product
    first, second = workspace(client), workspace(client)
    sample = client.post(f"/product/workspaces/{first['workspace_id']}/samples", files={"file": ("a.bin", b"a")}).json()
    response = client.post(f"/product/workspaces/{second['workspace_id']}/sessions", json={"name": "wrong", "sample_id": sample["sample_id"]})
    assert response.status_code == 404
    assert client.get(f"/product/workspaces/{second['workspace_id']}").json()["tasks"] == []


def test_upload_refuses_symlink_sample_directory(product, tmp_path):
    client, service, _ = product
    wid = workspace(client)["workspace_id"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (service.workspace_root("owner", wid) / "samples").symlink_to(outside, target_is_directory=True)
    response = client.post(f"/product/workspaces/{wid}/samples", files={"file": ("a.bin", b"a")})
    assert response.status_code == 404
    assert not list(outside.iterdir())


def test_interrupted_upload_removes_partial_bytes(tmp_path):
    from traceglass.workspaces.files import upload_sample

    class InterruptedUpload:
        filename = "interrupted.bin"
        calls = 0
        closed = False

        async def read(self, size):
            self.calls += 1
            if self.calls == 1:
                return b"first bytes"
            raise ConnectionError("disconnected")

        async def close(self):
            self.closed = True

    upload = InterruptedUpload()
    with pytest.raises(ConnectionError):
        asyncio.run(upload_sample(tmp_path, upload))
    assert not list((tmp_path / "samples").iterdir())
    assert upload.closed


def test_docker_product_paths_isolate_workspace_and_hash_untrusted_user(tmp_path):
    from traceglass.workspaces import ProductDockerWorkspaceManager

    manager = ProductDockerWorkspaceManager(basedir=str(tmp_path))
    first = manager.host_root("../../other", AGENT_ID, "tgws-one")
    second = manager.host_root("../../other", AGENT_ID, "tgws-two")
    assert first != second and first.resolve().is_relative_to(tmp_path)
    assert manager.host_root("../../other", "worker-agent", "tgws-one") == first
    assert manager.agent_path("../../other", AGENT_ID, "tgws-one", "samples/a.bin") == "/workspace/samples/a.bin"


def test_upload_declared_body_limit_rejected_before_multipart_parsing(product):
    client, _, _ = product
    wid = workspace(client)["workspace_id"]
    response = client.post(f"/product/workspaces/{wid}/samples", content=b"not parsed multipart",
                           headers={"Content-Length": str(101 * 1024 * 1024), "Content-Type": "multipart/form-data"})
    assert response.status_code == 413


def test_chunked_upload_body_limit_is_also_enforced(product, monkeypatch):
    client, _, _ = product
    wid = workspace(client)["workspace_id"]
    monkeypatch.setattr("traceglass.workspaces.files.MAX_UPLOAD_BYTES", 4)
    async def unexpected_upload(*args):
        raise AssertionError("Oversized request must be rejected during parsing")
    monkeypatch.setattr("traceglass.workspaces.files.upload_sample", unexpected_upload)
    payload = (b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="large.bin"\r\n'
               b'Content-Type: application/octet-stream\r\n\r\n' + b"x" * 70_000 + b"\r\n--boundary--\r\n")
    response = client.post(f"/product/workspaces/{wid}/samples", content=iter([payload]),
                           headers={"Content-Type": "multipart/form-data; boundary=boundary"})
    assert response.status_code == 413
