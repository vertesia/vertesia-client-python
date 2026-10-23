# Pull request policy

`main` and `release/**` require a pull request, one approval, an up-to-date
branch, successful tests, the generated-code guard, workflow security checks,
and `PR approval gate`. Force pushes and branch deletion are blocked.

The approval gate uses trusted base-branch code and the existing
`vertesia-automerge` App. It approves ready, same-repository PRs from active
`@vertesia/engineering` members only after CI passes for the current head and
base. `human-review-required` opts out; human reviews remain untouched.

Dependabot runtime minor/patch groups and the GitHub Actions group are eligible
for automatic approval and squash merge after the same CI gate. The merge gate
checks the real bot identity, repository, head/base revision, changed files and
configured update groups. Major runtime upgrades and opted-out PRs require a human.

Configure `APP_VERTESIA_RENOVATE_AUTOMERGE_CLIENT_ID` and the secret
`APP_VERTESIA_RENOVATE_AUTOMERGE_PEM` in the `renovate-automerge` environment.
Install the App on this repository with pull requests write, contents write,
and organization members read. No merge bypass is granted to this App.

The dedicated SDK generation App may bypass PR rules to synchronize generated
sources after the upstream live tests. This exception does not apply to users
or the approval App. Version tags are outside these branch rules.

Install these workflows on each existing release branch before enabling its
required approval status. New release branches cut from main inherit them.

To reconcile a missed event, send a `ci-approve-reconcile` repository dispatch,
optionally with `client_payload.pr_number`.
