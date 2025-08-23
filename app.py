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
    jsonify
)
from werkzeug.security import check_password_hash

from py.db import init_db
from py.users import (
    create_user,
    login_user,
    logout_user,
    get_user_by_username,
    get_user_by_id,
    update_user_lang,
    get_all_users,
    record_user_login
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

)
from py.processing import sort_key, get_unique_locations
try:
    from py.plant_ai import get_plant_info
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

###############################################################################
# Flask app setup
###############################################################################

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "plantlog-secret-key")
app.template_filter("dateadd")(dateadd)
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
    left_col, right_col = build_state_cards(state_groups)
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
    )

@app.route("/")
@login_required
def index():
    ctx = build_dashboard_context(g.user, g.lang)
    return render_template("index.html", **ctx)

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
# Main entry point
###############################################################################

if __name__ == "__main__":
    app.run(debug=True)
