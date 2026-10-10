import json

from django.test import SimpleTestCase

from review.engines import contracts

from review.engines.contracts import (
    normalize_engine_result,
    normalize_open_code_review_result,
    normalize_pi_agent_result,
    normalize_pr_agent_result,
)


class ReviewEngineContractTests(SimpleTestCase):
    def test_normalizes_common_result_and_preserves_unknown_severity_as_unrated(self):
        result = normalize_engine_result('pi-agent', {
            'summary': '本次变更总体可行。',
            'findings': [{
                'file_path': 'src/a.py', 'new_line': 12, 'level': 'possible-bug',
                'title': '边界条件', 'body': '空输入会触发异常。',
            }],
        }, version='1.2.3')

        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['engine'], {'id': 'pi-agent', 'version': '1.2.3'})
        self.assertEqual(result['findings'][0]['level'], 'UNRATED')

    def test_rejects_missing_or_non_positive_line_instead_of_inventing_anchor(self):
        for finding in (
            {'file_path': 'src/a.py', 'title': '问题', 'body': '说明'},
            {'file_path': 'src/a.py', 'new_line': 0, 'title': '问题', 'body': '说明'},
        ):
            with self.subTest(finding=finding), self.assertRaises(ValueError):
                normalize_engine_result('pi-agent', {'summary': '总评', 'findings': [finding]})

    def test_open_code_review_maps_actual_new_side_range_and_suggestion(self):
        result = normalize_open_code_review_result({
            'status': 'complete',
            'summary': {'files_reviewed': 3, 'comments': 1, 'elapsed': '8s'},
            'comments': [{
                'path': 'src/a.py', 'content': '空值会导致异常。',
                'start_line': 20, 'end_line': 21,
                'existing_code': 'value = item.name',
                'suggestion_code': 'value = item.name if item else ""',
            }],
        }, version='1.12.9')

        finding = result['findings'][0]
        self.assertEqual((finding['new_line'], finding['suggestion']['start_line'], finding['suggestion']['end_line']), (20, 20, 21))
        self.assertEqual(finding['suggestion']['original_code'], 'value = item.name')
        self.assertEqual(finding['suggestion']['replacement_code'], 'value = item.name if item else ""')
        self.assertEqual(result['summary'], 'OpenCodeReview 完成了 3 个文件的评审，共整理出 1 条行级意见。')

    def test_pi_agent_requires_a_json_object_not_an_event_stream_or_markdown(self):
        for raw in ('{"summary":"总评","findings":[]}\n{"type":"done"}', '```json\n{}\n```', 'review complete'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_pi_agent_result(raw, version='0.86.0')

    def test_pi_stream_uses_complete_terminal_assistant_text_blocks(self):
        final = '{"summary":"本次没有问题。","findings":[]}'
        message = {'role': 'assistant', 'stopReason': 'stop', 'content': [
            {'type': 'thinking', 'thinking': 'private reasoning'},
            {'type': 'text', 'text': final[:15]}, {'type': 'text', 'text': final[15:]},
        ]}
        events = [
            {'type': 'session'},
            {'type': 'message_end', 'message': {'role': 'toolResult', 'content': [{'type': 'text', 'text': 'untrusted tool output'}]}},
            {'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': 'partial'}},
            {'type': 'agent_end', 'messages': [message]},
            {'type': 'agent_settled'},
        ]
        raw = '\n'.join(json.dumps(event) for event in events)
        self.assertEqual(contracts.extract_pi_agent_final_text(raw), final)

    def test_pi_stream_rejects_missing_terminal_event_and_unsuccessful_final_messages(self):
        base = {'role': 'assistant', 'stopReason': 'stop', 'content': [{'type': 'text', 'text': '{"summary":"总评","findings":[]}'}]}
        for message in (None, {**base, 'stopReason': 'length'}, {**base, 'stopReason': 'error'},
                        {**base, 'stopReason': 'aborted'}, {**base, 'stopReason': 'toolUse'},
                        {**base, 'role': 'toolResult'}, {**base, 'content': []},
                        {**base, 'content': [{'type': 'toolCall', 'name': 'read'}]}):
            event = {'type': 'message_end', 'message': base} if message is None else {'type': 'agent_end', 'messages': [message]}
            with self.subTest(message=message), self.assertRaises(ValueError):
                contracts.extract_pi_agent_final_text(json.dumps(event))

    def test_pi_stream_rejects_malformed_events_and_trailing_output(self):
        complete = json.dumps({'type': 'agent_end', 'messages': [{
            'role': 'assistant', 'stopReason': 'stop',
            'content': [{'type': 'text', 'text': '{"summary":"总评","findings":[]}'}],
        }]})
        for raw in ('log line\n' + complete, complete + '\n{"type":"message_update"}',
                    '[]\n' + complete, '{"type":"agent_end","messages":null}',
                    '{"type":"agent_settled"}',
                    complete.replace('"type": "agent_end"', '"type": "agent_end", "willRetry": true')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                contracts.extract_pi_agent_final_text(raw)

    def test_pr_agent_maps_only_structured_key_issues_and_does_not_invent_severity(self):
        result = normalize_pr_agent_result({
            'review': {'key_issues_to_review': [{
                'relevant_file': 'src/a.py', 'issue_header': 'Possible Bug',
                'issue_content': '空值会导致异常。', 'start_line': 8, 'end_line': 9,
            }]},
            'markdown': '## 评审\n请修复空值处理。',
        }, version='0.46.0')

        self.assertEqual(result['findings'][0]['new_line'], 8)
        self.assertEqual(result['findings'][0]['level'], 'UNRATED')
        self.assertNotIn('suggestion', result['findings'][0])
        self.assertIn('PR-Agent 本次发现 1 个待关注问题', result['summary'])

    def test_pr_agent_improve_maps_a_replaceable_suggestion_without_inventing_lines(self):
        result = normalize_pr_agent_result({
            'type': 'improve',
            'payload': {
                'code_suggestions': [{
                    'relevant_file': 'src/a.py', 'relevant_lines_start': 8,
                    'relevant_lines_end': 9, 'one_sentence_summary': '避免空值异常',
                    'suggestion_content': '先检查对象是否为空。',
                    'existing_code': 'value = item.name',
                    'improved_code': 'value = item.name if item else ""',
                }],
            },
            'markdown': '## 改进建议\n请增加空值检查。',
        }, version='0.46.0', mode='improve')

        finding = result['findings'][0]
        self.assertEqual((finding['new_line'], finding['suggestion']['start_line'], finding['suggestion']['end_line']), (8, 8, 9))
        self.assertEqual(finding['suggestion']['original_code'], 'value = item.name')
        self.assertEqual(finding['suggestion']['replacement_code'], 'value = item.name if item else ""')
        self.assertEqual(finding['level'], 'UNRATED')
