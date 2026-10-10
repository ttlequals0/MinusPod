"""Shared response conversion for System One-backed adapters."""
import json


def response_content(response):
    choices = (response.get('choices') if isinstance(response, dict)
               else getattr(response, 'choices', None)) or []
    if not choices:
        return ''
    first = choices[0]
    message = first.get('message') if isinstance(first, dict) else getattr(first, 'message', None)
    return (message.get('content') if isinstance(message, dict)
            else getattr(message, 'content', None)) or ''


def response_payload(response):
    content = response_content(response)
    if not isinstance(content, str):
        raise ValueError('System One response content must be text')
    return json.loads(content)
