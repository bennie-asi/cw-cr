class IntegrationNotConfiguredError(RuntimeError):
    """Raised when a real external integration is required but not configured."""


def safe_error(exc: Exception) -> str:
    """requests errors may contain complete URLs, including robot/API keys."""
    import requests
    if isinstance(exc, requests.Timeout):
        return '外部服务请求超时，请检查连接或调整请求超时'
    if isinstance(exc, requests.ConnectionError):
        return '无法连接外部服务，请检查地址、代理监听和网络'
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else 'unknown'
        return f'外部服务返回 HTTP {code}，请检查访问权限和服务状态'
    if isinstance(exc, requests.RequestException):
        return '外部服务请求失败，请检查连接配置'
    return str(exc)
