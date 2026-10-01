"""Conservative question diagnostics, not a semantic approval classifier."""
import re
import unicodedata


def _norm(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).casefold())


def question_issues(question, program, *, parent=False, aliases=()):
    issues = []
    if not parent and not any(_norm(name) in _norm(question) for name in (program, *aliases) if name.strip()):
        issues.append('missing_program_in_standalone_question')
    # A generated context prefix cannot repair a missing metric or task scope.
    body = question.strip().splitlines()[-1].strip()
    body = re.sub(r'[?？.!。\s]+$', '', body)
    if re.fullmatch(r'(?:그\s*|이\s*)?(?:수치|금액|인원|규모)(?:는|은|가|이)?\s*(?:얼마(?:야|인가요|인가|예요)?|몇(?:이야|인가요)?)', body):
        issues.append('missing_metric_in_question')
    if re.fullmatch(r'(?:그\s*|이\s*)?계획서(?:는|를)?\s*어떻게\s*(?:작성해야\s*해|써야\s*해|작성하나요)', body):
        issues.append('missing_task_scope_in_question')
    return issues
