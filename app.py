"""Flask entry‑point, updated for the new event/state backend.

Only minimal changes were needed:
* variable names switched from *action* → *event* where clearer
* `update_plant()` now receives `new_event` instead of `new_action`
  (helpers accepts either keyword for backward compatibility)
* everything else still uses the same helper‑layer API so routes,
  templates and user experience remain unchanged.
"""

from __future__ import annotations

import os
from datetime import date
from functools import wraps
import json
import markdown as _md

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    abort,
    g,
    flash,
    jsonify,
    Response,
)
from werkzeug.security import check_password_hash

from py.db import init_db, get_conn
from py.users import (
    create_user,
    login_user,
    logout_user,
    get_user_by_username,
    get_user_by_id,
    update_user_lang,
    get_all_users,
    record_user_login,
    generate_api_key,
    revoke_api_key,
    get_user_by_api_key,
    has_api_key,
    get_api_key,
)
from py.helpers import (
    # data helpers
    load_data,
    load_one,
    save_new_plant,
    update_plant,
    # event helpers (names unchanged in helpers)
    validate_form,
    get_empty_form,
    get_form_data,
    get_form_from_plant,
    get_action_by_id,
    update_action,
    process_delete_plant,
    process_delete_action,
    form_keys_for,
    # misc utilities
    get_translations,
    duration_to_days,
    dateadd,
    get_event_specs,
    format_age,
    age_badge,
    size_badge,
    overdue_badge,
    anytime_soon_badge,
    countdown_badge,
    done_badge,
    get_daily_unique_logins,
    group_plants_by_state,
    build_state_cards,
    group_by_batch,
    explode_plant,

)
from py.processing import sort_key, get_unique_locations
try:
    from py.plant_ai import get_plant_info
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

from py.label_printer import (
    create_label_classic, create_label_circular,
    label_to_png_bytes,
)

###############################################################################
# Flask app setup
###############################################################################

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "plantlog-secret-key")
app.template_filter("dateadd")(dateadd)

@app.template_filter("mdrender")
def mdrender_filter(text):
    if not text:
        return ""
    from markupsafe import Markup
    return Markup(_md.markdown(text, extensions=["nl2br", "fenced_code", "tables"]))

init_db()

def load_config():
    """Load configuration from config.json file."""
    config_path = "config.json"
    
    # Default configuration
    default_config = {
        "mistral_small": {
            "api_key": None,
            "enabled": False
        },
        "mistral_large": {
            "api_key": None,
            "enabled": False
        },
        "features": {
            "ai_completion": False,
            "public_profiles": True
        },
        "mcp": {
            "enabled": False,
            "user_id": 1,
            "api_key": ""
        }
    }
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
        
        # Merge user config with defaults
        config = default_config.copy()
        config.update(user_config)
        
        # Enable AI completion if any model is configured
        small_enabled = (config["mistral_small"]["enabled"] and 
                        config["mistral_small"]["api_key"])
        large_enabled = (config["mistral_large"]["enabled"] and 
                        config["mistral_large"]["api_key"])
        
        if small_enabled or large_enabled:
            config["features"]["ai_completion"] = True
        else:
            config["features"]["ai_completion"] = False
        
        return config
        
    except FileNotFoundError:
        print(f"Warning: {config_path} not found, using default configuration")
        return default_config
        
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {config_path}: {e}")
        print("Using default configuration")
        return default_config
        
    except Exception as e:
        print(f"Error loading config: {e}")
        return default_config
    
# Replace the existing load_config() function with this one
CONFIG = load_config()

@app.context_processor
def inject_event_specs():
    # event_specs -> list[dict]   event_map -> dict[code -> dict]
    specs = get_event_specs()
    return dict(event_specs=specs,
                event_map={s["code"]: s for s in specs})

@app.context_processor
def inject_custom_config():
    """Make custom configuration available in templates."""
    return dict(app_config=CONFIG)

AVAILABLE_LANGS = ["en", "fr", "ru"]


@app.template_filter("todate")
def todate(value):
    return date.fromisoformat(value) if isinstance(value, str) else value

app.jinja_env.globals.update(
    format_age=format_age,
    age_badge=age_badge,
    size_badge=size_badge,
    overdue_badge=overdue_badge,
    anytime_soon_badge=anytime_soon_badge,
    countdown_badge=countdown_badge,
    done_badge=done_badge,
)
# 

###############################################################################
# Request/Session helpers
###############################################################################

from flask import request
from werkzeug.datastructures import LanguageAccept

@app.before_request
def load_logged_in_user_and_language():
    uid = session.get("uid")
    g.user = get_user_by_id(uid) if uid else None

    if g.user:
        # This handles both true logins and session resumes
        record_user_login(g.user["id"])

    lang_param = request.args.get("lang")
    should_detect_lang = (not g.user) or lang_param

    # Determine preferred language
    if should_detect_lang:
        preferred_lang = None

        if lang_param in AVAILABLE_LANGS:
            preferred_lang = lang_param
        else:
            # Try to parse the best match from Accept-Language header
            browser_langs: LanguageAccept = request.accept_languages
            preferred_lang = browser_langs.best_match(AVAILABLE_LANGS)

        if preferred_lang:
            if g.user:
                update_user_lang(g.user["id"], preferred_lang)
                g.user = get_user_by_id(g.user["id"])
            session["tmp_lang"] = preferred_lang

    # Set g.lang and session["lang"]
    if g.user:
        g.lang = g.user["lang"]
    elif "tmp_lang" in session:
        g.lang = session["tmp_lang"]
    else:
        g.lang = "en"
    session["lang"] = g.lang


def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            if request.endpoint != "index":
                flash("You must be logged in to access this page.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(**kwargs)

    return wrapped_view

def login_required_for_plant(view):
    @wraps(view)
    def wrapped_view(idx, *args, **kwargs):
        if g.user is None:
            flash("You must be logged in to access this page.", "warning")
            return redirect(url_for("login", next=request.path))
        plant = load_one(idx)
        if plant is None or plant.get("user_id") != g.user["id"]:
            abort(403)
        return view(idx, *args, **kwargs)
    return wrapped_view

def login_required_for_action(view):
    @wraps(view)
    def wrapped_view(action_id, *args, **kwargs):
        if g.user is None:
            flash("You must be logged in to access this page.", "warning")
            return redirect(url_for("login", next=request.path))
        action = get_action_by_id(action_id)
        # Find the owning plant, then compare user_id
        if action is None:
            abort(404)
        plant = load_one(action.get("plant_id"))
        if plant is None or plant.get("user_id") != g.user["id"]:
            abort(403)
        return view(action_id, *args, **kwargs)
    return wrapped_view



###############################################################################
# Routes – dashboard & list views
###############################################################################

def build_dashboard_context(user_obj, lang):
    """Return the kwargs needed by either dashboard template."""
    translations = get_translations(lang)
    plants       = sorted(load_data(user_obj["id"]), key=sort_key)
    state_groups = group_plants_by_state(plants)
    left_col, right_col = build_state_cards(state_groups, include_dead=False)
    dead_count   = len(state_groups.get("Dead", []))
    return dict(
        lang      = lang,
        t         = translations,
        plants    = plants,
        today     = date.today(),
        duration_to_days = duration_to_days,
        owner     = user_obj,
        state_groups = state_groups,
        left_col  = left_col,
        right_col = right_col,
        dead_count = dead_count,
        group_by_batch = group_by_batch,
    )

@app.route("/")
@login_required
def index():
    ctx = build_dashboard_context(g.user, g.lang)
    return render_template("index.html", **ctx)

@app.route("/graveyard")
@login_required
def graveyard():
    t      = get_translations(g.lang)
    plants = sorted(load_data(g.user["id"]), key=sort_key)
    dead   = [p for p in plants if p.get("state", {}).get("label", "").lower() == "dead"]
    return render_template("graveyard.html", dead=dead, t=t, lang=g.lang, today=date.today())

@app.route("/explode/<int:idx>", methods=["POST"])
@login_required
def explode_plant_route(idx):
    plant = load_one(idx) or abort(404)
    if plant["user_id"] != g.user["id"]:
        abort(403)
    count = max(2, int(request.form.get("count", 2)))
    explode_plant(idx, count, g.user["id"])
    return redirect(url_for("index", lang=g.lang))


@app.route("/u/<username>")
def public_view(username):
    user = get_user_by_username(username) or abort(404)
    lang = request.args.get("lang", user["lang"])
    ctx  = build_dashboard_context(user, lang)
    return render_template("index.html", public_view=True, **ctx)

@app.route("/plant/<int:idx>")
@login_required
def view_plant(idx):
    plant = load_one(idx)
    if plant is None or plant["id"] is None:
        abort(404)

    return render_template(
        "plant.html",
        plant=plant,
        lang=g.lang,
        t=get_translations(g.lang),
        duration_to_days = duration_to_days,
        today=date.today()
    )

@app.route("/p/<int:idx>")
def public_view_plant(idx):
    plant = load_one(idx)
    if plant is None or plant["id"] is None:
        abort(404)

    return render_template(
        "plant.html",
        plant=plant,
        lang=g.lang,
        t=get_translations(g.lang),
        duration_to_days = duration_to_days,
        public_view=True,
        today=date.today()
    )

###############################################################################
# Routes – Admin
###############################################################################


@app.route("/admin/users")
@login_required
def admin_users():
    if g.user["id"] != 1:
        abort(403)

    # ── per-user rows ────────────────────────────────────────────────
    all_users = get_all_users()
    user_data = []
    for user in all_users:
        plants = load_data(user["id"])
        user_data.append({
            "id":          user["id"],
            "username":    user["username"],
            "plant_count": len(plants),
            "created_at":  user["created_at"],
            "last_login":  user["last_login"],
        })

    # ── today’s unique-login count ───────────────────────────────────
    today = date.today().isoformat()                    # 'YYYY-MM-DD'
    row   = get_daily_unique_logins(today, today)       # [(day, cnt)] or []
    today_logins = row[0][1] if row else 0

    return render_template(
        "admin_users.html",
        users=user_data,
        today_logins=today_logins,       # ↰ pass to template
        lang=g.lang,
        t=get_translations(g.lang),
    )

###############################################################################
# Routes – plant CRUD
###############################################################################

@app.route("/add", methods=["GET", "POST"])
@login_required
def add_plant():
    lang = g.lang
    translations = get_translations(lang)
    plants = load_data(g.user["id"])

    if request.method == "POST":
        form = get_form_data(request)
        errors, event = validate_form(form, translations, context="add")
        if not errors:
            save_new_plant(form, event, g.user["id"])
            return redirect(url_for("index", lang=lang))
        # fall‑through: show form with errors
    else:
        form = get_empty_form()
        errors = []

    return render_template(
        "add.html",
        lang=lang,
        t=translations,
        form=form,
        errors=errors,
        locations=get_unique_locations(plants),
        today=date.today(),
    )


@app.route("/edit_plant/<int:idx>", methods=["GET", "POST"])
@login_required_for_plant
def edit_plant(idx):
    plant = load_one(idx)
    if plant is None:
        abort(404)

    lang = g.lang
    translations = get_translations(lang)

    if request.method == "POST":
        form = get_form_data(request)
        errors, _ = validate_form(form, translations, context="edit")
        if not errors:
            update_plant(plant["id"], form, new_event=None)  # metadata‑only edit
            return redirect(url_for("index", lang=lang))
    else:
        form = get_form_from_plant(plant)
        errors = []

    return render_template(
        "edit_plant.html",
        form=form,
        errors=errors,
        edit_idx=idx,
        lang=lang,
        t=translations,
    )


@app.route("/delete_plant/<int:idx>")
@login_required_for_plant
def delete_plant(idx):
    process_delete_plant(idx, g.user["id"])
    return redirect(url_for("index", lang=g.lang))


###############################################################################
# Routes – event (stage) CRUD
###############################################################################

@app.route("/add_stage/<int:idx>", methods=["GET", "POST"])
@login_required_for_plant
def add_stage(idx):
    plant = load_one(idx) or abort(404)

    lang = request.args.get("lang", "en")
    translations = get_translations(lang)

    if request.method == "POST":
        form = get_form_data(request)
        errors, event = validate_form(form, translations, context="add_stage")
        if not errors:
            plant_data = {
                "common": plant["common"],
                "latin": plant["latin"],
                "location": plant["location"],
                "notes": plant["notes"],
            }
            update_plant(plant["id"], plant_data, new_event=event)
            return redirect(url_for("index", lang=lang))
    else:
        form = get_empty_form()
        form.update({"common": plant["common"], "latin": plant["latin"]})
        errors = {}

    return render_template(
        "add_stage.html",
        form=form,
        errors=errors,
        plant_idx=idx,
        lang=lang,
        t=translations,
        today=date.today()
    )


@app.route("/edit_stage/<int:action_id>", methods=["GET", "POST"])
@login_required_for_action
def edit_stage(action_id):
    ev = get_action_by_id(action_id) or abort(404)

    lang = g.lang
    translations = get_translations(lang)

    if request.method == "POST":
        form = get_form_data(request)
        errors, event = validate_form(form, translations, context="edit_stage")
        if not errors:
            update_action(action_id, event)
            return redirect(url_for("index", lang=lang))
    else:
        form = get_empty_form()
        form["status"] = ev["action"]
        date_field, extras = form_keys_for(ev)
        form[date_field] = ev["start"]
        form.update(extras)
        errors = []
    return render_template(
        "edit_stage.html",
        form=form,
        errors=errors,
        action_id=action_id,
        lang=lang,
        t=translations,
    )


@app.route("/delete_stage/<int:action_id>", methods=["POST"])
@login_required_for_action
def delete_stage(action_id):
    process_delete_action(action_id, g.user["id"])
    return redirect(url_for("index", lang=g.lang))


###############################################################################
# Auth routes
###############################################################################

@app.route("/register", methods=["GET", "POST"])
def register():
    t = get_translations(g.lang)
    if request.method == "POST":
        username = request.form["username"].strip()
        pw = request.form["password"]
        if not username or not pw:
            flash(t["Username & password required"], "danger")
        elif get_user_by_username(username):
            flash(t["Username already taken"], "danger")
        else:
            create_user(username, pw, request.form.get("lang", "en"))
            flash(t["Account created – you can now log in"], "success")
            return redirect(url_for("login"))
    return render_template("register.html", lang=g.lang, t=t)


@app.route("/login", methods=["GET", "POST"])
def login():
    t = get_translations(g.lang)
    next_page = request.args.get("next", url_for("index"))
    if request.method == "POST":
        user = get_user_by_username(request.form["username"])
        if user and check_password_hash(user["pw_hash"], request.form["password"]):
            login_user(user)
            return redirect(next_page)
        flash(t["Bad credentials"], "danger")
    return render_template("login.html", lang=g.lang, t=t)


@app.route("/logout")
def logout():
    t = get_translations(g.lang)
    logout_user()
    flash(t["Logged out"], "info")
    return redirect(url_for("login"))

@app.route('/help')
def help_page():
    return render_template('help.html', lang=g.lang, t=get_translations(g.lang))


###############################################################################
# Static helpers
###############################################################################

@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")

###############################################################################
# AI Routes - Simplified
###############################################################################

@app.route("/ai/complete_plant", methods=["POST"])
@login_required
def ai_complete_plant():
    """Get plant information using simplified AI service."""
    
    # Check if AI is enabled
    if not CONFIG.get("features", {}).get("ai_completion", False):
        return jsonify({"error": "AI completion not enabled"}), 503
    
    if not AI_AVAILABLE:
        return jsonify({"error": "AI service not available"}), 503
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Get plant name (common or latin)
        plant_name = data.get('common', '').strip() or data.get('latin', '').strip()
        if not plant_name:
            return jsonify({"error": "Plant name required"}), 400
        
        stage = data.get('status', 'sown')
        
        # Get plant information
        result = get_plant_info(CONFIG, plant_name, stage, g.lang)
        
        # Add flash message based on source with more detail
        source = result.get('source', 'ai')
        if source == 'boutique_vegetale':
            flash(f"✨ Plant information found on Boutique Végétale and processed with Mistral AI", "success")
        elif source == 'mistral_large':
            flash(f"🤖 Plant information provided by Mistral Large AI model", "info")
        else:
            flash(f"🤖 Plant information provided by Mistral AI", "info")
        
        return jsonify(result)
        
    except Exception as e:
        app.logger.error(f"AI completion error: {e}")
        return jsonify({"error": str(e)}), 500

###############################################################################
# Label / Printer Routes
###############################################################################

def _make_label_image(plant, style):
    common   = plant.get("common", "")
    latin    = plant.get("latin",  "")
    variety  = plant.get("variety") or None
    date_str = date.today().strftime("%d-%m-%Y")
    if style == "circular":
        return create_label_circular(common, latin, date_str, variety)
    return create_label_classic(common, latin, date_str, variety)


@app.route("/label_preview/<int:idx>")
@login_required
def label_preview(idx):
    plant = load_one(idx) or abort(404)
    if plant.get("user_id") != g.user["id"]:
        abort(403)
    style = request.args.get("style", "classic")
    img = _make_label_image(plant, style)
    return Response(label_to_png_bytes(img), mimetype="image/png")


@app.route("/print_label/<int:idx>", methods=["POST"])
@login_required
def print_label_route(idx):
    """Queue a print job — label_client.py on the user's machine picks it up."""
    plant = load_one(idx) or abort(404)
    if plant.get("user_id") != g.user["id"]:
        abort(403)
    data  = request.get_json(silent=True) or {}
    style = data.get("style", "classic")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO print_jobs (user_id, plant_id, style) VALUES (?,?,?)",
            (g.user["id"], idx, style),
        )
        job_id = cur.lastrowid
        conn.commit()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/print_job_status/<int:job_id>")
@login_required
def print_job_status(job_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, error_msg FROM print_jobs WHERE id=? AND user_id=?",
            (job_id, g.user["id"]),
        ).fetchone()
    if row is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"status": row["status"], "error": row["error_msg"]})


###############################################################################
# Quick-log Route (AJAX)
###############################################################################

@app.route("/quick_log/<int:idx>", methods=["POST"])
@login_required
def quick_log(idx):
    """Log a simple event (water / fertilize / custom) via AJAX from dashboard or plant page."""
    plant = load_one(idx)
    if plant is None or plant.get("user_id") != g.user["id"]:
        abort(403)
    data       = request.get_json(silent=True) or {}
    event_code = data.get("event", "water")
    happened   = data.get("date") or date.today().isoformat()
    # Build a minimal form dict and reuse existing helpers
    form = get_empty_form()
    form["status"] = event_code
    if event_code == "custom":
        form["event_custom_label"] = data.get("custom_label", "Note")
        form["event_custom_note"]  = data.get("custom_note", "")
    form["event_date"] = happened
    translations = get_translations(g.lang)
    errors, event = validate_form(form, translations, context="add_stage")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    plant_data = {k: plant.get(k, "") or "" for k in ("common", "latin", "location", "notes", "variety")}
    update_plant(plant["id"], plant_data, new_event=event)
    return jsonify({"ok": True})


###############################################################################
# Clone Plant Route
###############################################################################

@app.route("/clone_plant/<int:idx>", methods=["POST"])
@login_required_for_plant
def clone_plant(idx):
    """Duplicate a plant's metadata (no event history) and redirect to the new plant."""
    plant = load_one(idx) or abort(404)
    form = {
        "common":   plant["common"],
        "latin":    plant["latin"],
        "location": plant.get("location") or "",
        "notes":    plant.get("notes") or "",
        "variety":  plant.get("variety") or "",
        "status":   "sow",
        "date_happened": date.today().isoformat(),
    }
    translations = get_translations(g.lang)
    errors, event = validate_form(form, translations, context="add")
    if not errors:
        save_new_plant(form, event, g.user["id"])
    return redirect(url_for("index", lang=g.lang))


###############################################################################
# Downloads
###############################################################################

@app.route("/download/label_client.py")
@login_required
def download_label_client():
    return Response(
        open(os.path.join(os.path.dirname(__file__), "scripts", "label_client.py"), "rb").read(),
        mimetype="text/x-python",
        headers={"Content-Disposition": "attachment; filename=label_client.py"},
    )


@app.route("/download/mcp_server.py")
@login_required
def download_mcp_server():
    return Response(
        open(os.path.join(os.path.dirname(__file__), "scripts", "mcp_server.py"), "rb").read(),
        mimetype="text/x-python",
        headers={"Content-Disposition": "attachment; filename=mcp_server.py"},
    )


###############################################################################
# Settings (API key management)
###############################################################################

@app.route("/settings", methods=["GET"])
@login_required
def settings():
    return render_template(
        "settings.html",
        lang=g.lang,
        t=get_translations(g.lang),
        has_key=has_api_key(g.user["id"]),
        new_key=session.pop("new_api_key", None),
        revealed_key=session.pop("revealed_key", None),
    )


@app.route("/settings/generate_key", methods=["POST"])
@login_required
def settings_generate_key():
    raw = generate_api_key(g.user["id"])
    session["new_api_key"] = raw        # shown once via settings page
    return redirect(url_for("settings", lang=g.lang))


@app.route("/settings/revoke_key", methods=["POST"])
@login_required
def settings_revoke_key():
    revoke_api_key(g.user["id"])
    flash(get_translations(g.lang).get("settings_key_revoked", "API key revoked."), "info")
    return redirect(url_for("settings", lang=g.lang))


@app.route("/settings/reveal_key", methods=["POST"])
@login_required
def settings_reveal_key():
    t = get_translations(g.lang)
    password = request.form.get("password", "")
    if check_password_hash(g.user["pw_hash"], password):
        key = get_api_key(g.user["id"])
        if key:
            session["revealed_key"] = key
        else:
            flash(t.get("settings_key_none", "No API key set."), "warning")
    else:
        flash(t.get("settings_wrong_password", "Wrong password."), "danger")
    return redirect(url_for("settings", lang=g.lang))


###############################################################################
# JSON API  (Bearer-token auth, consumed by the MCP server)
###############################################################################

def _api_user():
    """Resolve the user from Authorization: Bearer or X-API-Key header."""
    key = request.headers.get("X-API-Key", "")
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:]
    if not key:
        return None
    return get_user_by_api_key(key)


def _api_auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _api_user()
        if user is None:
            return jsonify({"error": "Unauthorized"}), 401
        g.api_user = user
        return view(*args, **kwargs)
    return wrapped


def _plant_summary(p):
    state   = p.get("state") or {}
    current = p.get("current") or {}
    return {
        "id":       p["id"],
        "common":   p["common"],
        "latin":    p["latin"],
        "variety":  p.get("variety"),
        "location": p.get("location"),
        "notes":    p.get("notes"),
        "state":    state.get("label"),
        "last_event": {
            "type": current.get("action"),
            "date": current.get("start"),
        } if current else None,
    }


def _event_detail(h):
    ev = {"id": h["id"], "type": h["action"], "date": h["start"]}
    if h["action"] == "sow" and "range" in h:
        ev["sprout_range"] = {
            "min": h["range"][0], "min_unit": h["range"][1],
            "max": h["range"][2], "max_unit": h["range"][3],
        }
    elif h["action"] in ("soak", "strat") and "duration" in h:
        ev["duration"] = {"val": h["duration"][0], "unit": h["duration"][1]}
    elif h["action"] == "measure" and "size" in h:
        ev["size"] = {"val": h["size"][0], "unit": h["size"][1]}
    elif h["action"] == "custom":
        ev["label"] = h.get("custom_label")
        ev["note"]  = h.get("custom_note")
    return ev


@app.route("/api/plants", methods=["GET"])
@_api_auth_required
def api_list_plants():
    plants = load_data(g.api_user["id"])
    return jsonify([_plant_summary(p) for p in plants])


@app.route("/api/plants/<int:idx>", methods=["GET"])
@_api_auth_required
def api_get_plant(idx):
    p = load_one(idx)
    if p is None or p.get("user_id") != g.api_user["id"]:
        return jsonify({"error": "Not found"}), 404
    result = _plant_summary(p)
    result["history"] = [_event_detail(h) for h in p.get("history", [])]
    return jsonify(result)


@app.route("/api/plants", methods=["POST"])
@_api_auth_required
def api_add_plant():
    data = request.get_json(silent=True) or {}
    form = get_empty_form()
    form.update({
        "common":            data.get("common", ""),
        "latin":             data.get("latin", ""),
        "variety":           data.get("variety", ""),
        "nickname":          data.get("nickname", ""),
        "count":             max(1, int(data.get("count", 1))),
        "location":          data.get("location", ""),
        "notes":             data.get("notes", ""),
        "status":            data.get("first_event", "sow"),
        "event_date":        data.get("event_date") or date.today().isoformat(),
        "event_range_min":   data.get("sprout_min_days", 14),
        "event_range_min_u": "days",
        "event_range_max":   data.get("sprout_max_days", 30),
        "event_range_max_u": "days",
        "event_dur_val":     data.get("duration_val", 24),
        "event_dur_unit":    data.get("duration_unit", "hours"),
    })
    t = get_translations(g.api_user["lang"])
    errors, event = validate_form(form, t, context="add")
    if errors:
        return jsonify({"error": errors}), 400
    plant_id = save_new_plant(form, event, g.api_user["id"])
    return jsonify({"ok": True, "id": plant_id, "message": f"Plant '{form['common']}' added."}), 201


@app.route("/api/plants/<int:idx>", methods=["PATCH"])
@_api_auth_required
def api_update_plant(idx):
    p = load_one(idx)
    if p is None or p.get("user_id") != g.api_user["id"]:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    plant_data = {
        "common":    data.get("common",    p["common"]),
        "latin":     data.get("latin",     p["latin"]),
        "location":  data.get("location",  p.get("location") or ""),
        "notes":     data.get("notes",     p.get("notes") or ""),
        "variety":   data.get("variety",   p.get("variety") or ""),
        "nickname":  data.get("nickname",  p.get("nickname") or ""),
        "count":     data.get("count",     p.get("count", 1)),
    }
    update_plant(idx, plant_data, new_event=None)
    return jsonify({"ok": True})


@app.route("/api/plants/<int:idx>/explode", methods=["POST"])
@_api_auth_required
def api_explode_plant(idx):
    p = load_one(idx)
    if p is None or p.get("user_id") != g.api_user["id"]:
        return jsonify({"error": "Not found"}), 404
    data  = request.get_json(silent=True) or {}
    count = max(2, int(data.get("count", 2)))
    ids   = explode_plant(idx, count, g.api_user["id"])
    return jsonify({"ok": True, "plant_ids": ids})


@app.route("/api/plants/<int:idx>", methods=["DELETE"])
@_api_auth_required
def api_delete_plant(idx):
    p = load_one(idx)
    if p is None or p.get("user_id") != g.api_user["id"]:
        return jsonify({"error": "Not found"}), 404
    process_delete_plant(idx, g.api_user["id"])
    return jsonify({"ok": True})


@app.route("/api/plants/<int:idx>/events", methods=["POST"])
@_api_auth_required
def api_add_event(idx):
    p = load_one(idx)
    if p is None or p.get("user_id") != g.api_user["id"]:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    form = get_empty_form()
    form.update({
        "status":             data.get("event_type", "water"),
        "event_date":         data.get("event_date") or date.today().isoformat(),
        "event_range_min":    data.get("sprout_min_days", 14),
        "event_range_min_u":  "days",
        "event_range_max":    data.get("sprout_max_days", 30),
        "event_range_max_u":  "days",
        "event_dur_val":      data.get("duration_val", 24),
        "event_dur_unit":     data.get("duration_unit", "hours"),
        "event_size_val":     data.get("size_val", 0),
        "event_size_unit":    data.get("size_unit", "cm"),
        "event_custom_label": data.get("custom_label", ""),
        "event_custom_note":  data.get("custom_note", ""),
    })
    t = get_translations(g.api_user["lang"])
    errors, event = validate_form(form, t, context="add_stage")
    if errors:
        return jsonify({"error": errors}), 400
    plant_data = {k: p.get(k) or "" for k in ("common", "latin", "location", "notes", "variety")}
    update_plant(idx, plant_data, new_event=event)
    return jsonify({"ok": True}), 201


@app.route("/api/event_types", methods=["GET"])
@_api_auth_required
def api_event_types():
    return jsonify(get_event_specs())


@app.route("/api/print_queue", methods=["POST"])
@_api_auth_required
def api_queue_print():
    """Queue a print job via API (e.g. from MCP tool)."""
    data  = request.get_json(silent=True) or {}
    idx   = data.get("plant_id")
    if not idx:
        return jsonify({"error": "plant_id required"}), 400
    p = load_one(idx)
    if p is None or p.get("user_id") != g.api_user["id"]:
        return jsonify({"error": "Not found"}), 404
    style = data.get("style", "classic")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO print_jobs (user_id, plant_id, style) VALUES (?,?,?)",
            (g.api_user["id"], idx, style),
        )
        job_id = cur.lastrowid
        conn.commit()
    return jsonify({"ok": True, "job_id": job_id}), 201


@app.route("/api/print_queue/pending", methods=["GET"])
@_api_auth_required
def api_print_queue_pending():
    """Printer client polls this to get its pending jobs."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT j.id, j.style, j.created_at,
                      p.common, p.latin, p.variety
               FROM print_jobs j
               JOIN plants p ON p.id = j.plant_id
               WHERE j.user_id = ? AND j.status = 'pending'
               ORDER BY j.created_at ASC""",
            (g.api_user["id"],),
        ).fetchall()
    return jsonify([
        {
            "job_id":    r["id"],
            "style":     r["style"],
            "created_at": r["created_at"],
            "plant": {
                "common":  r["common"],
                "latin":   r["latin"],
                "variety": r["variety"],
            },
        }
        for r in rows
    ])


@app.route("/api/print_queue/<int:job_id>/done", methods=["POST"])
@_api_auth_required
def api_print_job_done(job_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE print_jobs SET status='done', updated_at=datetime('now') WHERE id=? AND user_id=?",
            (job_id, g.api_user["id"]),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/print_queue/<int:job_id>/error", methods=["POST"])
@_api_auth_required
def api_print_job_error(job_id):
    data = request.get_json(silent=True) or {}
    msg  = data.get("error", "Unknown error")
    with get_conn() as conn:
        conn.execute(
            """UPDATE print_jobs SET status='error', error_msg=?, updated_at=datetime('now')
               WHERE id=? AND user_id=?""",
            (msg, job_id, g.api_user["id"]),
        )
        conn.commit()
    return jsonify({"ok": True})


###############################################################################
# Main entry point
###############################################################################

if __name__ == "__main__":
    app.run(debug=True)
