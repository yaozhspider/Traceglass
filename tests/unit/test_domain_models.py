from __future__ import annotations
import pytest
from pydantic import ValidationError
from traceglass.domain.errors import InvariantViolation
from traceglass.domain.models import Artifact, ArtifactKind, Evidence, EvidenceKind, Finding, FindingStatus


def test_evidence_and_artifact_are_immutable_source_backed_records() -> None:
    evidence = Evidence(
        id="E-031",
        session_id="run_001",
        kind=EvidenceKind.STATIC,
        statement="函数会创建名为 Updater 的服务",
        source_ref="sample.exe!install_service:0x401000",
        confidence=0.92,
    )
    assert evidence.model_dump(mode="json")["kind"] == "static"
    with pytest.raises(ValidationError):
        evidence.statement = "被修改的事实"

    artifact = Artifact(
        id="artifact_001",
        session_id="run_001",
        name="decompile.json",
        kind=ArtifactKind.JSON,
        content_hash="a" * 64,
        uri="traceglass://runs/run_001/artifacts/decompile.json",
        size_bytes=128,
        evidence_ids=["E-031"],
    )
    assert artifact.model_dump(mode="json")["content_hash"] == "a" * 64
    with pytest.raises(ValidationError):
        artifact.size_bytes = 256


def test_finding_validates_confidence_and_reference_sets() -> None:
    finding = Finding(
        id="finding_001",
        session_id="run_001",
        claim="样本通过 Windows 服务实现持久化",
        status=FindingStatus.OPEN,
        confidence=0.87,
        evidence_ids=["E-031"],
        contradiction_ids=["E-099"],
        unknowns=["服务启动时机尚未确认"],
        next_action="动态观察服务创建调用",
    )
    assert finding.model_dump(mode="json")["status"] == "open"

    with pytest.raises(InvariantViolation):
        Finding(
            id="finding_002",
            session_id="run_001",
            claim="重复引用",
            evidence_ids=["E-031", "E-031"],
        )
    with pytest.raises(ValidationError):
        Finding(
            id="finding_003",
            session_id="run_001",
            claim="越界置信度",
            confidence=1.1,
        )
