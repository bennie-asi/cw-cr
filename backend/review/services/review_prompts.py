from review.models import GlobalPromptConfig, ProjectPolicy


def build_review_prompt(policy: ProjectPolicy, mode: str = 'review') -> str:
    config, _ = GlobalPromptConfig.objects.get_or_create(pk=1)
    sections = [f'平台通用要求：\n{config.general_prompt}',
                f'本次模式：/{mode}\n{config.improve_prompt if mode == "improve" else config.review_prompt}']
    rules = [
        ('安全性', policy.security_checks, policy.security_prompt.strip() or config.security_prompt),
        ('可靠性', policy.reliability_checks, policy.reliability_prompt.strip() or config.reliability_prompt),
        ('代码风格', policy.style_checks, policy.style_prompt.strip() or config.style_prompt),
    ]
    sections.extend(f'{name}检查要求：\n{prompt}' for name, enabled, prompt in rules if enabled)
    if policy.summary_prompt.strip():
        sections.append(f'项目补充要求：\n{policy.summary_prompt}')
    enabled = '、'.join(name for name, enabled, _ in rules if enabled) or '无额外专项检查'
    disabled = '、'.join(name for name, enabled, _ in rules if not enabled) or '无'
    sections.append(f'本项目启用检查：{enabled}。关闭的专项检查：{disabled}。请遵守项目检查范围。')
    return '\n\n'.join(sections)
