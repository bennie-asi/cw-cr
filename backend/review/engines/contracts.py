from __future__ import annotations

import json
import posixpath
import re
from typing import Any

from review.models import ReviewEngine


SCHEMA_VERSION = 1
LEVELS = {'P0', 'P1', 'P2', 'UNRATED'}


def _line(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f'{label} must be a positive integer')
    return value


def _path(value: Any) -> str:
    path = str(value or '').strip().replace('\\', '/')
    normalized = posixpath.normpath(path)
    if not path or normalized in ('.', '..') or normalized.startswith('../') or normalized.startswith('/'):
        raise ValueError('finding file_path must be a repository-relative path')
    if re.match(r'^[A-Za-z]:', normalized):
        raise ValueError('finding file_path must be a repository-relative path')
    return normalized


def normalize_engine_result(engine: str, raw: Any, version: str = '') -> dict:
    if engine not in ReviewEngine.values:
        raise ValueError(f'unsupported review engine: {engine}')
    if not isinstance(raw, dict):
        raise ValueError('review engine output must be a JSON object')
    summary = raw.get('summary')
    findings = raw.get('findings')
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError('review engine output is missing a summary')
    if not isinstance(findings, list):
        raise ValueError('review engine findings must be an array')

    normalized = []
    for index, item in enumerate(findings):
        if not isinstance(item, dict):
            raise ValueError(f'finding {index} must be an object')
        path = _path(item.get('file_path'))
        new_line = item.get('new_line')
        old_line = item.get('old_line')
        if new_line is None and old_line is None:
            raise ValueError(f'finding {index} has no diff-side line anchor')
        if new_line is not None:
            new_line = _line(new_line, f'finding {index} new_line')
        if old_line is not None:
            old_line = _line(old_line, f'finding {index} old_line')
        level = str(item.get('level') or 'UNRATED').upper()
        if level not in LEVELS:
            level = 'UNRATED'
        title = str(item.get('title') or '').strip()
        body = str(item.get('body') or '').strip()
        if not title or not body:
            raise ValueError(f'finding {index} requires a title and body')
        finding = {
            'file_path': path,
            'new_line': new_line,
            'old_line': old_line,
            'level': level,
            'title': title,
            'body': body,
        }
        anchor_code = item.get('anchor_code')
        if anchor_code is not None:
            if not isinstance(anchor_code, str) or not anchor_code.strip() or '\n' in anchor_code or '\r' in anchor_code:
                raise ValueError(f'finding {index} anchor_code must be one original source line')
            finding['anchor_code'] = anchor_code
        suggestion = item.get('suggestion')
        if suggestion is not None:
            if not isinstance(suggestion, dict):
                raise ValueError(f'finding {index} suggestion must be an object or null')
            start_line = _line(suggestion.get('start_line'), f'finding {index} suggestion start_line')
            end_line = _line(suggestion.get('end_line'), f'finding {index} suggestion end_line')
            original_code = suggestion.get('original_code')
            replacement_code = suggestion.get('replacement_code')
            if not isinstance(original_code, str) or not isinstance(replacement_code, str):
                raise ValueError(f'finding {index} suggestion requires original_code and replacement_code')
            if new_line is None or not (start_line <= new_line <= end_line):
                raise ValueError(f'finding {index} suggestion must contain its new-side anchor')
            finding['suggestion'] = {
                'start_line': start_line,
                'end_line': end_line,
                'original_code': original_code,
                'replacement_code': replacement_code,
            }
        normalized.append(finding)

    return {
        'schema_version': SCHEMA_VERSION,
        'summary': summary.strip(),
        'findings': normalized,
        'engine': {'id': engine, 'version': str(version or 'unknown')},
    }


def normalize_open_code_review_result(raw: Any, version: str = '') -> dict:
    if not isinstance(raw, dict):
        raise ValueError('OpenCodeReview output must be a JSON object')
    status = str(raw.get('status') or '').lower()
    if status and status not in {'complete', 'completed', 'success', 'passed', 'partial'}:
        raise ValueError('OpenCodeReview reported an unsuccessful review')
    comments = raw.get('comments')
    if not isinstance(comments, list):
        raise ValueError('OpenCodeReview comments must be an array')
    findings = []
    for index, comment in enumerate(comments):
        if not isinstance(comment, dict):
            raise ValueError(f'OpenCodeReview comment {index} must be an object')
        line = comment.get('start_line')
        if line is None:
            line = comment.get('line')
        finding = {
            'file_path': comment.get('path') or comment.get('file_path'),
            'new_line': line,
            'level': comment.get('level') or comment.get('severity') or 'UNRATED',
            'title': comment.get('title') or '代码评审建议',
            'body': comment.get('content') or comment.get('body'),
        }
        suggestion_code = comment.get('suggestion_code')
        existing_code = comment.get('existing_code')
        if comment.get('anchor_code') is not None:
            finding['anchor_code'] = comment['anchor_code']
        elif isinstance(existing_code, str) and existing_code.splitlines():
            finding['anchor_code'] = existing_code.splitlines()[0]
        if suggestion_code is not None or existing_code is not None:
            finding['suggestion'] = {
                'start_line': comment.get('start_line'),
                'end_line': comment.get('end_line') or comment.get('start_line'),
                'original_code': existing_code,
                'replacement_code': suggestion_code,
            }
        findings.append(finding)
    summary_value = raw.get('summary')
    if isinstance(summary_value, str) and summary_value.strip():
        summary = summary_value.strip()
    else:
        stats = summary_value if isinstance(summary_value, dict) else {}
        files_reviewed = stats.get('files_reviewed')
        if type(files_reviewed) is not int or files_reviewed < 0:
            files_reviewed = None
        if files_reviewed is not None:
            file_count = f'{files_reviewed} 个文件'
        else:
            file_count = '所选文件'
        if findings:
            summary = f'OpenCodeReview 完成了 {file_count}的评审，共整理出 {len(findings)} 条行级意见。'
        else:
            summary = f'OpenCodeReview 完成了 {file_count}的评审，未发现需要发布的行级问题。'
    return normalize_engine_result(ReviewEngine.OPEN_CODE_REVIEW, {
        'summary': summary, 'findings': findings,
    }, version)


def extract_pi_agent_final_text(raw: str) -> str:
    """Read Pi's terminal message, never treating progress or tool output as a review."""
    final_event = None
    try:
        for line in raw.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get('type'), str):
                raise ValueError('invalid event')
            if event['type'] == 'agent_settled':
                # Pi 0.86 emits this lifecycle acknowledgement after agent_end.
                if final_event is None or final_event.get('type') != 'agent_end':
                    raise ValueError('settled without a completed agent turn')
                continue
            final_event = event
    except (ValueError, TypeError) as exc:
        raise ValueError('Pi Agent 返回了无效的事件流，评审结果未保存') from exc
    if final_event is None or final_event.get('type') != 'agent_end' or final_event.get('willRetry'):
        raise ValueError('Pi Agent 未完整结束本次评审，评审结果未保存')
    messages = final_event.get('messages')
    message = messages[-1] if isinstance(messages, list) and messages else None
    if not isinstance(message, dict) or message.get('role') != 'assistant' or message.get('stopReason') != 'stop':
        raise ValueError('Pi Agent 返回的评审结果不完整或执行失败，请核对模型连接后重试')
    content = message.get('content')
    if not isinstance(content, list) or any(not isinstance(item, dict) or item.get('type') == 'toolCall' for item in content):
        raise ValueError('Pi Agent 未返回完整的最终评审文本')
    blocks = [item.get('text') for item in content if item.get('type') == 'text']
    if not blocks or any(not isinstance(block, str) for block in blocks):
        raise ValueError('Pi Agent 未返回完整的最终评审文本')
    text = ''.join(blocks).strip()
    if not text:
        raise ValueError('Pi Agent 未返回完整的最终评审文本')
    return text


def normalize_pi_agent_result(raw: str | dict, version: str = '') -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.strip())
        except (ValueError, TypeError) as exc:
            raise ValueError('Pi Agent output must be one strict JSON object') from exc
    if not isinstance(raw, dict) or set(raw).intersection({'type', 'event', 'message'}) and 'findings' not in raw:
        raise ValueError('Pi Agent output is not a final review result object')
    return normalize_engine_result(ReviewEngine.PI_AGENT, raw, version)


def normalize_pr_agent_result(raw: Any, version: str = '', mode: str = 'review') -> dict:
    if not isinstance(raw, dict):
        raise ValueError('PR-Agent structured output must be an object')
    payload = raw.get('payload', raw)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise ValueError('PR-Agent push output payload is not JSON') from exc
    if not isinstance(payload, dict):
        raise ValueError('PR-Agent push output payload is not an object')
    review = payload.get('review', payload)
    if not isinstance(review, dict):
        raise ValueError('PR-Agent output is missing the structured review object')
    if mode == 'improve':
        issues = payload.get('code_suggestions')
        if not isinstance(issues, list):
            raise ValueError('PR-Agent output is missing structured code_suggestions')
        findings = []
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                raise ValueError(f'PR-Agent suggestion {index} must be an object')
            start_line = issue.get('relevant_lines_start')
            end_line = issue.get('relevant_lines_end')
            finding = {
                'file_path': issue.get('relevant_file'),
                'new_line': start_line,
                'level': 'UNRATED',
                'title': issue.get('one_sentence_summary') or issue.get('label') or '代码改进建议',
                'body': issue.get('suggestion_content') or issue.get('one_sentence_summary'),
            }
            existing_code = issue.get('existing_code')
            if isinstance(existing_code, str) and existing_code.splitlines():
                finding['anchor_code'] = existing_code.splitlines()[0]
            if start_line is not None or end_line is not None or issue.get('existing_code') is not None or issue.get('improved_code') is not None:
                finding['suggestion'] = {
                    'start_line': start_line,
                    'end_line': end_line,
                    'original_code': issue.get('existing_code'),
                    'replacement_code': issue.get('improved_code'),
                }
            findings.append(finding)
    else:
        issues = review.get('key_issues_to_review')
        if not isinstance(issues, list):
            raise ValueError('PR-Agent output is missing structured key_issues_to_review')
        findings = []
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                raise ValueError(f'PR-Agent issue {index} must be an object')
            findings.append({
                'file_path': issue.get('relevant_file'),
                'new_line': issue.get('start_line'),
                'level': issue.get('level') or 'UNRATED',
                'title': issue.get('issue_header') or '代码评审建议',
                'body': issue.get('issue_content'),
                **({'anchor_code': issue['anchor_code']} if issue.get('anchor_code') is not None else {}),
            })
    markdown = raw.get('markdown') or payload.get('markdown') or ''
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError('PR-Agent output is missing its rendered summary')
    titles = [finding['title'] for finding in findings if finding.get('title')]
    if mode == 'improve':
        summary = (
            f'PR-Agent 本次生成 {len(findings)} 条代码改进建议。主要建议：{ "；".join(titles[:5])}。'
            if findings else 'PR-Agent 本次未生成需要发布的代码改进建议。'
        )
    else:
        summary = (
            f'PR-Agent 本次发现 {len(findings)} 个待关注问题，主要包括：{ "；".join(titles[:5])}。详细影响与建议见对应行级评论。'
            if findings else 'PR-Agent 本次未发现需要提交为行级评论的问题。'
        )
    return normalize_engine_result(ReviewEngine.PR_AGENT, {
        'summary': summary, 'findings': findings,
    }, version)
