import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import { main, verifyWorkflow } from './automerge-ci.mjs';

const context = { sha: 'head', branch: 'fix', pr: 12 };
const policy = { noOp: { gate: 'Gate', skipped: 'Plan' }, jobs: [{ name: '^Tests$', steps: ['Run tests'] }] };
const run = (id, overrides = {}) => ({
    id,
    head_sha: 'head',
    head_branch: 'fix',
    event: 'pull_request',
    pull_requests: [{ number: 12 }],
    status: 'completed',
    conclusion: 'success',
    ...overrides,
});
const job = (name, conclusion = 'success') => ({ name, status: 'completed', conclusion });
const tested = [{ ...job('Tests'), steps: [job('Run tests')] }];
const noop = [job('Gate'), job('Plan', 'skipped'), job('Tests', 'skipped')];

test('a successful label-only run cannot authorize a merge', () => {
    assert.equal(
        verifyWorkflow([run(2)], () => noop, policy, context),
        false,
    );
});
test('a newer no-op cannot hide a pending real build', () => {
    assert.equal(
        verifyWorkflow(
            [run(1, { status: 'in_progress', conclusion: null }), run(2)],
            (id) => (id === 2 ? noop : tested),
            policy,
            context,
        ),
        false,
    );
});
test('a completed real run remains usable after an unrelated label update', () => {
    assert.equal(
        verifyWorkflow([run(1), run(2)], (id) => (id === 2 ? noop : tested), policy, context),
        true,
    );
});
const coordinatorPolicy = { ...policy, workflow: 'pr-validation.yaml' };
const coordinatorPath = '.github/workflows/pr-validation.yaml';
for (const conclusion of ['failure', 'cancelled', 'skipped', 'neutral', 'timed_out']) {
    test(`accepts the required jobs when an unrelated coordinator job concluded ${conclusion}`, () => {
        assert.equal(
            verifyWorkflow(
                [run(1), run(2, { conclusion, path: coordinatorPath })],
                () => tested,
                coordinatorPolicy,
                context,
            ),
            true,
        );
    });
    test(`rejects a non-coordinator run that concluded ${conclusion} despite passing required jobs`, () => {
        // A failed image build is outside the required jobs of a direct build-and-deploy run.
        for (const [configured, path] of [
            [policy, '.github/workflows/build-and-deploy-dev.yaml'],
            [coordinatorPolicy, '.github/workflows/build-and-deploy-dev.yaml'],
            [coordinatorPolicy, undefined],
        ]) {
            assert.equal(verifyWorkflow([run(2, { conclusion, path })], () => tested, configured, context), false);
        }
    });
}
test('does not accept required jobs from a coordinator run that is still active', () => {
    assert.equal(
        verifyWorkflow(
            [run(1, { status: 'in_progress', conclusion: null, path: coordinatorPath })],
            () => tested,
            coordinatorPolicy,
            context,
        ),
        false,
    );
});
test('missing and skipped jobs or steps fail closed', () => {
    for (const jobs of [
        [],
        [job('Tests', 'skipped')],
        [job('Tests')],
        [{ ...job('Tests'), steps: [job('Run tests', 'skipped')] }],
    ]) {
        assert.equal(
            verifyWorkflow([run(1)], () => jobs, policy, context),
            false,
        );
    }
});
test('all matrix jobs must succeed', () => {
    assert.equal(
        verifyWorkflow([run(1)], () => [...tested, job('Tests', 'failure')], policy, context),
        false,
    );
});
test('ignores other commits, branches, events and PRs', () => {
    for (const overrides of [
        { head_sha: 'old' },
        { head_branch: 'other' },
        { event: 'push' },
        { pull_requests: [{ number: 13 }] },
    ]) {
        assert.equal(
            verifyWorkflow([run(1, overrides)], () => tested, policy, context),
            false,
        );
    }
});
test('does not mistake a partially executed or failed run for a no-op', () => {
    assert.equal(
        verifyWorkflow([run(1), run(2)], (id) => (id === 2 ? [...noop, job('Other')] : tested), policy, context),
        false,
    );
});
test('API errors block merging', () => {
    assert.throws(() =>
        verifyWorkflow(
            [run(1)],
            () => {
                throw new Error('API failed');
            },
            policy,
            context,
        ),
    );
});
test('all automerge workflows use trusted policy and the shared gate', () => {
    const dir = new URL('../workflows/', import.meta.url);
    for (const file of readdirSync(dir).filter((name) => name.includes('automerge'))) {
        const yaml = readFileSync(new URL(file, dir), 'utf8');
        if (!yaml.includes('required_workflows=(')) continue;
        assert.match(yaml, /ref: \$\{\{ github.workflow_sha \}\}/, file);
        assert.match(yaml, /node \.github\/bin\/automerge-ci.mjs/, file);
        assert.doesNotMatch(yaml, /sort_by\(\.id\) \| last/, file);
    }
});


test('every configured required step must actually run', () => {
    const policies = JSON.parse(readFileSync(new URL('./automerge-ci-policy.json', import.meta.url), 'utf8'));
    for (const configured of Object.values(policies)) {
        const jobs = configured.jobs.map((required) => ({
            ...job(required.example ?? required.name.slice(1, -1)),
            steps: required.steps.map((name) => job(name)),
        }));
        for (const required of jobs) {
            for (const step of required.steps) {
                step.conclusion = 'skipped';
                assert.equal(verifyWorkflow([run(1)], () => jobs, configured, context), false, step.name);
                step.conclusion = 'success';
            }
        }
    }
});

test('reads all pages for every configured SDK workflow', () => {
    const policies = JSON.parse(readFileSync(new URL('./automerge-ci-policy.json', import.meta.url), 'utf8'));
    for (const [workflow, policy] of Object.entries(policies)) {
        const jobs = policy.jobs.map(required => ({ ...job(required.example), steps: required.steps.map(name => job(name)) }));
        const pages = endpoint => endpoint.includes('/workflows/')
            ? [{ workflow_runs: [] }, { workflow_runs: [run(1)] }]
            : [{ jobs: [] }, { jobs }];
        assert.equal(main({ REPO: 'owner/repo', HEAD_BRANCH: 'fix', HEAD_SHA: 'head' }, 12, [workflow], pages), true);
        if (jobs.length > 1) {
            assert.equal(verifyWorkflow([run(1)], () => jobs.slice(1), policy, context), false);
        }
    }
});


test('configured SDK checks name jobs and steps in the actual workflows', () => {
    const policies = JSON.parse(readFileSync(new URL('./automerge-ci-policy.json', import.meta.url), 'utf8'));
    for (const [file, policy] of Object.entries(policies)) {
        const yaml = readFileSync(new URL(`../workflows/${file}`, import.meta.url), 'utf8');
        for (const required of policy.jobs) {
            if (!required.example.startsWith('Python ')) assert.ok(yaml.includes(`name: ${required.example}`), `${file}: ${required.example}`);
            for (const name of required.steps) assert.ok(yaml.includes(`name: ${name}`), `${file}: ${name}`);
        }
    }
});
