# GitLab Periodic Sync Implementation Plan

> **For agentic workers:** Execute the approved tasks inline in this session. The referenced executing-plans and using-git-worktrees skills are unavailable; use the managed Codex worktree and test-driven-development with verification at each stage.

**Goal:** Support periodic GitLab MR discovery alongside Webhooks, processing only new MR/new commits after activation.

**Architecture:** A supervised Django synchronizer persists activation baselines and schedules independently of the existing review worker. Both automatic trigger paths share SHA-aware enqueueing under the existing single-host MR lock; merge gates remain in the review workflow.

**Tech Stack:** Django/DRF, SQLite, React/TypeScript, Vitest, Supervisor, Docker Compose, GitHub Actions/GHCR.

---

### Task 1: Configuration and activation baseline

Files: add `backend/review/sync_views.py`, `backend/review/services/gitlab_sync.py`, `backend/review/tests/test_gitlab_sync.py`; modify `backend/review/models.py`, `backend/review/urls.py`, `backend/review/permissions.py`; generate `backend/review/migrations/0028_gitlab_periodic_sync.py` and `0029_review_sync_source.py`.

Verified before publishing: 565 Django tests, 344 frontend tests, 5 Docker checks; migration drift check and production frontend build passed. Activation, bounded reads, source/status mapping, dual-trigger deduplication, stale/closed MR guards, failure isolation, scheduler and settings UI are implemented. Publication and server verification follow Task 5.

- [ ] Write API tests before implementation. Authenticate a platform admin, configure one repository, mock only GitLab network reads, then assert:

```python
response = client.patch(f'/api/projects/{project.pk}/sync/',
    {'enabled': True, 'interval_seconds': 60}, format='json')
self.assertEqual(response.status_code, 200)
self.assertTrue(response.json()['enabled'])
self.assertEqual(ReviewJob.objects.count(), 0)
```

- [ ] Run `python backend/manage.py test review.tests.test_gitlab_sync --noinput`; expect an assertion failure because the route returns 404.
- [ ] Implement configuration models, strict writable fields, project-authorized routes and activation reads. Hold `resource_lock(f'gitlab-sync:{project.pk}')` across baseline capture and save; keep network calls outside database transactions. Persist baseline only after all enabled repositories were read completely. Reject missing SHA and non-remote GitLab clients.

```python
class ProjectGitLabSync(TimeStampedModel):
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='gitlab_sync')
    enabled = models.BooleanField(default=False)
    interval_seconds = models.PositiveIntegerField(default=60,
        validators=[MinValueValidator(30), MaxValueValidator(3600)])

class GitLabSyncBaseline(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='sync_baselines')
    repository_path = models.CharField(max_length=255)
    initial_shas = models.JSONField(default=dict)
```

- [ ] Extend tests with unavailable connection, incomplete pages, missing SHA, interval validation and admin/member/outsider access. Generate the migration and run API tests until all pass.

### Task 2: Shared automatic enqueue and polling

Files: add `backend/review/services/automatic_reviews.py`; modify `backend/review/services/gitlab_sync.py`, `backend/review/webhooks.py`, `backend/review/services/review_runner.py`, `backend/review/models.py`; extend `backend/review/tests/test_gitlab_sync.py` and add `backend/review/tests/test_automatic_reviews.py`.

- [ ] Write tests invoking `synchronize_project(config)` after activation. Assert baseline SHA creates no job; new IID/new SHA creates one job; a repeated scan and Webhook produce one job; distinct repositories sharing IID create separate jobs.

```python
synchronize_project(config)
self.assertEqual(ReviewJob.objects.count(), 0)
upstream_items[0]['sha'] = 'b' * 40
synchronize_project(config)
synchronize_project(config)
self.assertEqual(ReviewJob.objects.count(), 1)
```

- [ ] Run the new tests and observe missing synchronization/duplicate behavior failures before implementation.
- [ ] Add `enqueue_automatic_review(project, repository_path, mr, source, payload=None)` returning `(job, created)` or `(None, False)` for ignored MR. Within the MR lock, check baseline, completed full-review coverage and active jobs; use a deterministic SHA event key and the existing locked queue creation helper. Store `automatic_head_sha` in the payload. Real MR Webhooks fetch authoritative MR metadata after secret/policy validation, then call this helper; command Webhooks retain current handling.

```python
identity = [str(project.pk), repository_path, int(mr['iid']), head_sha, ReviewJob.Kind.REVIEW]
event_uuid = 'automatic:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
```

- [ ] Implement bounded paginated reads, baseline creation for newly bound repositories, per-repository failure isolation, last-result persistence and exponential retry capped at 900 seconds. At automatic execution, compare fetched head SHA to `payload['automatic_head_sha']` before AI analysis. Add `Review.Source.SYNC = '定时同步', '定时同步'` and its migration.
- [ ] Verify draft/paused/closed handling, failed job exhaustion, completed manual-review coverage, busy MR catch-up and same-event race behavior. Run the synchronization, Webhook, review retry and multi-repository suites together.

### Task 3: Supervised scheduler

Files: add `backend/review/management/commands/sync_gitlab_mrs.py`, `backend/review/tests/test_gitlab_sync_command.py`; modify `docker/supervisord.conf`.

- [ ] Write command tests using persisted due/not-due configurations and `call_command('sync_gitlab_mrs')`; assert due config is scanned, disabled/paused projects are skipped, and a manual request sets the next check due. Observe command-missing failure.
- [ ] Implement `--watch`, `--interval` (default 2 seconds) and `--limit` (default 5). Close old database connections per iteration, serialize projects under their sync locks, recover interrupted scans on the next due execution.

```ini
[program:sync]
command=python manage.py sync_gitlab_mrs --watch --interval 2
directory=/app/backend
priority=20
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
```

- [ ] Run command tests, baseline restart tests and Docker configuration checks.

### Task 4: Project settings UI

Files: add `src/ProjectSync.tsx`, `src/ProjectSync.test.tsx`, `src/project-sync.css`; modify `src/api.ts`, `src/types.ts`, `src/App.tsx`.

- [ ] Write component tests before implementation: persisted disabled settings, enabling and interval save, failed-save draft retention, requesting sync without modifying configuration, read-only member view, and project-switch isolation.
- [ ] Run `npm run test:ci -- src/ProjectSync.test.tsx`; expect missing component/controls failure.
- [ ] Add typed API methods and a project-keyed self-contained card inside project review policy settings. Load configuration on mount, display safe errors and status, disable writes for read-only members and while requests are pending, validate integer 30–3600 seconds, refresh status periodically without clobbering edits.

```typescript
projectSync: (id: string) => request<GitLabSyncStatus>(`/projects/${id}/sync/`),
saveProjectSync: (id: string, enabled: boolean, interval_seconds: number) =>
  request<GitLabSyncStatus>(`/projects/${id}/sync/`, {
    method: 'PATCH', body: JSON.stringify({ enabled, interval_seconds }),
  }),
runProjectSync: (id: string) => request<GitLabSyncStatus>(`/projects/${id}/sync/run/`, { method: 'POST' }),
```

- [ ] Add the “定时同步” review source to frontend types. Run component tests and build. Verify project policies retain their existing save behavior.

### Task 5: Documentation, validation and deployment

Files: modify `README.md`, `docs/integrations.md`, `docs/deployment.md`; update this plan with checked tasks after verification.

- [ ] Document callback URL/Secret, outbound-only polling, activation baseline behavior, interval and manual sync, scheduler process, retries and single-host lock scope.
- [ ] Run `python backend/manage.py makemigrations --check --dry-run`, `python backend/manage.py test --noinput`, `npm run test:ci`, `npm run build`, `git diff --check`; expected no migration drift, all tests passing and production build exit 0.
- [ ] Commit only this isolated worktree's changes. Push to GitHub and trigger the existing Docker workflow, verify test/build success and record immutable GHCR digest.
- [ ] Via the authorized Chrome JumpServer terminal, back up SQLite and existing Compose deployment configuration, update the image digest and wait for health. Verify API, web, worker and sync processes.
- [ ] Configure both approved workspaces for 60-second sync through authenticated API. Re-read settings, request a read-only-baseline scan and verify all four repositories plus the existing three MR baselines. Assert no review job was created for baseline commits. Verify Chrome displays saved settings and report the deployed address and verification results.
