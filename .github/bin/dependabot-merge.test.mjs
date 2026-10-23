import assert from 'node:assert/strict';
import { test } from 'node:test';
import { eligibilityReason } from './dependabot-merge.mjs';
const repo = 'vertesia/sdk';
const config = { ecosystem: 'pip', group: 'python-minor-patch', files: ['pyproject.toml'] };
const fixture = () => ({ user: { login: 'dependabot[bot]', type: 'Bot' }, state: 'open', draft: false,
    base: { ref: 'main' }, head: { ref: 'dependabot/pip/python-minor-patch-123', repo: { full_name: repo } }, labels: [] });
const files = [{ filename: 'pyproject.toml', status: 'modified' }];
test('accepts configured runtime minor/patch group on main and release', () => {
    const pr = fixture();
    assert.equal(eligibilityReason(pr, files, config, repo), undefined);
    pr.base.ref = 'release/1.5';
    assert.equal(eligibilityReason(pr, files, config, repo), undefined);
});
test('accepts configured Actions group only for workflow files', () => {
    const pr = fixture(); pr.head.ref = 'dependabot/github_actions/github-actions-123';
    assert.equal(eligibilityReason(pr, [{ filename: '.github/workflows/test.yaml', status: 'modified' }], config, repo), undefined);
    assert.equal(eligibilityReason(pr, files, config, repo), 'unexpected changed files');
});
test('rejects forged author, forks, drafts, opt-out, and unsupported bases', () => {
    for (const mutate of [p => p.user.type = 'User', p => p.user.login = 'someone',
        p => p.head.repo.full_name = 'fork/sdk', p => p.draft = true,
        p => p.base.ref = 'feature', p => p.labels.push({ name: 'human-review-required' })]) {
        const pr = fixture(); mutate(pr); assert.ok(eligibilityReason(pr, files, config, repo));
    }
});
test('rejects individual/major runtime upgrades and unrelated changes', () => {
    const pr = fixture(); pr.head.ref = 'dependabot/pip/pydantic-3.0.0';
    assert.ok(eligibilityReason(pr, files, config, repo));
    assert.equal(eligibilityReason(fixture(), [{ filename: 'client.py', status: 'modified' }], config, repo), 'unexpected changed files');
    assert.ok(eligibilityReason(fixture(), [], config, repo));
    assert.ok(eligibilityReason(fixture(), [{ ...files[0], status: 'renamed' }], config, repo));
});

test('merge checks exact revision twice and submits approval for the tested commit', async () => {
    const { main } = await import('./dependabot-merge.mjs');
    const { readFileSync } = await import('node:fs');
    const local = JSON.parse(readFileSync(new URL('./dependabot-policy.json', import.meta.url)));
    const pr = fixture();
    pr.head.ref = `dependabot/${local.ecosystem}/${local.group}-123`;
    pr.head.sha = 'a'.repeat(40); pr.base.sha = 'b'.repeat(40); pr.changed_files = 1;
    const env = { REPO: repo, PR_NUMBER: '12', HEAD_BRANCH: pr.head.ref, HEAD_SHA: pr.head.sha, BASE_REF: pr.base.ref, BASE_SHA: pr.base.sha };
    const writes = [];
    const api = endpoint => endpoint.includes('/statuses')
        ? [[{ context: 'PR approval gate', state: 'success', creator: { login: 'github-actions[bot]' } }]]
        : endpoint.includes('/files') ? [[{ filename: local.files[0], status: 'modified' }]] : structuredClone(pr);
    assert.equal(main(env, api, (...args) => writes.push(args)), true);
    assert.ok(writes[0][1].includes(`commit_id=${pr.head.sha}`));
    assert.ok(writes[1][1].includes('--match-head-commit'));
    assert.ok(writes[1][1].includes(pr.head.sha));
    let reads = 0; writes.length = 0;
    const moved = endpoint => {
        const value = api(endpoint);
        if (/\/pulls\/12$/.test(endpoint) && ++reads === 2) value.head.sha = 'c'.repeat(40);
        return value;
    };
    assert.equal(main(env, moved, (...args) => writes.push(args)), false);
    assert.equal(writes.length, 1);
    writes.length = 0;
    const incomplete = endpoint => endpoint.includes('/files') ? [[]] : api(endpoint);
    assert.equal(main(env, incomplete, (...args) => writes.push(args)), false);
    assert.equal(writes.length, 0);
});
