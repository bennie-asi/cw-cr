from __future__ import annotations

import requests
from django.conf import settings

from review.integrations.errors import IntegrationNotConfiguredError
from review.models import LineComment, NotificationConfig, Review, distinct_issue_count
from review.services.finding_locations import comment_location


class WeComClient:
    @staticmethod
    def _delivery_result(response) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('errcode') != 0:
            code = payload.get('errcode') if isinstance(payload, dict) else None
            return {'ok': False, 'status_code': response.status_code, 'error': f'企业微信拒绝消息（errcode={code}）', 'response': {'errcode': code}}
        return {'ok': True, 'status_code': response.status_code, 'response': {'errcode': 0}}

    def __init__(self, config: NotificationConfig | None = None, timeout: float | None = None):
        self.config = config
        self.timeout = timeout if timeout is not None else settings.WECOM_REQUEST_TIMEOUT

    @property
    def webhook_url(self) -> str:
        if self.config and self.config.scope == NotificationConfig.Scope.PROJECT:
            return self.config.webhook_url.strip()
        return (self.config.webhook_url if self.config and self.config.webhook_url else settings.WECOM_WEBHOOK_URL).strip()

    @staticmethod
    def review_content(review: Review) -> str:
        blockers = review.blocking_comment_count
        status = '需要修改' if blockers else '通过'
        if review.status == Review.Status.FAILED:
            status = '评审失败'
        elif review.status in (Review.Status.QUEUED, Review.Status.RUNNING):
            status = '待评审' if review.status == Review.Status.QUEUED else '评审中'
        unresolved = list({comment.issue_id or comment.pk: comment for comment in
                           review.comments.exclude(status=LineComment.Status.RESOLVED)}.values())
        rank = {'P0': 0, 'P1': 1, 'UNRATED': 2, 'P2': 3}
        unresolved.sort(key=lambda item: (rank.get(item.level, 2), item.file_path, item.new_line or item.old_line or 0))
        resolved = distinct_issue_count(review.comments.filter(status=LineComment.Status.RESOLVED))
        # Free-form model prose can contradict its structured findings. Build
        # the delivery report from current comments, not from that prose.
        summary = f'**MR：{review.title or "!" + str(review.mr_iid)}**\n'
        if unresolved:
            summary += '\n**当前未解决问题（阻断优先）**\n'
            for comment in unresolved[:8]:
                level = '未评级 · 需人工确认' if comment.level == LineComment.Level.UNRATED else comment.level
                location = comment_location(comment)
                line = location['new_line'] or location['old_line'] or '行号待核对'
                summary += f'- [{level}] {comment.title}（`{comment.file_path}:{line}`）\n'
            if len(unresolved) > 8:
                summary += f'- 其余 {len(unresolved) - 8} 条请在评审详情查看。\n'
        elif review.status not in (Review.Status.QUEUED, Review.Status.RUNNING, Review.Status.FAILED):
            summary += '\n当前没有未解决的行级问题。\n'
        if resolved:
            summary += f'\n已解决：{resolved} 条。\n'
        recheck = review.recheck_result
        if recheck.get('applied'):
            summary += (f'\n本轮：新增 {recheck.get("new", 0)} 条 · 重复 {recheck.get("repeated", 0)} 条'
                        f' · 复审解决 {recheck.get("resolved", 0)} 条 · 保留待核验 {recheck.get("retained", 0)} 条。\n')
        summary += '\n结论依据当前行级评论；模型说明和完整建议可在详情查看。'
        prefix = (
            f'### 智审台 · MR !{review.mr_iid} 评审结果\n'
            f'> 项目：{review.project.name}\n'
            f'> 仓库：{review.repository_path or review.project.gitlab_project_path}\n'
            f'> 结论：**{status}**\n'
            f'> 阻断问题：{blockers} 条\n'
            f'> 行级评论：{review.comment_count} 条\n'
        )
        suffix = f'\n> [查看评审详情]({review.gitlab_url or "#"})'
        budget = max(0, 3500 - len((prefix + suffix + '\n…').encode('utf-8')))
        encoded = summary.encode('utf-8')
        summary = encoded[:budget].decode('utf-8', errors='ignore') + ('…' if len(encoded) > budget else '')
        return prefix + '\n' + summary + suffix

    def send_review(self, review: Review) -> dict:
        if self.config and not self.config.enabled:
            return {'ok': True, 'skipped': True, 'message': '项目通知已关闭'}
        webhook_url = self.webhook_url
        if not webhook_url:
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise IntegrationNotConfiguredError('企业微信机器人未配置 Webhook 地址')
            return {'ok': True, 'configured': False, 'message': '未配置企业微信机器人，未发起外部请求'}
        blockers = review.blocking_comment_count
        if self.config and blockers and not self.config.notify_blockers:
            return {'ok': True, 'skipped': True, 'message': '项目策略未开启阻断通知'}
        if self.config and not blockers and not self.config.notify_suggestions:
            return {'ok': True, 'skipped': True, 'message': '项目策略未开启普通评审通知'}
        content = self.review_content(review)
        response = requests.post(
            webhook_url,
            json={'msgtype': 'markdown', 'markdown': {'content': content}},
            timeout=self.timeout,
        )
        return self._delivery_result(response)

    def send_merge_result(self, review: Review, result: dict | None = None, status: str = 'unknown') -> dict:
        """Send one bounded Chinese merge-result card.

        The caller owns delivery-state idempotency; this client only performs
        one HTTP request and treats WeCom's business ``errcode`` as the source
        of truth.
        """
        if self.config and not self.config.enabled:
            return {'ok': True, 'skipped': True, 'message': '项目通知已关闭'}
        if self.config and not self.config.notify_merge_result:
            return {'ok': True, 'skipped': True, 'message': '项目策略未开启自动合并结果通知'}
        webhook_url = self.webhook_url
        if not webhook_url:
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise IntegrationNotConfiguredError('企业微信机器人未配置 Webhook 地址')
            return {'ok': True, 'configured': False, 'message': '未配置企业微信机器人，未发起外部请求'}
        state_labels = {
            'merged': '已自动合并',
            'pending': '合并请求已接受，等待 GitLab 完成',
            'blocked': '门禁阻断，未合并',
            'unknown': '合并结果待核对',
            'skipped': '未执行自动合并',
            'busy': '自动合并正在执行',
        }
        label = state_labels.get(status, status)
        result = result or {}
        reason = result.get('reason') or result.get('message') or ''
        content = (
            f'### 智审台 · MR !{review.mr_iid} 自动合并结果\n'
            f'> 项目：{review.project.name}\n'
            f'> 仓库：{review.repository_path or review.project.gitlab_project_path}\n'
            f'> 结果：**{label}**\n'
        )
        if reason:
            content += f'> 说明：{reason}\n'
        if review.gitlab_url:
            content += f'> [查看 MR]({review.gitlab_url})'
        encoded = content.encode('utf-8')
        if len(encoded) > 3500:
            content = encoded[:3497].decode('utf-8', errors='ignore') + '…'
        response = requests.post(
            webhook_url,
            json={'msgtype': 'markdown', 'markdown': {'content': content}},
            timeout=self.timeout,
        )
        return self._delivery_result(response)

    def send_test(self) -> dict:
        """Send a non-review test card without creating a fake Review row."""
        if self.config and not self.config.enabled:
            return {'ok': True, 'skipped': True, 'message': '通知已关闭'}
        webhook_url = self.webhook_url
        if not webhook_url:
            if not settings.REVIEW_ALLOW_TEST_FALLBACK:
                raise IntegrationNotConfiguredError('企业微信机器人未配置 Webhook 地址')
            return {'ok': True, 'configured': False, 'message': '未配置企业微信机器人，未发起外部请求'}
        response = requests.post(
            webhook_url,
            json={'msgtype': 'markdown', 'markdown': {'content': '### 智审台连接测试\n> 企业微信机器人配置正常。'}},
            timeout=self.timeout,
        )
        return self._delivery_result(response)
