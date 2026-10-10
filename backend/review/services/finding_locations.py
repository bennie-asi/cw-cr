"""Bind review findings to source evidence from the immutable MR diff."""

import re

from review.services.diff_positions import diff_lines


def comment_finding(comment):
    return {field: getattr(comment, field) for field in
            ('file_path', 'new_line', 'old_line', 'body', 'anchor_code')}


def _first_source_quote(body):
    body = str(body or '')
    match = re.search(r'(?<!`)`([^`]+)`(?!`)', body)
    if not match or re.search(r'建议|改成|改为|替换|recommend|replace', body[:match.start()][-30:], re.I):
        return ''
    quote = match.group(1)
    if len(quote.strip()) < 12 or any(token in quote for token in ('\n', '\r', '...', '…')):
        return ''
    return quote


def finding_location(diff, finding, *, rows=None):
    location = {
        'status': 'unverified', 'new_line': None, 'old_line': None,
        'original_new_line': finding.get('new_line'),
        'original_old_line': finding.get('old_line'),
        'reason': '缺少完整原代码引用，行号待核对',
    }
    explicit = finding.get('anchor_code')
    if explicit is not None and not isinstance(explicit, str):
        return location
    body_evidence = _first_source_quote(finding.get('body'))
    if explicit:
        evidence = explicit
    else:
        # Later quotes commonly describe a proposed fix, not the reviewed code.
        evidence = body_evidence
    if (not isinstance(evidence, str) or not evidence.strip()
            or '\n' in evidence or '\r' in evidence
            or (not explicit and ('...' in evidence or '…' in evidence))
            or (not explicit and len(evidence.strip()) < 12)):
        return location
    side = 'new_line' if finding.get('new_line') is not None else 'old_line'
    number = finding.get(side)
    if type(number) is not int or number <= 0:
        location['reason'] = '缺少有效文件行号，行号待核对'
        return location
    try:
        rows = diff_lines(diff) if rows is None else rows
    except ValueError:
        location['reason'] = '原始 diff 不完整，行号待核对'
        return location
    path = finding.get('file_path')
    matches = []
    for (row_path, row_side, _), row in rows.items():
        if row_path != path or row_side != side:
            continue
        text = row['text'].strip()
        matches_code = text == evidence.strip() if explicit else evidence.strip() in text
        if matches_code:
            matches.append(row)
    declared = rows.get((path, side, number))
    if explicit and declared is not None and any(row is declared for row in matches):
        selected = declared
    elif len(matches) == 1:
        selected = matches[0]
    else:
        location['reason'] = '引用代码在该文件中不唯一，行号待核对' if matches else '引用代码与本次 diff 不匹配，行号待核对'
        return location
    if explicit and body_evidence and selected['text'].strip() != body_evidence.strip() and any(
        row_path == path and row_side == side and row['text'].strip() == body_evidence.strip()
        for (row_path, row_side, _), row in rows.items()
    ):
        location['reason'] = '锚点原代码与正文引用不一致，行号待核对'
        return location
    position = selected['position']
    # Emit only the requested side(s). A context row has both coordinates, but
    # that must not silently convert a deletion-side finding to the new side.
    location[side] = position[side]
    opposite = 'old_line' if side == 'new_line' else 'new_line'
    if finding.get(opposite) is not None:
        location[opposite] = position.get(opposite)
    relocated = any(location[field] != finding.get(field) for field in ('old_line', 'new_line'))
    location.update(status='relocated' if relocated else 'exact',
                    reason='已按原代码定位' if relocated else '原代码与行号一致',
                    anchor_code=selected['text'])
    return location


def ground_finding_anchors(diff, findings):
    if not isinstance(findings, list):
        raise ValueError('模型 findings 必须是数组')
    rows = diff_lines(diff)
    grounded = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError('模型 finding 必须是对象')
        location = finding_location(diff, finding, rows=rows)
        if location['status'] == 'unverified':
            raise ValueError(f'无法精确定位 {finding.get("file_path", "")}: {location["reason"]}，已停止发布，请重新评审')
        grounded.append({**finding, 'new_line': location['new_line'], 'old_line': location['old_line'],
                         'anchor_code': location['anchor_code']})
    return grounded


def comment_location(comment):
    review = comment.review
    cached = getattr(review, '_location_index', None)
    if cached is None or cached[0] != review.diff_text:
        try:
            rows = diff_lines(review.diff_text)
        except ValueError:
            rows = {}
        cached = review._location_index = (review.diff_text, rows)
    return finding_location(review.diff_text, comment_finding(comment), rows=cached[1])


def numbered_diff(diff):
    """Give models explicit file coordinates; keep the stored diff untouched."""
    rendered, seen = [], set()
    path = hunk = None
    for row in diff_lines(diff).values():
        if id(row) in seen:
            continue
        seen.add(id(row))
        position = row['position']
        if position['new_path'] != path:
            path = position['new_path']
            rendered.append(f'文件：{path}（原路径：{position["old_path"]}）')
        if row['hunk'] != hunk:
            hunk = row['hunk']
            rendered.append(f'变更片段 {hunk}：')
        old, new = position.get('old_line'), position.get('new_line')
        kind = '+' if old is None else '-' if new is None else ' '
        rendered.append(f'[old={old if old is not None else "-"} new={new if new is not None else "-"}] {kind}{row["text"]}')
    return '\n'.join(rendered)
