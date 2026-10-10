// Test-only fixtures. The production bundle never imports this module's data;
// App starts with empty state and hydrates from the backend API.
import type { LineComment, PolicyState, Project, Review } from './types'

export const PROJECTS: Project[] = [
  {
    id: 'demo-api',
    name: 'demo-api',
    path: 'example-team/demo-api',
    owner: '混沌工程组',
    reviews: 48,
    blockers: 3,
    status: '运行中',
    model: 'CCSwitch · Qwen3-Coder',
    notify: '已覆盖',
    updatedAt: '2 分钟前',
  },
  {
    id: 'demo-web',
    name: 'demo-web',
    path: 'example-team/demo-web',
    owner: '前端平台组',
    reviews: 31,
    blockers: 0,
    status: '运行中',
    model: '继承全局模型',
    notify: '继承全局',
    updatedAt: '18 分钟前',
  },
  {
    id: 'demo-operator',
    name: 'demo-operator',
    path: 'example-team/demo-operator',
    owner: '平台基础设施组',
    reviews: 12,
    blockers: 1,
    status: '待配置',
    model: '未配置',
    notify: '未配置',
    updatedAt: '昨天',
  },
  {
    id: 'demo-observability',
    name: 'demo-observability',
    path: 'example-team/demo-observability',
    owner: '可观测性组',
    reviews: 27,
    blockers: 0,
    status: '运行中',
    model: 'CCSwitch · DeepSeek-V3',
    notify: '继承全局',
    updatedAt: '3 小时前',
  },
]

export const REVIEWS: Review[] = [
  {
    id: 'review-357',
    mr: '!357',
    project: 'demo-api',
    branch: 'feature/auto-review',
    source: 'GitLab Webhook',
    status: '有阻断',
    generationState: 'completed',
    publicationState: 'completed',
    notificationState: 'done',
    blockers: 2,
    comments: 6,
    createdAt: '今天 14:32',
    duration: '2m 18s',
  },
  {
    id: 'review-356',
    mr: '!356',
    project: 'demo-web',
    branch: 'fix/empty-state',
    source: '/review',
    status: '已完成',
    generationState: 'completed',
    publicationState: 'completed',
    notificationState: 'done',
    blockers: 0,
    comments: 3,
    createdAt: '今天 13:08',
    duration: '1m 42s',
  },
  {
    id: 'review-355',
    mr: '!355',
    project: 'demo-observability',
    branch: 'refactor/collector',
    source: '/improve',
    status: '已完成',
    generationState: 'completed',
    publicationState: 'failed',
    notificationState: 'unknown',
    blockers: 0,
    comments: 4,
    createdAt: '今天 11:24',
    duration: '2m 06s',
  },
  {
    id: 'review-354',
    mr: '!354',
    project: 'demo-operator',
    branch: 'release/v1.9',
    source: '手动触发',
    status: '失败',
    blockers: 0,
    comments: 0,
    createdAt: '昨天 18:50',
    duration: '42s',
  },
]

export const LINE_COMMENTS: LineComment[] = [
  {
    id: 'comment-1',
    file: 'src/service/review.ts',
    line: 118,
    level: 'P1',
    title: '异常路径会吞掉上下文',
    body: '这里直接返回空数组会让上层误判为“没有问题”。建议保留 provider 的错误信息并进入重试队列。',
    status: '待发布',
  },
  {
    id: 'comment-2',
    file: 'src/service/webhook.ts',
    line: 64,
    level: 'P1',
    title: '缺少幂等键校验',
    body: 'GitLab 重试 webhook 时可能重复创建评审任务，建议使用 event_uuid 做去重。',
    status: '已发布',
  },
  {
    id: 'comment-3',
    file: 'src/service/review.ts',
    line: 203,
    level: 'P2',
    title: '可以抽取重试策略',
    body: '当前重试间隔写死在调用方，后续可复用统一的指数退避策略。',
    status: '已解决',
  },
  {
    id: 'comment-4',
    file: 'src/service/engine.ts',
    line: 12,
    level: 'UNRATED',
    title: '引擎无法提供可信等级',
    body: '该发现未评级，门禁会按阻断处理，请人工确认风险。',
    status: '待发布',
  },
]

export const DEFAULT_POLICY: PolicyState = {
  webhookTrigger: true,
  commandReview: true,
  commandImprove: true,
  security: true,
  reliability: true,
  style: false,
  blockOnP1: true,
  prompt:
    '请使用中文输出总评。先概括本次 MR 的目的，再按阻断问题、可靠性、安全性、可维护性分组说明。所有 P0/P1 必须关联到具体文件与行号。',
}

export const OVERVIEW_STATS = {
  projects: 4,
  reviews: 118,
  recent_severe_count: 3,
  recent_severe_issues: [
    { id: 'comment-1', review_id: 'review-357', level: 'P1', title: '异常路径会吞掉上下文', project: 'demo-api', mr_iid: 357, created_at: '2026-09-30T06:32:00Z' },
    { id: 'comment-2', review_id: 'review-357', level: 'P1', title: '缺少幂等键校验', project: 'demo-api', mr_iid: 357, created_at: '2026-09-30T06:32:00Z' },
    { id: 'comment-4', review_id: 'review-357', level: 'UNRATED', title: '引擎无法提供可信等级', project: 'demo-api', mr_iid: 357, created_at: '2026-09-30T06:32:00Z' },
  ],
  daily_severe_issues: [1, 0, 2, 1, 0, 2, 3],
  daily_labels: ['09-24', '09-25', '09-26', '09-27', '09-28', '09-29', '09-30'],
}

export const REVIEW_DIFF = `diff --git a/src/service/review.ts b/src/service/review.ts
--- a/src/service/review.ts
+++ b/src/service/review.ts
@@ -116,4 +116,4 @@
 async function reviewChanges(provider) {
   try {
-    return await provider.analyze();
+    return [];
   }
@@ -201,3 +201,3 @@
 export function retryPolicy(attempt) {
   const base = 1000;
-  return base * attempt;
+  return 5000;
diff --git a/src/service/webhook.ts b/src/service/webhook.ts
--- a/src/service/webhook.ts
+++ b/src/service/webhook.ts
@@ -62,3 +62,3 @@
 export async function receiveWebhook(event) {
   const payload = event.object_attributes;
-  await queue.once(event.event_uuid, payload);
+  await queue.enqueue(payload);
diff --git a/src/service/engine.ts b/src/service/engine.ts
--- a/src/service/engine.ts
+++ b/src/service/engine.ts
@@ -10,3 +10,3 @@
 export function normalizeFinding(finding) {
   const title = finding.title;
-  return { title, level: finding.level };
+  return { title };
`
