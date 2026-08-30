# Repository target

All implementation branches and pull requests for this project target
`YUGOROU/Alpaca-Trading-Hackathon` (`main`). Before a push or PR, verify:

```bash
git remote get-url yugorou
git status --short --branch
gh repo view YUGOROU/Alpaca-Trading-Hackathon --json nameWithOwner
```

Use an explicit destination when pushing from a shared checkout:

```bash
git push -u yugorou <branch>
```

Do not use a fork or similarly named repository as a PR destination.
