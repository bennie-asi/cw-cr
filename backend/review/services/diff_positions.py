import re


def diff_lines(diff):
    """Index actual file coordinates, never unified-diff display row numbers."""
    rows = {}
    path = old_path = None
    old = new = None
    old_left = new_left = 0
    hunk = 0
    for line in diff.splitlines():
        if line.startswith('diff --git a/'):
            if old_left or new_left:
                raise ValueError('GitLab diff hunk 不完整')
            old_path, path = line[len('diff --git a/'):].rsplit(' b/', 1)
            old = new = None
        elif line.startswith('@@ '):
            if old_left or new_left:
                raise ValueError('GitLab diff hunk 不完整')
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if not match or not path:
                raise ValueError('无法解析 GitLab diff 行号')
            old, old_count, new, new_count = match.groups()
            old, new = int(old), int(new)
            old_left, new_left = int(old_count or 1), int(new_count or 1)
            hunk += 1
        elif old is not None and path and not line.startswith('\\'):
            if not line or line[0] not in ' +-':
                continue
            kind = line[0]
            position = {'old_path': old_path, 'new_path': path}
            if kind in ' -':
                old_left -= 1
                position['old_line'] = old
                old += 1
            if kind in ' +':
                new_left -= 1
                position['new_line'] = new
                new += 1
            if old_left < 0 or new_left < 0:
                raise ValueError('GitLab diff 行数与 hunk 头不一致')
            row = {'position': position, 'text': line[1:], 'hunk': hunk}
            for side in ('old_line', 'new_line'):
                if side in position:
                    key = (path, side, position[side])
                    if key in rows:
                        raise ValueError('GitLab diff 包含重复行号')
                    rows[key] = row
    if old_left or new_left:
        raise ValueError('GitLab diff hunk 不完整')
    return rows


def discussion_position(diff, path, new_line=None, old_line=None):
    rows = diff_lines(diff)
    side, number = ('new_line', new_line) if new_line is not None else ('old_line', old_line)
    if type(number) is not int or number <= 0 or (path, side, number) not in rows:
        raise ValueError('模型给出的文件或行号不在本次 diff 中，已停止发布，请重新评审')
    position = rows[path, side, number]['position']
    for field, supplied in (('new_line', new_line), ('old_line', old_line)):
        if supplied is not None and (type(supplied) is not int or position.get(field) != supplied):
            raise ValueError('模型给出的新旧行号不匹配，已停止发布')
    return dict(position)


def validate_findings(diff, findings):
    if not isinstance(findings, list):
        raise ValueError('模型 findings 必须是数组')
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError('模型 finding 必须是对象')
        discussion_position(diff, finding.get('file_path'), finding.get('new_line'), finding.get('old_line'))


def _suggestion_original(rows, path, anchor, start, end):
    if type(anchor) is not int or type(start) is not int or type(end) is not int or not (0 < start <= anchor <= end):
        raise ValueError('建议替换范围必须覆盖有效的新侧评论行')
    if end - start > 100:
        raise ValueError('建议替换范围超过 100 行，已停止发布')
    try:
        selected = [rows[(path, 'new_line', number)] for number in range(start, end + 1)]
    except (KeyError, TypeError):
        raise ValueError('建议替换范围不在本次 MR 的新侧 diff 中')
    if len({row['hunk'] for row in selected}) != 1:
        raise ValueError('建议替换范围跨越多个 diff hunk')
    return '\n'.join(row['text'] for row in selected)


def ground_suggestion_originals(diff, findings):
    """Use exact source lines from the verified diff, not a model's copied original."""
    if not isinstance(findings, list):
        raise ValueError('模型 findings 必须是数组')
    rows = diff_lines(diff)
    grounded = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError('模型 finding 必须是对象')
        normalized = dict(finding)
        suggestion = finding.get('suggestion')
        if suggestion is not None:
            if not isinstance(suggestion, dict):
                raise ValueError('评审 suggestion 必须是对象或 null')
            suggestion = dict(suggestion)
            suggestion['original_code'] = _suggestion_original(
                rows,
                finding.get('file_path'),
                finding.get('new_line'),
                suggestion.get('start_line'),
                suggestion.get('end_line'),
            )
            normalized['suggestion'] = suggestion
        grounded.append(normalized)
    return grounded


def validate_suggestions(diff, findings):
    """Fail the whole review before publication if any executable suggestion is untrusted."""
    rows = diff_lines(diff)
    for finding in findings:
        suggestion = finding.get('suggestion')
        if suggestion is None:
            continue
        if not isinstance(suggestion, dict):
            raise ValueError('评审 suggestion 必须是对象或 null')
        path = finding.get('file_path')
        anchor = finding.get('new_line')
        start = suggestion.get('start_line')
        end = suggestion.get('end_line')
        original = suggestion.get('original_code')
        replacement = suggestion.get('replacement_code')
        if not isinstance(original, str) or not isinstance(replacement, str) or original == replacement:
            raise ValueError('建议替换缺少完整原代码/替换代码或没有实际变化')
        if '```' in replacement or '~~~' in replacement or '\r' in replacement:
            raise ValueError('建议替换包含 GitLab 不支持的代码围栏或换行格式')
        expected_original = _suggestion_original(rows, path, anchor, start, end)
        if expected_original != original:
            raise ValueError('建议替换原代码与 MR diff 不完全一致')
