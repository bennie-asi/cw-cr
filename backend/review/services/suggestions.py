import re

from review.services.diff_positions import diff_lines


def suggestion_body(diff, path, new_line, old_line, body, suggestion):
    # Only this renderer can create executable suggestions; model prose is untrusted.
    body = re.sub(r'(?im)([`~]{3,})\s*suggestion\b[^\n]*', r'\1text', str(body))
    if suggestion is None:
        return body
    try:
        if not isinstance(suggestion, dict) or type(new_line) is not int:
            raise ValueError()
        start, end = suggestion.get('start_line'), suggestion.get('end_line')
        original, replacement = suggestion.get('original_code'), suggestion.get('replacement_code')
        if type(start) is not int or type(end) is not int or not (0 < start <= new_line <= end):
            raise ValueError()
        if new_line - start > 100 or end - new_line > 100:
            raise ValueError()
        if not isinstance(original, str) or not isinstance(replacement, str) or original == replacement:
            raise ValueError()
        # Older self-managed versions cannot safely nest fences in suggestions.
        if '```' in replacement or '~~~' in replacement or '\r' in replacement:
            raise ValueError()
        rows = diff_lines(diff)
        selected = [rows[path, 'new_line', number] for number in range(start, end + 1)]
        if len({row['hunk'] for row in selected}) != 1:
            raise ValueError()
        if '\n'.join(row['text'] for row in selected) != original:
            raise ValueError()
        return f'{body}\n\n```suggestion:-{new_line - start}+{end - new_line}\n{replacement}\n```'
    except (ValueError, KeyError, TypeError):
        return body + '\n\n> 此建议的替换范围或原文未通过校验，仅供人工修改。'
