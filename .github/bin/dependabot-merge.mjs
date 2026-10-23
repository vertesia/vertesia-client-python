import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dispatchedRevisionReason, ghApi } from './automerge-dispatch.mjs';
import { supportedBase } from './ci-approve.mjs';

export function eligibilityReason(pr, files, config, repo) {
    if (pr.user?.login !== 'dependabot[bot]' || pr.user?.type !== 'Bot') return 'not Dependabot';
    if (pr.state !== 'open' || pr.draft || !supportedBase(pr.base.ref)) return 'not a ready protected-branch PR';
    if (pr.head.repo?.full_name !== repo) return 'fork';
    if (pr.labels?.some(({ name }) => name === 'human-review-required')) return 'human review requested';
    if (!files.length || files.some(({ status }) => status !== 'modified')) return 'requires only modified files';
    const actions = /^dependabot\/github_actions\/github-actions(?:-|$)/.test(pr.head.ref);
    const runtime = pr.head.ref.startsWith(`dependabot/${config.ecosystem}/${config.group}-`);
    if (!actions && !runtime) return 'outside configured update groups';
    if (files.some(({ filename }) => actions
        ? !/^\.github\/workflows\/[^/]+\.ya?ml$/.test(filename)
        : !config.files.includes(filename))) return 'unexpected changed files';
    return undefined;
}

export function main(env, api = ghApi, run = execFileSync) {
    const repo = env.REPO;
    const number = Number(env.PR_NUMBER);
    if (!Number.isSafeInteger(number) || number < 1 || !/^[\w.-]+\/[\w.-]+$/.test(repo ?? '')) {
        throw new Error('Invalid repository or PR');
    }
    const expected = { repo, headBranch: env.HEAD_BRANCH, headSha: env.HEAD_SHA, baseRef: env.BASE_REF, baseSha: env.BASE_SHA };
    const config = JSON.parse(readFileSync(new URL('./dependabot-policy.json', import.meta.url), 'utf8'));
    const inspect = () => {
        const pr = api(`repos/${repo}/pulls/${number}`);
        const statuses = api(`repos/${repo}/commits/${expected.headSha}/statuses?per_page=100`, { pages: true }).flat();
        const files = api(`repos/${repo}/pulls/${number}/files?per_page=100`, { pages: true }).flat();
        // The full file list must be present, including PRs that exceed one API page.
        return dispatchedRevisionReason(pr, statuses, expected)
            || (files.length !== pr.changed_files ? 'incomplete file list' : eligibilityReason(pr, files, config, repo));
    };
    let reason = inspect();
    if (reason) { console.log(`Skipping #${number}: ${reason}`); return false; }
    run('gh', ['api', '--method', 'POST', `repos/${repo}/pulls/${number}/reviews`,
        '-f', 'event=APPROVE', '-f', `commit_id=${expected.headSha}`,
        '-f', 'body=Auto-approved configured Dependabot update after all required CI passed.'], { stdio: 'inherit' });
    reason = inspect();
    if (reason) { console.log(`Not merging #${number}: ${reason}`); return false; }
    run('gh', ['pr', 'merge', String(number), '--repo', repo, '--squash', '--auto',
        '--delete-branch', '--match-head-commit', expected.headSha], { stdio: 'inherit' });
    return true;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main(process.env);
