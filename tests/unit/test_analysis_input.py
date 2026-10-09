import asyncio
import hashlib
from pathlib import Path

import pytest
from traceglass.integrations.analysis_input import AnalysisInput


def test_stage_nested_pyd_for_same_host_ida_without_touching_original(tmp_path):
    root = tmp_path/'workspace'; root.mkdir()
    source = root/'client.pyd'; source.write_bytes(b'fixture bytes, not executable')
    staging = tmp_path/'staged'
    tool = AnalysisInput(root, staging, docker=True, ida_local_files=True)
    result = asyncio.run(tool.prepare_analysis_input('/workspace/client.pyd'))
    target = Path(result['ida_file_path'])
    assert target.is_relative_to(staging)
    assert target.read_bytes() == source.read_bytes()
    assert target.name.endswith('.pyd')
    assert result['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result['rea_file_path'] == '/workspace/client.pyd'
    assert not target.stat().st_mode & 0o222
    assert source.stat().st_mode & 0o200


def test_remote_ida_has_no_guessed_host_path_and_missing_files_fail(tmp_path):
    source = tmp_path/'a'; source.write_bytes(b'a')
    tool = AnalysisInput(tmp_path, tmp_path/'stage', docker=True, ida_local_files=False)
    result = asyncio.run(tool.prepare_analysis_input('a'))
    assert result['ida_file_path'] is None
    assert result['rea_file_path'] == '/workspace/a'
    with pytest.raises(FileNotFoundError):
        asyncio.run(tool.prepare_analysis_input('/workspace/missing'))


def test_path_escape_and_symlinks_are_rejected(tmp_path):
    root = tmp_path/'root'; root.mkdir()
    secret = tmp_path/'outside'; secret.write_text('outside')
    (root/'link').symlink_to(secret)
    tool = AnalysisInput(root, tmp_path/'staged', docker=True, ida_local_files=True)
    for name in ('../outside','/etc/passwd','/workspace/../outside','link'):
        with pytest.raises(ValueError):
            asyncio.run(tool.prepare_analysis_input(name))


def test_preparation_still_requires_native_permission_in_default_mode(tmp_path):
    from agentscope.permission import PermissionContext, PermissionBehavior
    tool = AnalysisInput(tmp_path,tmp_path/'staged',docker=True,ida_local_files=True).tool()
    decision = asyncio.run(tool.check_permissions({'path':'sample.pyd'},PermissionContext()))
    assert decision.behavior == PermissionBehavior.ASK
