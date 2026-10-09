import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceLink } from '../src/features/workspaces/workspaceData.ts';
test('workspace links retain relative file identity and decode once', () => {
  assert.equal(
    workspaceLink('/workspace/artifacts/report%20one.md'),
    'artifacts/report one.md',
  );
  assert.equal(workspaceLink('reports/review.md'), 'reports/review.md');
  assert.equal(workspaceLink('report.md'), 'report.md');
});
test('external and unsafe paths never become workspace navigation', () => {
  for (const path of [
    'https://example.com/a',
    'javascript:alert(1)',
    '//example.com/a',
    '/etc/passwd',
    '../a',
    'artifacts/../../a',
    'artifacts/%2e%2e/a',
    'artifacts/%252e%252e/a',
    'file:///a',
    'a\\b',
    'a%00b',
    '#section',
  ])
    assert.equal(workspaceLink(path), null, path);
});
test('report links resolve relative to the report while remaining inside the workspace', () => {
  assert.equal(
    workspaceLink('evidence.json', 'reports/review.md'),
    'reports/evidence.json',
  );
  assert.equal(
    workspaceLink('../evidence.json', 'reports/review.md'),
    'evidence.json',
  );
  assert.equal(workspaceLink('../../secret', 'reports/review.md'), null);
  assert.equal(
    workspaceLink('/workspace/artifacts/a.md', 'reports/review.md'),
    'artifacts/a.md',
  );
});
