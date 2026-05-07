import os
import re
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Tuple

from biz.utils.log import logger

IGNORE_MAINLINE_MERGE_REBASE_CHANGES_ENABLED = 'IGNORE_MAINLINE_MERGE_REBASE_CHANGES_ENABLED'
DEFAULT_MAINLINE_BRANCHES = ('main', 'master')


def _filter_enabled(enabled: bool | None = None) -> bool:
    if enabled is not None:
        return enabled
    return os.environ.get(IGNORE_MAINLINE_MERGE_REBASE_CHANGES_ENABLED, '0') == '1'


def _split_diff_hunks(diff_text: str) -> List[str]:
    if not diff_text or not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    hunks = []
    preamble = []
    current_hunk = []
    seen_hunk = False

    for line in lines:
        if line.startswith('@@'):
            if current_hunk:
                hunks.append('\n'.join(current_hunk))
            current_hunk = (preamble + [line]) if not seen_hunk and preamble else [line]
            preamble = []
            seen_hunk = True
        elif seen_hunk:
            current_hunk.append(line)
        else:
            preamble.append(line)

    if current_hunk:
        hunks.append('\n'.join(current_hunk))

    return hunks or [diff_text]


def _fingerprint_patch(diff_text: str) -> Tuple[str, ...]:
    changed_lines = []
    for line in diff_text.splitlines():
        if line.startswith(('diff --git', 'index ', '@@')):
            continue
        if line.startswith('+++') or line.startswith('---'):
            continue
        if line.startswith('+'):
            changed_lines.append(f'+{line[1:].rstrip()}')
        elif line.startswith('-'):
            changed_lines.append(f'-{line[1:].rstrip()}')
    return tuple(changed_lines)


def _count_diff_changes(diff_text: str) -> Tuple[int, int]:
    additions = len(re.findall(r'^\+(?!\+\+)', diff_text, re.MULTILINE))
    deletions = len(re.findall(r'^-(?!--)', diff_text, re.MULTILINE))
    return additions, deletions


def _normalize_branch_name(branch_name: str) -> str:
    if not branch_name:
        return ''
    normalized = branch_name.strip().strip('\'"')
    for prefix in ('refs/heads/', 'refs/remotes/', 'origin/'):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized.lower()


def is_mainline_sync_commit(
        commit: dict,
        *,
        source_branch: str = '',
        target_branch: str = '',
        mainline_branches: Tuple[str, ...] = DEFAULT_MAINLINE_BRANCHES,
) -> bool:
    title = str(commit.get('title', '') or '')
    message = str(commit.get('message', '') or '')
    text = f'{title}\n{message}'
    if not text.strip():
        return False

    mainline_branch_names = {_normalize_branch_name(branch) for branch in mainline_branches if branch}
    source_branch_name = _normalize_branch_name(source_branch)
    target_branch_name = _normalize_branch_name(target_branch)

    for merged_branch, into_branch in re.findall(
            r"merge\s+(?:remote-tracking\s+)?branch\s+'([^']+)'\s+into\s+'?([^'\n]+)'?",
            text,
            flags=re.IGNORECASE,
    ):
        merged_branch_name = _normalize_branch_name(merged_branch)
        into_branch_name = _normalize_branch_name(into_branch)
        if merged_branch_name not in mainline_branch_names:
            continue
        if source_branch_name and into_branch_name == source_branch_name:
            return True
        if target_branch_name and into_branch_name == target_branch_name:
            return False
        if into_branch_name and into_branch_name not in mainline_branch_names:
            return True

    return False


def filter_out_mainline_sync_commits(
        commits: Iterable[dict],
        *,
        source_branch: str = '',
        target_branch: str = '',
        mainline_branches: Tuple[str, ...] = DEFAULT_MAINLINE_BRANCHES,
) -> List[dict]:
    return [
        commit for commit in commits
        if not is_mainline_sync_commit(
            commit,
            source_branch=source_branch,
            target_branch=target_branch,
            mainline_branches=mainline_branches,
        )
    ]


def _build_mainline_hunk_index(mainline_changes: Iterable[dict]) -> Dict[str, set]:
    hunks_by_path: Dict[str, set] = defaultdict(set)
    for change in mainline_changes:
        new_path = change.get('new_path', '')
        if not new_path:
            continue
        for hunk in _split_diff_hunks(change.get('diff', '')):
            fingerprint = _fingerprint_patch(hunk)
            if fingerprint:
                hunks_by_path[new_path].add(fingerprint)
    return hunks_by_path


def subtract_mainline_changes(review_changes: List[dict], mainline_changes: Iterable[dict]) -> List[dict]:
    if not review_changes:
        return []

    mainline_hunks = _build_mainline_hunk_index(mainline_changes)
    if not mainline_hunks:
        return review_changes

    filtered_changes = []
    for change in review_changes:
        new_path = change.get('new_path', '')
        diff_text = change.get('diff', '')
        known_hunks = mainline_hunks.get(new_path)
        if not new_path or not diff_text or not known_hunks:
            filtered_changes.append(change)
            continue

        retained_hunks = []
        removed_hunk = False
        for hunk in _split_diff_hunks(diff_text):
            fingerprint = _fingerprint_patch(hunk)
            if fingerprint and fingerprint in known_hunks:
                removed_hunk = True
                continue
            retained_hunks.append(hunk)

        if not removed_hunk:
            filtered_changes.append(change)
            continue

        if not retained_hunks:
            continue

        new_change = dict(change)
        new_change['diff'] = '\n'.join(retained_hunks)
        new_change['additions'], new_change['deletions'] = _count_diff_changes(new_change['diff'])
        filtered_changes.append(new_change)

    return filtered_changes


def filter_out_mainline_changes(
        review_changes: List[dict],
        *,
        source_branch: str,
        target_branch: str,
        compare_fn: Callable[[str, str], List[dict]],
        change_filter_fn: Callable[[List[dict]], List[dict]],
        enabled: bool | None = None,
        mainline_branches: Tuple[str, ...] = DEFAULT_MAINLINE_BRANCHES,
) -> List[dict]:
    if not _filter_enabled(enabled):
        return review_changes

    if not review_changes or not target_branch or target_branch in mainline_branches:
        return review_changes

    candidate_branches = [
        branch for branch in mainline_branches
        if branch and branch not in {source_branch, target_branch}
    ]
    if not candidate_branches:
        return review_changes

    filtered_changes = review_changes
    for branch in candidate_branches:
        try:
            mainline_changes = change_filter_fn(compare_fn(target_branch, branch))
        except Exception as exc:
            logger.warning(f"Failed to compare target branch '{target_branch}' with mainline '{branch}': {exc}")
            continue

        if not mainline_changes:
            continue

        previous_count = len(filtered_changes)
        filtered_changes = subtract_mainline_changes(filtered_changes, mainline_changes)
        if len(filtered_changes) != previous_count:
            logger.info(
                f"Filtered {previous_count - len(filtered_changes)} file(s) of mainline-only changes using branch '{branch}'."
            )
        if not filtered_changes:
            break

    return filtered_changes
