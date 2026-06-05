import argparse
import json
import os
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.github.com"
JsonObject = dict[str, Any]


class GitHubPaginator(Protocol):
    """페이지네이션된 GitHub API 목록 응답을 가져오는 클라이언트 규약."""

    def paginate(self, path: str, params: JsonObject | None = None) -> list[Any]:
        """GitHub API 목록 엔드포인트의 모든 페이지를 반환한다.

        Args:
            path: API base URL을 제외한 GitHub REST API 경로.
            params: 요청 query string에 넣을 파라미터.

        Returns:
            모든 페이지 응답을 합친 항목 목록.
        """


class GitHubAPIError(RuntimeError):
    """GitHub API 요청 실패를 표현하는 예외."""

    pass


class GitHubClient:
    """GitHub REST API 요청과 Link header 기반 페이지네이션을 처리한다."""

    def __init__(self, token: str | None = None, api_base_url: str = API_BASE_URL) -> None:
        """GitHub API 클라이언트를 초기화한다.

        Args:
            token: GitHub API 인증 토큰. 없으면 익명 요청을 사용한다.
            api_base_url: GitHub API base URL. GitHub Enterprise 테스트에 교체 가능하다.
        """

        self.api_base_url = api_base_url.rstrip("/")
        self.token = token

    def paginate(self, path: str, params: JsonObject | None = None) -> list[Any]:
        """GitHub API 목록 응답을 끝 페이지까지 수집한다.

        Args:
            path: API base URL을 제외한 REST API 경로.
            params: 요청 query string에 넣을 파라미터.

        Returns:
            각 페이지의 JSON list 응답을 순서대로 합친 목록.

        Raises:
            GitHubAPIError: 목록 엔드포인트가 list가 아닌 JSON을 반환한 경우.
        """

        params = dict(params or {})
        params.setdefault("per_page", 100)
        page = 1
        results: list[Any] = []

        while True:
            params["page"] = page
            payload, headers = self.request_json(path, params)

            if not isinstance(payload, list):
                raise GitHubAPIError(f"Expected a list response for {path}, got {type(payload).__name__}")

            results.extend(payload)

            if not _has_next_link(headers.get("Link", "")) or not payload:
                return results

            page += 1

    def request_json(self, path: str, params: JsonObject | None = None) -> tuple[Any, dict[str, str]]:
        """GitHub API에 GET 요청을 보내고 JSON body와 response header를 반환한다.

        Args:
            path: API base URL을 제외한 REST API 경로.
            params: 요청 query string에 넣을 파라미터.

        Returns:
            디코딩된 JSON payload와 response header 딕셔너리.

        Raises:
            GitHubAPIError: GitHub API가 HTTP 오류를 반환한 경우.
        """

        query = f"?{urlencode(params or {})}" if params else ""
        url = f"{self.api_base_url}{path}{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-issue-exporter",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw), dict(response.headers.items())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise GitHubAPIError(f"GitHub API request failed: {error.code} {error.reason}: {detail}") from error


def parse_repo(value: str) -> tuple[str, str]:
    """GitHub 저장소 입력값에서 owner와 repo 이름을 파싱한다.

    Args:
        value: `owner/repo` 형식 또는 `https://github.com/owner/repo` URL.

    Returns:
        `(owner, repo)` 튜플.

    Raises:
        ValueError: GitHub 저장소 형식이 아니거나 github.com URL이 아닌 경우.
    """

    value = value.strip()
    parsed = urlparse(value)

    if parsed.netloc:
        if parsed.netloc.lower() != "github.com":
            raise ValueError(f"Only github.com repository URLs are supported: {value}")
        parts = [part for part in parsed.path.strip("/").split("/") if part]
    else:
        parts = [part for part in value.strip("/").split("/") if part]

    if len(parts) < 2:
        raise ValueError("Repository must be a GitHub URL or owner/repo string")

    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return owner, repo


def export_open_issues(
    owner: str,
    repo: str,
    client: GitHubPaginator,
    max_issues: int | None = None,
    generated_at: str | None = None,
) -> JsonObject:
    """open issue와 연결 정보를 JSON 직렬화 가능한 구조로 추출한다.

    GitHub issues API는 pull request도 issue 형태로 반환하므로,
    `pull_request` 필드가 있는 항목은 제외한다.

    Args:
        owner: GitHub 저장소 owner.
        repo: GitHub 저장소 이름.
        client: 페이지네이션을 지원하는 GitHub API 클라이언트.
        max_issues: 추출할 issue 최대 개수. `None`이면 전체 open issue를 추출한다.
        generated_at: 결과 JSON에 기록할 생성 시각. 없으면 현재 UTC 시각을 사용한다.

    Returns:
        repository, filters, issue 목록을 포함한 JSON 직렬화 가능 딕셔너리.
    """

    issues_path = f"/repos/{quote(owner)}/{quote(repo)}/issues"
    raw_issues = client.paginate(
        issues_path,
        {
            "state": "open",
            "per_page": 100,
            "sort": "updated",
            "direction": "desc",
        },
    )

    issues: list[JsonObject] = []
    for raw_issue in raw_issues:
        if "pull_request" in raw_issue:
            continue

        issue_number = raw_issue["number"]
        comments = client.paginate(f"{issues_path}/{issue_number}/comments", {"per_page": 100})
        timeline = client.paginate(f"{issues_path}/{issue_number}/timeline", {"per_page": 100})
        issues.append(normalize_issue(raw_issue, comments, timeline))

        if max_issues is not None and len(issues) >= max_issues:
            break

    return {
        "schema_version": 1,
        "generated_at": generated_at or utc_now_iso(),
        "repository": {
            "owner": owner,
            "name": repo,
            "full_name": f"{owner}/{repo}",
            "html_url": f"https://github.com/{owner}/{repo}",
        },
        "filters": {
            "state": "open",
            "exclude_pull_request_items": True,
            "sort": "updated",
            "direction": "desc",
        },
        "issue_count": len(issues),
        "issues": issues,
    }


def normalize_issue(raw_issue: JsonObject, comments: list[Any], timeline: list[Any]) -> JsonObject:
    """GitHub issue API 응답을 exporter JSON schema에 맞게 변환한다.

    Args:
        raw_issue: GitHub issue API 원본 응답.
        comments: issue comments API에서 가져온 comment 목록.
        timeline: issue timeline API에서 가져온 event 목록.

    Returns:
        exporter가 저장하는 issue 딕셔너리.
    """

    return {
        "number": raw_issue.get("number"),
        "title": raw_issue.get("title"),
        "state": raw_issue.get("state"),
        "html_url": raw_issue.get("html_url"),
        "body": raw_issue.get("body"),
        "author": normalize_user(raw_issue.get("user")),
        "assignees": [normalize_user(user) for user in raw_issue.get("assignees", [])],
        "labels": [normalize_label(label) for label in raw_issue.get("labels", [])],
        "comment_count": raw_issue.get("comments", len(comments)),
        "comments": [normalize_comment(comment) for comment in comments],
        "linked_pull_requests": extract_linked_pull_requests_from_timeline(timeline),
        "created_at": raw_issue.get("created_at"),
        "updated_at": raw_issue.get("updated_at"),
        "closed_at": raw_issue.get("closed_at"),
    }


def normalize_comment(raw_comment: JsonObject) -> JsonObject:
    """GitHub comment API 응답을 exporter JSON schema에 맞게 변환한다.

    Args:
        raw_comment: GitHub issue comment API 원본 응답.

    Returns:
        exporter가 저장하는 comment 딕셔너리.
    """

    return {
        "id": raw_comment.get("id"),
        "html_url": raw_comment.get("html_url"),
        "body": raw_comment.get("body"),
        "author": normalize_user(raw_comment.get("user")),
        "author_association": raw_comment.get("author_association"),
        "created_at": raw_comment.get("created_at"),
        "updated_at": raw_comment.get("updated_at"),
    }


def normalize_user(raw_user: JsonObject | None) -> JsonObject | None:
    """GitHub user 응답에서 필요한 필드만 추출한다.

    Args:
        raw_user: GitHub user 객체. 값이 없으면 `None`.

    Returns:
        login, id, html_url, type만 포함한 user 딕셔너리. 입력이 `None`이면 `None`.
    """

    if raw_user is None:
        return None

    return {
        "login": raw_user.get("login"),
        "id": raw_user.get("id"),
        "html_url": raw_user.get("html_url"),
        "type": raw_user.get("type"),
    }


def normalize_label(raw_label: JsonObject | str) -> JsonObject:
    """GitHub label 응답에서 필요한 필드만 추출한다.

    Args:
        raw_label: GitHub label 객체 또는 label 이름 문자열.

    Returns:
        name, color, description을 포함한 label 딕셔너리.
    """

    if isinstance(raw_label, str):
        return {"name": raw_label, "color": None, "description": None}

    return {
        "name": raw_label.get("name"),
        "color": raw_label.get("color"),
        "description": raw_label.get("description"),
    }


def extract_linked_pull_requests_from_timeline(events: list[Any]) -> list[JsonObject]:
    """issue timeline event에서 연결된 pull request 목록을 추출한다.

    Args:
        events: GitHub issue timeline API에서 가져온 event 목록.

    Returns:
        중복 제거된 linked pull request 목록.
    """

    linked: dict[str, JsonObject] = {}

    for event in events:
        for candidate in _iter_pull_request_issue_candidates(event):
            normalized = normalize_pull_request(candidate)
            key = normalized.get("html_url") or str(normalized.get("number"))
            if key and key not in linked:
                linked[key] = normalized

    return list(linked.values())


def normalize_pull_request(raw_pr_issue: JsonObject) -> JsonObject:
    """pull request를 issue 형태로 담은 GitHub 응답을 정규화한다.

    Args:
        raw_pr_issue: `pull_request` 필드를 포함한 GitHub issue 객체.

    Returns:
        exporter가 저장하는 linked pull request 딕셔너리.
    """

    pull_request = raw_pr_issue.get("pull_request") or {}

    return {
        "number": raw_pr_issue.get("number"),
        "title": raw_pr_issue.get("title"),
        "state": raw_pr_issue.get("state"),
        "html_url": pull_request.get("html_url") or raw_pr_issue.get("html_url"),
        "author": normalize_user(raw_pr_issue.get("user")),
        "created_at": raw_pr_issue.get("created_at"),
        "updated_at": raw_pr_issue.get("updated_at"),
        "closed_at": raw_pr_issue.get("closed_at"),
        "merged_at": raw_pr_issue.get("merged_at"),
    }


def _iter_pull_request_issue_candidates(value: Any) -> Iterator[JsonObject]:
    """중첩된 timeline event에서 pull request issue 객체를 순회한다.

    Args:
        value: timeline event 또는 그 내부에 중첩된 임의의 값.

    Yields:
        `pull_request` 필드와 issue number를 가진 GitHub issue 객체.
    """

    if isinstance(value, dict):
        if "pull_request" in value and value.get("number") is not None:
            yield value
        for nested in value.values():
            yield from _iter_pull_request_issue_candidates(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_pull_request_issue_candidates(item)


def _has_next_link(link_header: str) -> bool:
    """GitHub Link header에 다음 페이지가 있는지 확인한다.

    Args:
        link_header: GitHub API 응답의 `Link` header 값.

    Returns:
        `rel="next"` 링크가 있으면 `True`, 없으면 `False`.
    """

    links = [link.strip() for link in link_header.split(",") if link.strip()]
    return any('rel="next"' in link for link in links)


def utc_now_iso() -> str:
    """현재 UTC 시각을 ISO-8601 문자열로 반환한다.

    Returns:
        `YYYY-MM-DDTHH:MM:SSZ` 형식의 UTC 시각 문자열.
    """

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    """CLI argument parser를 생성한다.

    Returns:
        GitHub issue exporter CLI용 `ArgumentParser`.
    """

    parser = argparse.ArgumentParser(
        description="Export open GitHub issues as JSON, including comments, labels, assignees, and linked PRs.",
    )
    parser.add_argument(
        "repo",
        nargs="?",
        default=DEFAULT_REPO,
        help=f"GitHub repo URL or owner/repo. Defaults to {DEFAULT_REPO}.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write JSON to this file instead of stdout.",
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        help="Limit exported issues after filtering out pull requests. Useful for demos and rate-limit-safe checks.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub token. Defaults to GITHUB_TOKEN when set.",
    )
    parser.add_argument(
        "--api-base-url",
        default=API_BASE_URL,
        help="GitHub API base URL. Override for tests or GitHub Enterprise.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint를 실행한다.

    Args:
        argv: 명령행 인자 목록. `None`이면 `sys.argv`를 사용한다.

    Returns:
        성공 시 `0`, 실패 시 `1`.
    """

    parser = build_parser()
    args: argparse.Namespace = parser.parse_args(argv)

    try:
        owner, repo = parse_repo(args.repo)
        payload = export_open_issues(
            owner,
            repo,
            GitHubClient(token=args.token, api_base_url=args.api_base_url),
            max_issues=args.max_issues,
        )
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"{rendered}\n", encoding="utf-8")
        else:
            print(rendered)

        return 0
    except (GitHubAPIError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
