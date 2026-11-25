"""
Dependencies for FastAPI routes.
"""

import re
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Union, Literal

from passlib import ext

from dynamodb_client.org_invites import OrgMembersClient
from dynamodb_client.organization import OrganizationsClient
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, status
import requests
from sentry_sdk import capture_exception
from config import settings
from utils import get_logger
from utils.auth_urls import get_auth_token_validation_url, get_pat_validation_url, build_ensure_user_payload
from utils.user_org_cache import cache_user_org
from utils.constants import HOME_REGION_ATTR
from utils.country_region_map import get_region, get_group_for_aws_region
from enums import ResourceType
from typing import Annotated
from utils import validate_uuid
from jose import jwt, JWTError
import httpx
from dependencies.auth0_authenticator import verify_token as _verify_auth0_token
from utils.token_denylist import is_revoked_by_cutoff, is_token_denylisted

logger = get_logger("auth")


# [ghi chú]
# Khi agentic-backend chạy AUTH_ENABLED=false, nó gửi platform-api đường /public với X-User-Token = "test-token-123" của anonymous@example.com
# (AB api/auth.py:57-65 — CHUỖI VIẾT CỨNG trong mã gốc của Capitol, KHÔNG phải khoá bí mật; ai trên máy gửi được tới platform-api đều dùng
# được ⇒ đó là cái giá User chấp nhận cho bộ chạy thử 1 người dùng). Cờ CHAY_THU_NGUOI_DUNG_CO_DINH=on VÀ token khớp ĐÚNG chuỗi đó ⇒ nhận là
# người dùng cố định anonymous@example.com, user_type "user" (KHÔNG admin: admin qua mọi tổ chức). Sau đó mã gốc vẫn tra thành viên tổ chức
# (OrgMembersClient) + ACL từng kho ⇒ tách tổ chức giữ nguyên. Cờ tắt / token khác ⇒ y bản gốc.
_CO_DINH_TOKEN = "test-token-123"
_CO_DINH_EMAIL = "anonymous@example.com"
# [E MAJOR 17/11] get_current_user chỉ nhận người dùng cố định khi CHÍNH yêu cầu này đã qua check_org_access với một tổ chức mà người dùng cố
# định là thành viên (đặt ở cuối nhánh kiểm thành viên). Route chỉ dựa vào get_current_user mà không có check_org_access (vd agentic_files,
# agentic_workflow_run) ⇒ biến chưa đặt ⇒ đi đường gốc ⇒ 401 như trước vá.
import contextvars as _cv
_CO_DINH_ORG: "_cv.ContextVar[str | None]" = _cv.ContextVar("chay_thu_co_dinh_org", default=None)


def _nguoi_dung_co_dinh(token) -> bool:
    import hmac
    import os
    if os.environ.get("CHAY_THU_NGUOI_DUNG_CO_DINH") != "on":   # [E minor 1] đúng chữ "on", không hạ chữ / bỏ khoảng trắng
        return False
    return isinstance(token, str) and hmac.compare_digest(token.encode(), _CO_DINH_TOKEN.encode())


# /public/organizations/{orgid} with optional trailing slash — used to gate
# the org-delete 403 so sub-routes like .../encryption/configure aren't blocked.
_ORG_ROOT_DELETE_PATH = re.compile(r"^/public/organizations/[^/]+/?$")

# /public/organizations/{orgid}/archived — gates the admin-only archive toggle.
_ORG_ARCHIVED_PATH = re.compile(r"^/public/organizations/[^/]+/archived/?$")


def _is_org_root_delete_path(path: str) -> bool:
    return bool(_ORG_ROOT_DELETE_PATH.match(path))


def _is_org_archived_path(path: str) -> bool:
    return bool(_ORG_ARCHIVED_PATH.match(path))


# --- ENG-4634: org-owner-gated public endpoints --------------------------------
# Operations that mutate org-wide configuration, expose / mint / rotate
# credentials, or read full org configuration (agent list, single guardrail).
# These must be restricted to organization owners (platform super admins
# already bypass every org check below, at ``user_type == "admin"``).
#
# Enforcement lives ONLY inside ``check_org_access``, which is mounted solely on
# the public (browser / user-token) app. The internal machine-to-machine mount
# does not run this dependency, so service-to-service calls (e.g. agentic-backend
# reading or writing guardrails over M2M) are unaffected.
#
# Every pattern is evaluated only after ``org_id_from_path`` is confirmed present
# and a valid UUID, so collection-level routes without an ``{orgid}`` path param
# (e.g. ``/public/guardrails/create-guardrail-config-from-description``) never
# reach these checks. Member-facing routes on the same routers (agent chat/runs,
# the guardrail list and prompt checks, invite acceptance, roster reads) are
# deliberately excluded so ordinary members keep their current access; the
# roster and guardrail list redact owner-only fields for members in their
# handlers instead (``caller_has_owner_visibility``).
# The ``{orgid}`` path segment is always a UUID. Constraining it (rather than
# using a bare ``[^/]+``) keeps collection routes that live at the same depth but
# take a literal instead of an org id — e.g.
# ``/public/guardrails/create-guardrail-config-from-description`` — from matching.
_ORGID = r"[0-9a-fA-F-]{36}"

_OWNER_REQUIRED_ANY_METHOD = (
    # Org-scoped internal API keys: reads return live key material; writes and
    # deletes manage that material. Owner-only for every verb.
    re.compile(rf"^/public/internal-api-keys/{_ORGID}(/.*)?$"),
)

_OWNER_REQUIRED_BY_METHOD = {
    # NOTE: agent *creation* (``POST /public/agents/{orgid}``) intentionally
    # stays member-accessible — the workflow "Create orchestrator" flow lets
    # members mint an orchestrator for their own workflow. Only destructive /
    # credential operations on existing agents are owner-gated.
    "POST": (
        re.compile(rf"^/public/org-members/{_ORGID}/invites/?$"),            # create invite (assigns role)
        re.compile(rf"^/public/guardrails/{_ORGID}/?$"),                     # upsert guardrail
        re.compile(rf"^/public/agents/{_ORGID}/peer-agents/?$"),             # add peer-agent link
        re.compile(rf"^/public/agents/{_ORGID}/[^/]+/rotate-a2a-token/?$"),  # rotate bearer
        re.compile(rf"^/public/agents/{_ORGID}/[^/]+/bearers/?$"),           # mint additional bearer
    ),
    "PATCH": (
        re.compile(rf"^/public/org-members/{_ORGID}/invites/[^/]+/?$"),      # change member role
        re.compile(rf"^/public/org-members/{_ORGID}/members/[^/]+/?$"),      # change member role by email
        re.compile(rf"^/public/guardrails/{_ORGID}/[^/]+/?$"),               # edit guardrail
        re.compile(rf"^/public/agents/{_ORGID}/peer-agents/[^/]+/?$"),       # edit peer-agent link
        re.compile(rf"^/public/agents/{_ORGID}/[^/]+/?$"),                   # edit agent config
    ),
    "DELETE": (
        re.compile(rf"^/public/org-members/{_ORGID}/invites/[^/]+/?$"),      # remove member
        re.compile(rf"^/public/org-members/{_ORGID}/members/[^/]+/?$"),      # remove member by email
        re.compile(rf"^/public/guardrails/{_ORGID}/[^/]+/?$"),               # delete guardrail
        re.compile(rf"^/public/agents/{_ORGID}/peer-agents/[^/]+/?$"),       # remove peer-agent link
        re.compile(rf"^/public/agents/{_ORGID}/[^/]+/a2a-token/?$"),         # revoke primary bearer
        re.compile(rf"^/public/agents/{_ORGID}/[^/]+/bearers/[^/]+/?$"),     # revoke a bearer
        re.compile(rf"^/public/agents/{_ORGID}/[^/]+/?$"),                   # delete agent
    ),
    "GET": (
        # The agent list returns every agent's full configuration (system /
        # task prompts, sandbox env, allowlists). Its only browser consumer is
        # the owner/admin agents page; single-agent reads under /{agent_id}
        # stay member-accessible for the chat and orchestrator flows.
        re.compile(rf"^/public/agents/{_ORGID}/?$"),                         # agent list
        # Bearer inventory (token prefixes, labels, creators) is credential
        # management; its only browser consumer is the owner/admin config page.
        re.compile(rf"^/public/agents/{_ORGID}/[^/]+/bearers/?$"),           # list bearers
        # Email → (user id, name, role) resolution. Its consumer is
        # agentic-backend over the M2M mount; on the browser mount it would
        # let a member recover the role the roster projection withholds.
        re.compile(rf"^/public/org-members/{_ORGID}/user-by-email(/.*)?$"),
        # A single guardrail read returns the pass/fail criteria and examples
        # that describe how the policy is evaluated. Members get the redacted
        # list from /all instead (routes/guardrails.py). Only the GET
        # sub-resources that exist (/all, /failures) are excluded; anything
        # else in that segment is a guardrail id (/check/prompt is POST-only).
        re.compile(rf"^/public/guardrails/{_ORGID}/(?!(?:all|failures)(?:/|$))[^/]+/?$"),
    ),
}


def _public_path_requires_org_owner(path: str, method: str) -> bool:
    """Whether a public org-scoped route may be used only by an org owner.

    Only meaningful for paths that carry a valid ``{orgid}`` path parameter;
    callers must gate on that first (see ``_OWNER_REQUIRED_*`` above).
    """
    if any(pattern.match(path) for pattern in _OWNER_REQUIRED_ANY_METHOD):
        return True
    return any(pattern.match(path) for pattern in _OWNER_REQUIRED_BY_METHOD.get(method, ()))


# Public routes with no ``{orgid}`` that read across *every* organization.
# ``check_org_access`` has no org to check membership against for these, so
# without an explicit rule any authenticated user of any org can call them.
# Only platform super admins may use them on the browser mount; the org-scoped
# siblings (``…/{orgid}/invites``) are what the frontend actually uses.
_ADMIN_ONLY_PUBLIC_BY_METHOD = {
    "GET": (
        re.compile(r"^/public/internal-api-keys/?$"),                 # every org's API keys
        # Key-value → key-record oracle. Its handler documents it as M2M-only,
        # but shared_routers mounts every router on /public as well.
        re.compile(r"^/public/internal-api-keys/internal-api-keys/lookup/?$"),
        re.compile(r"^/public/org-members/invites/?$"),               # every member of every org
        re.compile(r"^/public/org-members/invites/email(/.*)?$"),     # any email's memberships across orgs
    ),
}


def _public_path_is_admin_only(path: str, method: str) -> bool:
    """Whether an un-scoped public route is reserved for platform super admins."""
    return any(pattern.match(path) for pattern in _ADMIN_ONLY_PUBLIC_BY_METHOD.get(method, ()))


# Single source of truth for the org-owner decision and its 403 message, shared
# by ``require_org_owner`` and the ``check_org_access`` owner gate so the two
# can't drift on role normalization or error wording.
_OWNER_REQUIRED_DETAIL = "Only organization owners or super admins can perform this action"


def _membership_is_owner(membership: dict[str, Any] | None) -> bool:
    """Whether a resolved org-membership row grants the ``owner`` role."""
    return bool(membership) and (membership.get("role") or "").strip().lower() == "owner"


def _is_org_owner(user_email: str | None, org_id: str) -> bool:
    """Whether ``user_email`` holds the ``owner`` role in ``org_id``.

    A missing membership row is treated as non-owner, matching how the frontend
    derives ``isOwner`` from the same record — an SSO user with no row is not
    shown owner affordances either.

    Uses the keyed lookup (the table key is ``(orgid, email)``): the scan-based
    sibling reads only the first 1MB page, which on a large members table could
    miss a real owner's row and deny them, besides costing a full-table scan on
    an auth path.
    """
    if not user_email:
        return False
    membership = OrgMembersClient().get_member_by_orgid_and_email(orgid=org_id, email=user_email)
    return _membership_is_owner(membership)


def _require_owner_role_or_403(user_email: str, org_id: str) -> None:
    """Raise 403 unless ``user_email`` is an *owner* of ``org_id``.

    Super admins never reach this (they short-circuit earlier in
    ``check_org_access``).
    """
    if not _is_org_owner(user_email, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_OWNER_REQUIRED_DETAIL,
        )


@dataclass(frozen=True)
class PublicCaller:
    """The identity ``check_org_access`` resolved for a public-mount request.

    Stored on ``request.state.public_caller`` so handlers that serve *both*
    owners and members on the same route can shape their response by role
    (see ``caller_has_owner_visibility``). Absent on the internal M2M mount,
    which never runs ``check_org_access``.
    """

    email: str | None
    user_type: str | None


def caller_has_owner_visibility(request: Request, org_id: str) -> bool:
    """Whether the caller may see owner-level detail for ``org_id``.

    Used by member-reachable reads whose records carry owner-only fields — the
    org roster (invite codes, roles) and the guardrail list (pass/fail
    criteria) — to choose between the full record and a redacted projection.

    True for platform super admins and org owners. When no ``PublicCaller`` was
    stashed, ``check_org_access`` did not run: on the internal machine-to-machine
    mount that is expected and the caller is a trusted service (full detail);
    on the ``/public`` mount it means a route was registered without the
    dependency, so fail closed and redact.
    """
    caller = getattr(request.state, "public_caller", None)
    if caller is None:
        return not request.url.path.startswith("/public/")
    if (caller.user_type or "").lower() == "admin":
        return True
    return _is_org_owner(caller.email, org_id)


async def get_current_user(
    x_user_token: Annotated[str | None, Header(alias="X-User-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Dict[str, Any]:
    """
    Get current user information from token or foreign API credentials.
    Returns user information for use in route handlers.
    Follows the clojure middleware pattern from gofapi.

    Supports two authentication methods:
    1. User tokens (X-User-Token or Authorization header) - for regular users
    2. Foreign API auth (X-User-ID + X-API-Key) - for API integrations
    """
    # Check for foreign API authentication first
    if x_user_id or x_api_key:
        from dependencies.external_user_auth import validate_external_user_token

        foreign_user = await validate_external_user_token(x_user_id=x_user_id, x_api_key=x_api_key)
        if foreign_user:
            logger.info(f"Foreign user authenticated: {foreign_user}")
            return foreign_user

    # Fall back to token-based authentication
    # Get token from either X-User-Token or Authorization header
    token = x_user_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication token is missing")

    if _nguoi_dung_co_dinh(token) and _CO_DINH_ORG.get() is not None:   # [ghi chú] người dùng cố định — CHỈ sau check_org_access đạt
        return {"user_id": "anonymous", "email": _CO_DINH_EMAIL, "user_type": "user", "name": "Anonymous User", "token": token}

    # Try Auth0 JWT first (SSO users)
    auth0_user = verify_auth0_user_token(token)
    if auth0_user:
        email = auth0_user["email"]
        # Resolve platform UUID — never use Auth0 sub as user_id
        try:
            clj_url = f"{settings.CLJ_PG_WRAPPER_BASE_URL.rstrip('/')}/api/v1/users/ensure"
            ensure_payload = build_ensure_user_payload(email, auth0_user.get("first_name"), auth0_user.get("last_name"))
            clj_resp = requests.post(clj_url, json=ensure_payload, timeout=5)
            if clj_resp.status_code == 200:
                clj_user = clj_resp.json()
                user_id = clj_user["id"]
                first = clj_user.get("first_name") or ""
                last = clj_user.get("last_name") or ""
                name = f"{first} {last}".strip() or email
                logger.info(f"Auth0 user {email} resolved to platform UUID {user_id}")
            else:
                logger.error(f"Failed to resolve Auth0 user {email} (status {clj_resp.status_code})")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to resolve user identity",
                )
        except HTTPException:
            raise
        except Exception as e:
            capture_exception(e)
            logger.error(f"Failed to resolve Auth0 user {email}: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to resolve user identity",
            )
        return {
            "user_id": user_id,
            "email": email,
            "user_type": auth0_user["user_type"],
            "name": name,
            # Auth0 enforces org membership at login, so downstream org checks can
            # trust this claim (as check_org_access does) instead of requiring a
            # JIT membership row to exist first.
            "org_id": auth0_user.get("org_id"),
            # Preserve the raw bearer so route handlers can forward it
            # to internal services (agentic-backend, mcp-servers, ...)
            # as ``X-User-Token`` for per-user attribution. Without this,
            # ``current_user.get("token")`` returns None and downstream
            # calls land at agentic-backend with no auth header, which
            # surfaces as 401 "Authentication token is missing" -- the
            # symptom in EU staging that caused the chat agent to think
            # its workflow allowlist was empty (orchestrator hop 401'd,
            # inline-chat fallback's workflow lookup 401'd too).
            "token": token,
        }

    # Fall through to legacy OTP token validation via clj-pg-wrapper
    try:
        user_info = check_token(token)
        # Log only non-sensitive identity fields. Never log the full dict:
        # ``token`` (the raw bearer, incl. pat_ PATs) is attached below and
        # would leak a live credential into logs/Sentry.
        logger.info(
            f"User authenticated: user_id={user_info.get('user_id')}, "
            f"type={user_info.get('user_type')}"
        )
        # See the Auth0 branch above for the rationale -- downstream
        # route handlers forward ``current_user["token"]`` as
        # ``X-User-Token`` to internal services. Patching the legacy
        # OTP branch too keeps the contract uniform across auth modes.
        user_info["token"] = token
        return user_info
    except Exception as e:
        logger.error(f"Error checking token: {str(e)}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


async def require_super_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Dependency that requires user to be a super admin.

    Args:
        user: User info from get_current_user dependency

    Returns:
        User info if user is admin

    Raises:
        HTTPException: 403 if user is not a super admin
    """
    user_type = user.get("user_type", "user")

    if user_type != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required. Only users with admin role can perform this action.",
        )

    return user


def require_org_owner(user: Dict[str, Any], org_id: str) -> Dict[str, Any]:
    """
    Check if user is org owner or super admin.
    Not async so it can be called from route handlers.

    Args:
        user: User info from get_current_user
        org_id: Organization ID to check ownership

    Returns:
        User info if user is owner or admin

    Raises:
        HTTPException: 403 if user is not org owner or admin
    """
    user_type = user.get("user_type", "user")

    # Super admins have access to all orgs
    if user_type == "admin":
        return user

    # Check if user is org owner
    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User email not found in token")

    if not _is_org_owner(user_email, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_OWNER_REQUIRED_DETAIL,
        )

    return user


def check_token(token: str, timeout: int = 10):
    """Validate a bearer token against the auth service and return user info.

    ``timeout`` bounds how long a caller can be held by the auth service. Lower
    it on paths reachable without authentication, where an attacker would
    otherwise choose how long each request occupies a worker.

    A ``pat_``-prefixed token is a Personal Access Token, always validated by DB
    lookup at clj-pg-wrapper ``/api/v1/pat/validate``. Every other (JWT) token
    goes to ``/api/v1/user/token`` on the *configured* auth service —
    clj-pg-wrapper when ``USE_CLJ_PG_WRAPPER`` is true, else legacy clj-services.
    Both endpoints return the same JSON shape.

    Args:
        token (str): The user token to check
    Returns:
        Dict[str, Any]: A dictionary containing user_id, email and role (admin or user)
    """
    try:
        if token.startswith("pat_"):
            auth_url = get_pat_validation_url()
        else:
            auth_url = get_auth_token_validation_url()
        response = requests.post(auth_url, json={"token": token}, timeout=timeout)

        if response.status_code == 200:
            response_data = response.json()

            # Reject legacy session tokens issued before the user signed out.
            # PATs are long-lived API credentials and outlive browser sessions.
            if not token.startswith("pat_") and is_revoked_by_cutoff(
                response_data.get("userId"), response_data.get("iat")
            ):
                logger.info("Rejected revoked (signed-out) legacy session token")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                )

            # extract the user role from the `typeName` key
            user_type = response_data.get("typeName", None) or "user"

            # Extract user information from response
            return {
                "user_id": response_data.get("userId", None),
                "email": response_data.get("email", None),
                "user_type": user_type.lower(),
                "name": response_data.get("name", ""),
            }
        else:
            logger.error(f"Failed to check token: {response.status_code} - {response.text}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    except HTTPException:
        raise
    except Exception as e:
        capture_exception(e)
        logger.error(f"Error checking token info: {str(e)}")
        raise Exception(f"Error checking token info: {str(e)}")


def verify_auth0_user_token(token: str) -> dict | None:
    """
    Attempt to verify a token as an Auth0-issued user JWT.

    Returns a dict with extracted user claims if valid, None if not an Auth0 token.
    Unlike M2M tokens, user JWTs include custom claims:
    - https://capitol.ai/org_id (internal DynamoDB org ID)
    - https://capitol.ai/roles (user roles)
    - https://capitol.ai/email (user email)
    """
    try:
        payload = _verify_auth0_token(token)

        # Log decoded SSO JWT claims to Sentry for observability
        from utils import sentry_message

        sentry_message(
            f"[SSO] Auth0 token decoded for {payload.get('https://capitol.ai/email') or payload.get('email')}",
            tags={"sso_auth": "true"},
            extra_context={"payload": payload},
        )

        # Extract custom claims
        raw_email = payload.get("https://capitol.ai/email") or payload.get("email")
        email = raw_email.strip().lower() if raw_email else None
        org_id = payload.get("https://capitol.ai/org_id")
        roles = payload.get("https://capitol.ai/roles", [])

        if not email:
            logger.warning("Auth0 user token missing email claim")
            return None

        user_type = "admin" if "admin" in roles else "user"

        # Extract name from IdP claims (e.g., Azure AD/Entra ID)
        idp_claims = payload.get("https://capitol.ai/idp_claims")
        if not isinstance(idp_claims, dict):
            idp_claims = {}
        first_name = idp_claims.get("given_name")
        last_name = idp_claims.get("family_name")

        user = {
            "user_id": None,  # Must be resolved via clj-pg-wrapper — never use Auth0 sub
            "email": email,
            "user_type": user_type,
            "org_id": org_id,
            "auth0_org_id": payload.get("https://capitol.ai/auth0_org_id") or payload.get("org_id"),
            "is_auth0_user": True,
            "first_name": first_name,
            "last_name": last_name,
            "idp_claims": idp_claims,
        }
    except HTTPException as e:
        if e.detail == "Invalid token header":
            # Not an Auth0 token (e.g., HS256 OTP/session token with no 'kid') — expected, no alert.
            return None
        # Auth0 token recognized but validation failed (expired, invalid signature, etc.)
        # This is a real SSO error — capture it for Sentry visibility.
        capture_exception(e)
        logger.warning(f"Auth0 token validation failed: {e.detail}")
        return None
    except Exception:
        # Not an Auth0 token at all (e.g., OTP session token) — expected, no alert.
        return None

    # Reject tokens revoked by sign-out.
    if is_token_denylisted(token):
        logger.info(f"Rejected revoked (signed-out) Auth0 token for {user['email']}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )
    return user


def _enforce_region(user_email: str, auth0_user: dict, org_id: str) -> tuple[dict | None, bool | None]:
    """Compute region status for SSO users on multi-regional orgs.

    No exceptions for region mismatch — returns a tuple instead.

    Returns (org_record, is_home_region):
      - (org_record, True)  — regional org, user's country matches backend
      - (org_record, False) — regional org, mismatch or missing/unknown country
      - (None, None)        — non-regional org or org not found
    """
    org_client = OrganizationsClient()
    org_record = org_client.get_organization(org_id)
    if not org_record or not org_record.get("regional_deployment"):
        return None, None

    idp_claims = auth0_user.get("idp_claims") or {}
    if not isinstance(idp_claims, dict):
        idp_claims = {}
    user_country = idp_claims.get("country")
    user_group = get_region(user_country)
    backend_group = get_group_for_aws_region(settings.AWS_REGION)

    if user_group is None or user_group != backend_group:
        from utils import sentry_message

        logger.warning(
            f"Cross-region access for {user_email}: user_country={user_country}, "
            f"user_group={user_group}, backend_group={backend_group}"
        )
        sentry_message(
            f"[SSO Cross-Region Access] {user_email}: user_group={user_group}, backend_group={backend_group}",
            tags={"sso_cross_region": "true", "user_group": str(user_group), "backend_group": backend_group},
            extra_context={
                "user_email": user_email,
                "user_country": user_country,
                "user_group": user_group,
                "backend_group": backend_group,
                "org_id": org_id,
            },
        )
        return org_record, False

    return org_record, True


async def require_home_region(request: Request):
    """Block resource creation for non-home-region SSO users.

    Uses ``is False`` identity check on request.state.is_home_region:
      - True      → home region, full access                          → allow
      - False     → non-home region, SSO user on regional org         → BLOCK
      - None/unset → OTP, foreign API, admin, non-regional org, M2M  → allow

    None and "not set" both resolve to the getattr default (None).
    ``None is False`` evaluates to False in Python, so both pass through.
    """
    is_home = getattr(request.state, HOME_REGION_ATTR, None)
    if is_home is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions. Creating new resources is not available in this region for your account.",
        )


async def check_org_access(
    request: Request,
    x_user_token: Annotated[str | None, Header(alias="X-User-Token")] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
):
    """
    Check if the user has access to the organization specified in the request path.

    Supports dual authentication:
    1. Foreign user auth: X-User-ID + X-API-Key headers
    2. Session auth: X-User-Token or Authorization header
    """
    user_email = None
    user_type = None
    is_foreign_user = False
    foreign_org_id = None
    token = None   # [ghi chú] để nhánh người dùng cố định dưới đọc được ở mọi đường

    # Try foreign user authentication first
    if x_user_id and x_api_key:
        logger.info(f"Using foreign user authentication in check_org_access - User: {x_user_id}")
        from dependencies.external_user_auth import validate_external_user_token

        try:
            user_info = await validate_external_user_token(x_user_id=x_user_id, x_api_key=x_api_key)
            user_email = user_info.get("email")
            foreign_org_id = user_info.get("org_id")
            is_foreign_user = True
            logger.info(f"Foreign user authenticated - Email: {user_email}, Org: {foreign_org_id}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in foreign auth: {str(e)}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid foreign user credentials")
    else:
        # Fall back to session authentication with JWT token
        token = x_user_token
        if not token and authorization and authorization.startswith("Bearer "):
            token = authorization.replace("Bearer ", "")

        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication token is missing")

        if _nguoi_dung_co_dinh(token):   # [ghi chú] người dùng cố định ⇒ bỏ giải mã JWT / Auth0 / OTP; kiểm tổ chức bên dưới giữ nguyên
            user_email, user_type = _CO_DINH_EMAIL, "user"
        else:
            # Try to decode our self-generated JWT token first
            try:
                payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
                user_uuid = payload.get("sub")
                org_id_from_token = payload.get("org_id")

                if not user_uuid:
                    # Decodes with our key but carries no "sub", so it was not
                    # minted by platform-api (e.g. another service's HS256 token
                    # signed with a colliding secret). Hand it to the fallback
                    # validators instead of failing here.
                    raise JWTError("decoded token missing 'sub' claim")

                logger.info(f"Decoded self-generated JWT token for user: {user_uuid}, org: {org_id_from_token}")

                # If token has org_id, it's a foreign user token
                if org_id_from_token:
                    foreign_org_id = org_id_from_token
                    is_foreign_user = True
                    # For foreign users, set a placeholder email (org validation will handle access)
                    user_email = f"foreign_user_{user_uuid}@external"
                    logger.info(f"Foreign user from JWT token - Org: {foreign_org_id}")
                else:
                    # Regular user token, look up in database
                    clj_wrapper_url = f"{settings.CLJ_PG_WRAPPER_BASE_URL}/api/v1/users/{user_uuid}"

                    try:
                        response = httpx.get(clj_wrapper_url, timeout=10)
                        if response.status_code == 200:
                            user_data = response.json()
                            user_email = user_data.get("email")
                            logger.info(f"Got user email from database: {user_email}")
                        else:
                            logger.warning(f"Failed to get user from database: {response.status_code}")
                            raise Exception(f"User not found: {user_uuid}")
                    except Exception as e:
                        logger.error(f"Error fetching user from database: {str(e)}")
                        raise

            except JWTError as e:
                logger.info(f"Token is not our self-generated JWT, trying Auth0 user token: {str(e)}")

                # Try Auth0 user JWT validation (JWKS) before falling back to clj-pg-wrapper
                auth0_user = verify_auth0_user_token(token)
                if auth0_user:
                    user_email = auth0_user["email"]
                    user_type = auth0_user["user_type"]
                    # Store Auth0 user info on request state for downstream use
                    request.state.auth0_user = auth0_user
                    request.state.auth0_org_id = auth0_user.get("org_id")
                    logger.info(f"Auth0 user authenticated: {user_email}, org: {auth0_user.get('org_id')}")
                else:
                    # Fall back to clj-pg-wrapper auth
                    try:
                        user_info_from_token = check_token(token)
                        user_email = user_info_from_token.get("email")
                        user_type = user_info_from_token.get("user_type")
                        logger.info(
                            f"User authenticated: user_id="
                            f"{user_info_from_token.get('user_id')}, type={user_type}"
                        )
                    except Exception as e:
                        capture_exception(e)
                        logger.error(f"Error checking token: {str(e)}")
                        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        if not user_email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unable to authenticate user")

    # Let handlers that serve owners and members on one route shape their
    # response by role (``caller_has_owner_visibility``).
    request.state.public_caller = PublicCaller(email=user_email, user_type=user_type)

    if user_type == "admin":  # Admins have access to all orgs
        return True

    # Region enforcement for Auth0 SSO users on endpoints without {orgid} in path
    # (e.g., /public/auth/validate, /public/auth/check).
    # Uses the org_id from the Auth0 JWT claim (https://capitol.ai/org_id).
    # Skip for /region endpoint — it must be accessible from any region so
    # the frontend can discover where to redirect the user.
    auth0_user = getattr(request.state, "auth0_user", None)
    is_region_lookup = request.url.path.endswith("/region")
    if auth0_user and not is_region_lookup:
        jwt_org_id = auth0_user.get("org_id")
        if jwt_org_id:
            _, is_home = _enforce_region(user_email, auth0_user, jwt_org_id)
            if is_home is not None:
                setattr(request.state, HOME_REGION_ATTR, is_home)

    # handle denying non-admins access to admin endpoints that do not contain orgid in the path
    if request.url.path == "/public/organizations" and request.method in ["POST", "GET"]:
        # block non-admins from creating, or listing all organizations
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User does not have permission to perform this action"
        )
    if _public_path_is_admin_only(request.url.path, request.method):
        # block non-admins from cross-org reads that carry no {orgid}
        # (all internal API keys, all org members, memberships by email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User does not have permission to perform this action"
        )
    if _is_org_archived_path(request.url.path) and request.method == "PATCH":
        # Archiving is administrative only; block ordinary org members.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User does not have permission to perform this action"
        )

    # These endpoints require organization access and have the orgid as part of the path
    org_endpoint_paths = [
        "/public/organizations",
        "/public/org-members",
        "/public/org-prompts",
        "/public/guardrails",
        "/public/qdrant-clusters",
        "/public/internal-api-keys",
        "/public/client-api-keys",
        "/public/internal-api-keys",
        "/public/pipeline-data-settings",
        "/public/pipeline-basic-settings",
        "/public/pipeline-blocks-settings",
        "/public/pipeline-layout-settings",
        "/public/pipeline-settings",
        "/public/sources",
        "/public/collections",
        "/public/collection-access",
        "/public/context-graphs",
        "/public/context-graph-access",
        "/public/uploads",
        "/public/generation-dashboards",
        "/public/qdrant-clusters",
        "/public/org-node-access-config",
        "/public/chat-agents",
        "/public/agents",
    ]

    org_id_from_path = request.path_params.get("orgid")
    # logger.info(f"org_id_from_path: {org_id_from_path}")
    # logger.info(f"request.path_params: {request.path_params}")
    # logger.info(f"path: {request.url.path}, method: {request.method}, org_id_from_path: {org_id_from_path}")

    if org_id_from_path and any(path in request.url.path for path in org_endpoint_paths):
        if not validate_uuid(org_id_from_path):
            logger.error(f"Invalid orgid in path: {org_id_from_path}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid organization ID in request path"
            )

        # Block non-admins from deleting the organization itself.  Match only the
        # exact /public/organizations/{orgid}[/] path — sub-routes like
        # .../encryption/configure or .../upload_logo must fall through to the
        # regular org-access check so they can be allowed for members.
        if request.method == "DELETE" and _is_org_root_delete_path(request.url.path):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User does not have permission to delete organizations"
            )
        if request.url.path == "/public/organizations/me":
            # If the request is for the user's own profile, allow access
            return True

        # ENG-4634: some mutations / secret reads on org-scoped routes are
        # owner-only. Membership alone is not sufficient for these.
        requires_owner = _public_path_requires_org_owner(request.url.path, request.method)

        # For Auth0 SSO users, trust the JWT org claim — Auth0 enforces
        # org membership at login time, so no DynamoDB scan needed
        auth0_user = getattr(request.state, "auth0_user", None)
        if auth0_user and auth0_user.get("org_id") == org_id_from_path:
            logger.info(f"Auth0 user {user_email} org access verified via JWT claim for org {org_id_from_path}")
            if not is_region_lookup:
                org_record, is_home = _enforce_region(user_email, auth0_user, org_id_from_path)
                if is_home is not None:
                    setattr(request.state, HOME_REGION_ATTR, is_home)
            else:
                org_record = None
            if org_record:
                request.state.organization = org_record
            # Owner-gated routes still need the membership role resolved even
            # for SSO users (the JWT carries org_id but not the org role).
            if requires_owner:
                _require_owner_role_or_403(user_email, org_id_from_path)
            return True

        org_membership_client = OrgMembersClient()
        org_membership = org_membership_client.get_organization_invite_by_email_and_org(
            email=user_email, orgid=org_id_from_path
        )
        logger.info(f"org_membership found: {org_membership}")
        if not org_membership:
            logger.warning(f"User {user_email} does not have access to organization {org_id_from_path}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User does not have access to organization {org_id_from_path}",
            )

        # ENG-4634: enforce owner role for sensitive mutations / secret reads.
        # Reuses the membership row already fetched above (no extra lookup).
        if requires_owner and not _membership_is_owner(org_membership):
            logger.warning(
                f"User {user_email} is not an owner of organization {org_id_from_path}; "
                f"denying {request.method} {request.url.path}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=_OWNER_REQUIRED_DETAIL,
            )

        # Cache email → org_id so internal services can resolve the caller's org
        if user_email and org_id_from_path:
            await cache_user_org(user_email, org_id_from_path)

        if user_email == _CO_DINH_EMAIL and _nguoi_dung_co_dinh(token):   # [ghi chú] qua kiểm thành viên tổ chức này ⇒ cho get_current_user
            _CO_DINH_ORG.set(org_id_from_path)
        return True

    return False
