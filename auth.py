"""
Google OAuth + Group-based access control for the Zscaler URL Checker.

Roles:
  - admin: members of the admin Google Group (full access including Settings)
  - user:  members of the user Google Group (checker + policy views only)

Requires:
  - Google Cloud OAuth 2.0 Client ID
  - Service account with domain-wide delegation (for group membership checks)
"""
import functools
import json
import os

from authlib.integrations.flask_client import OAuth
from flask import (
    Blueprint, current_app, redirect, render_template,
    request, session, url_for, jsonify,
)

auth_bp = Blueprint("auth", __name__)
oauth = OAuth()

ALLOWED_DOMAINS = ["ncmcs.org", "ncmcs.support"]
ADMIN_GROUP = "engineering@ncmcs.org"
USER_GROUP = "mcsallstaff@ncmcs.org"
POLICY_GROUPS = ["dif@ncmcs.org", "technology@ncmcs.org"]

AUTH_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "auth_config.json"
)


def load_auth_config():
    """Load OAuth config from auth_config.json."""
    if os.path.exists(AUTH_CONFIG_FILE):
        with open(AUTH_CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_auth_config(data):
    with open(AUTH_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def init_auth(app):
    """Initialize OAuth with the Flask app."""
    config = load_auth_config()

    app.secret_key = config.get("secret_key", os.urandom(32).hex())

    client_id = config.get("google_client_id", "")
    client_secret = config.get("google_client_secret", "")

    if client_id and client_secret:
        oauth.init_app(app)
        oauth.register(
            name="google",
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        app.config["AUTH_ENABLED"] = True
    else:
        app.config["AUTH_ENABLED"] = False

    app.config["AUTH_CONFIG"] = config


def check_group_membership(email):
    """
    Check if a user belongs to admin or user Google Groups.
    Uses the Admin Directory API with a service account.
    Falls back to domain-only check if no service account is configured.
    """
    config = load_auth_config()
    sa_file = config.get("service_account_file", "")
    admin_email = config.get("admin_email", "")

    if not sa_file or not os.path.exists(sa_file):
        # No service account — fall back to domain-only access
        domain = email.split("@")[1] if "@" in email else ""
        if domain in ALLOWED_DOMAINS:
            # Check if email is in the admin list from config
            admin_emails = config.get("admin_emails", [])
            return "admin" if email in admin_emails else "user"
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            sa_file,
            scopes=["https://www.googleapis.com/auth/admin.directory.group.member.readonly"],
            subject=admin_email,
        )
        service = build("admin", "directory_v1", credentials=credentials)

        # Check admin group first
        try:
            result = service.members().hasMember(
                groupKey=ADMIN_GROUP, memberKey=email
            ).execute()
            if result.get("isMember"):
                return "admin"
        except Exception:
            pass

        # Check user group
        try:
            result = service.members().hasMember(
                groupKey=USER_GROUP, memberKey=email
            ).execute()
            if result.get("isMember"):
                return "user"
        except Exception:
            pass

        return None
    except Exception as e:
        print(f"Group membership check failed: {e}")
        # Fall back to domain check
        domain = email.split("@")[1] if "@" in email else ""
        if domain in ALLOWED_DOMAINS:
            admin_emails = config.get("admin_emails", [])
            return "admin" if email in admin_emails else "user"
        return None


def check_policy_group_membership(email):
    """Check if a user belongs to any of the policy-authorized groups."""
    config = load_auth_config()
    sa_file = config.get("service_account_file", "")
    admin_email = config.get("admin_email", "")

    if not sa_file or not os.path.exists(sa_file):
        return False

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            sa_file,
            scopes=["https://www.googleapis.com/auth/admin.directory.group.member.readonly"],
            subject=admin_email,
        )
        service = build("admin", "directory_v1", credentials=credentials)

        for group in POLICY_GROUPS:
            try:
                result = service.members().hasMember(
                    groupKey=group, memberKey=email
                ).execute()
                if result.get("isMember"):
                    return True
            except Exception:
                pass

        return False
    except Exception as e:
        print(f"Policy group membership check failed: {e}")
        return False


# ── Decorators ────────────────────────────────────────────────────────

def login_required(f):
    """Require login. Redirects to /login if not authenticated."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not current_app.config.get("AUTH_ENABLED"):
            return f(*args, **kwargs)
        if not session.get("user"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require admin role. Returns 403 for non-admins."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not current_app.config.get("AUTH_ENABLED"):
            return f(*args, **kwargs)
        if not session.get("user"):
            return redirect(url_for("auth.login"))
        if session.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def policy_required(f):
    """Require admin role or membership in a policy-authorized group."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not current_app.config.get("AUTH_ENABLED"):
            return f(*args, **kwargs)
        if not session.get("user"):
            return redirect(url_for("auth.login"))
        if session.get("role") == "admin" or session.get("can_view_policy"):
            return f(*args, **kwargs)
        return jsonify({"error": "Policy view access required"}), 403
    return decorated


# ── Routes ────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login():
    if not current_app.config.get("AUTH_ENABLED"):
        return redirect(url_for("index"))
    if session.get("user"):
        return redirect(url_for("index"))
    return render_template("login.html", error=request.args.get("error"))


@auth_bp.route("/login/google")
def login_google():
    if not current_app.config.get("AUTH_ENABLED"):
        return redirect(url_for("index"))
    redirect_uri = url_for("auth.callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/auth/callback")
def callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        return redirect(url_for("auth.login", error="Authentication failed. Please try again."))

    userinfo = token.get("userinfo", {})

    # The ID token doesn't include 'picture' — fetch full profile from Google's userinfo endpoint
    resp = oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo")
    if resp.ok:
        full_profile = resp.json()
        userinfo.update(full_profile)

    email = userinfo.get("email", "")
    domain = email.split("@")[1] if "@" in email else ""

    if domain not in ALLOWED_DOMAINS:
        return render_template("login.html", error="Access is restricted to authorized domains.")

    role = check_group_membership(email)
    if not role:
        return render_template(
            "login.html",
            error="You are not a member of an authorized group. Contact your IT administrator.",
        )

    session["user"] = {
        "email": email,
        "name": userinfo.get("name", email),
        "picture": userinfo.get("picture", ""),
    }
    session["role"] = role
    # Admins always get policy access; otherwise check policy groups
    session["can_view_policy"] = (role == "admin") or check_policy_group_membership(email)
    return redirect(url_for("index"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/api/auth-status")
def auth_status():
    """Return current auth state for the frontend."""
    if not current_app.config.get("AUTH_ENABLED"):
        return jsonify({"auth_enabled": False})
    user = session.get("user")
    if not user:
        return jsonify({"auth_enabled": True, "logged_in": False})
    return jsonify({
        "auth_enabled": True,
        "logged_in": True,
        "user": user,
        "role": session.get("role", "user"),
        "can_view_policy": session.get("can_view_policy", False),
    })
