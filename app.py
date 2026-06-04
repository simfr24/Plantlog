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
from urllib.parse import urlencode
import markdown as _md
import PIL.Image

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
    LANGUAGES,
    AVAILABLE_LANGS,
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
    group_by_latin,
    duplicate_plant,
    build_location_tree,
    compute_attention,
)
from py.processing import sort_key, get_unique_locations
from py.mcp import blueprint as mcp_blueprint
from py.label_printer import (
    create_label_classic, create_label_circular,
    create_label_minimal, create_label_detailed_v, create_label_detailed_h, create_label_qr,
    create_label_stake_wrap, create_label_freetext,
    label_to_png_bytes, label_to_printer_bytes,
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

from py.translate import (
    translate_content, translate_html, tr,
    list_cached_translations, update_cached_translation, delete_cached_translation,
)


@app.template_filter("t_content")
def t_content_filter(text):
    """Translate user-generated free text into the current UI language."""
    if not text or not CONFIG.get("features", {}).get("translate_content", True):
        return text
    return translate_content(text, getattr(g, "lang", None), getattr(g, "content_lang", None))


def _tr_global(text):
    """Template global: translate + report whether it changed (for indicators)."""
    if not CONFIG.get("features", {}).get("translate_content", True):
        from types import SimpleNamespace
        return SimpleNamespace(text=text, translated=False)
    return tr(text)


def _tr_md_global(text):
    """Template global for Markdown content: render to HTML, then translate the
    HTML (preserving tables/lists). Returns a namespace with ``.html`` (safe
    Markup), ``.translated`` and ``.source`` for the indicator."""
    from types import SimpleNamespace
    from markupsafe import Markup

    rendered = mdrender_filter(text)
    source = getattr(g, "content_lang", None)
    target = getattr(g, "lang", None)
    if (not text or not target
            or not CONFIG.get("features", {}).get("translate_content", True)):
        return SimpleNamespace(html=rendered, translated=False, source=source)

    translated = translate_html(str(rendered), target, source)
    if translated and translated.strip() != str(rendered).strip():
        return SimpleNamespace(html=Markup(translated), translated=True, source=source)
    return SimpleNamespace(html=rendered, translated=False, source=source)

init_db()
app.register_blueprint(mcp_blueprint)

def load_config():
    """Load configuration from config.json file."""
    config_path = "config.json"
    
    # Default configuration
    default_config = {
        "features": {
            "public_profiles": True,
            "translate_content": True
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

@app.context_processor
def inject_languages():
    """Expose the language list so selectors can be rendered from one source."""
    return dict(languages=LANGUAGES)

@app.template_filter("todate")
def todate(value):
    return date.fromisoformat(value) if isinstance(value, str) else value

app.jinja_env.globals.update(
    tr=_tr_global,
    tr_md=_tr_md_global,
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

    # A ?lang= in the URL is a *temporary*, manual override: it is remembered for
    # this browser session only and never touches the account preference. The
    # permanent language lives on the account and is changed from Settings.
    # We store the choice and immediately redirect to the same URL without the
    # param, so it never lingers in the address bar.
    if lang_param in AVAILABLE_LANGS:
        session["lang_override"] = lang_param
        if request.method == "GET":
            args = request.args.to_dict(flat=True)
            args.pop("lang", None)
            return redirect(request.path + ("?" + urlencode(args) if args else ""))

    # Resolve the active language with a clear precedence:
    #   1. a manual temporary override (the ?lang picker), for this session,
    #   2. logged-in users follow their saved account preference,
    #   3. returning visitors keep their detected session choice,
    #   4. first-time visitors are detected once from the browser, then remembered.
    override = session.get("lang_override")
    if override in AVAILABLE_LANGS:
        g.lang = override
    elif g.user:
        g.lang = g.user["lang"]
    elif session.get("lang") in AVAILABLE_LANGS:
        g.lang = session["lang"]
    else:
        browser_langs: LanguageAccept = request.accept_languages
        g.lang = browser_langs.best_match(AVAILABLE_LANGS) or "en"

    session["lang"] = g.lang

    # Hint for translating user content: assume it was authored in the content
    # owner's language. Defaults to the logged-in user; views that display
    # someone else's content (public profiles) override this.
    g.content_lang = g.user["lang"] if g.user else None


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
    # Content shown here belongs to user_obj, so treat its language as theirs.
    g.content_lang = user_obj["lang"]
    translations = get_translations(lang)
    plants       = sorted(load_data(user_obj["id"]), key=sort_key)
    state_groups = group_plants_by_state(plants)
    left_col, right_col = build_state_cards(state_groups, include_dead=False)
    dead_count   = len(state_groups.get("Dead", []))
    stash_count  = len(state_groups.get("Stashed", [])) + len(state_groups.get("Ordered", []))
    alive_plants = [p for p in plants if p.get("state") and p["state"].get("label") not in ("Dead", "Stashed", "Ordered")]
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
        stash_count = stash_count,
        group_by_latin = group_by_latin,
        location_tree = build_location_tree(alive_plants),
        attention     = compute_attention(alive_plants),
        alive_count   = len(alive_plants),
    )

@app.route("/")
@login_required
def index():
    ctx = build_dashboard_context(g.user, g.lang)
    return render_template("dashboard.html", **ctx)

@app.route("/garden")
@login_required
def garden():
    ctx = build_dashboard_context(g.user, g.lang)
    return render_template("new_home.html", **ctx)

@app.route("/list")
@login_required
def list_view():
    ctx = build_dashboard_context(g.user, g.lang)
    return render_template("index.html", **ctx)

@app.route("/graveyard")
@login_required
def graveyard():
    t      = get_translations(g.lang)
    plants = load_data(g.user["id"])
    dead   = sorted(
        [p for p in plants if p.get("state") and p["state"].get("label", "").lower() == "dead"],
        key=lambda p: (p.get("current") or {}).get("start", ""),
        reverse=True,
    )
    return render_template("graveyard.html", dead=dead, t=t, lang=g.lang, today=date.today())

@app.route("/stash")
@login_required
def stash():
    t      = get_translations(g.lang)
    plants = load_data(g.user["id"])
    ordered = sorted(
        [p for p in plants if p.get("state") and p["state"].get("label", "").lower() == "ordered"],
        key=lambda p: (p.get("current") or {}).get("start", ""),
        reverse=True,
    )
    stashed = sorted(
        [p for p in plants if p.get("state") and p["state"].get("label", "").lower() == "stashed"],
        key=lambda p: (p.get("current") or {}).get("start", ""),
        reverse=True,
    )
    return render_template("stash.html", ordered=ordered, stashed=stashed, t=t, lang=g.lang, today=date.today())


@app.route("/plant_from_stash/<int:idx>", methods=["POST"])
@login_required_for_plant
def plant_from_stash(idx):
    plant = load_one(idx) or abort(404)
    event = {
        "action": "plant",
        "start": date.today().isoformat(),
    }
    plant_data = {k: plant.get(k) or "" for k in ("common", "latin", "location", "notes", "variety", "nickname", "rusticity")}
    plant_data["count"] = plant.get("count", 1)
    update_plant(plant["id"], plant_data, new_event=event)
    return redirect(url_for("view_plant", idx=idx))


@app.route("/receive_plant/<int:idx>", methods=["POST"])
@login_required_for_plant
def receive_plant(idx):
    plant = load_one(idx) or abort(404)
    # Copy source from the most recent order event
    order_ev = next((e for e in reversed(plant["history"]) if e["action"] == "order"), None)
    event = {
        "action": "acquire",
        "start": date.today().isoformat(),
        "acquire_type": "received",
        "source": order_ev["source"] if order_ev and order_ev.get("source") else None,
        "price": None,
        "price_currency": None,
    }
    plant_data = {k: plant.get(k) or "" for k in ("common", "latin", "location", "notes", "variety", "nickname", "rusticity")}
    plant_data["count"] = plant.get("count", 1)
    update_plant(plant["id"], plant_data, new_event=event)
    return redirect(url_for("stash"))


@app.route("/u/<username>")
def public_view(username):
    user = get_user_by_username(username) or abort(404)
    # Show the page in the viewer's resolved language; the owner's content is
    # auto-translated (build_dashboard_context sets g.content_lang accordingly).
    ctx  = build_dashboard_context(user, g.lang)
    return render_template("new_home.html", public_view=True, **ctx)

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

    if g.user and plant.get("user_id") == g.user["id"]:
        return redirect(url_for("view_plant", idx=idx))

    # Content belongs to the plant's owner; use their language as the hint.
    owner = get_user_by_id(plant["user_id"])
    g.content_lang = owner["lang"] if owner else None

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

    today = date.today().isoformat()                    # 'YYYY-MM-DD'

    # ── per-user rows ────────────────────────────────────────────────
    all_users = get_all_users()
    user_data = []
    for user in all_users:
        plants = load_data(user["id"])
        last_login = user["last_login"]
        user_data.append({
            "id":          user["id"],
            "username":    user["username"],
            "plant_count": len(plants),
            "created_at":  user["created_at"],
            "last_login":  last_login,
            "active_today": bool(last_login) and last_login[:10] == today,
        })

    # ── headline stats ───────────────────────────────────────────────
    row          = get_daily_unique_logins(today, today)   # [(day, cnt)] or []
    today_logins = row[0][1] if row else 0
    total_plants = sum(u["plant_count"] for u in user_data)

    return render_template(
        "admin_users.html",
        users=user_data,
        today_logins=today_logins,
        total_users=len(user_data),
        total_plants=total_plants,
        lang=g.lang,
        t=get_translations(g.lang),
    )


@app.route("/admin/translations")
@login_required
def admin_translations():
    """Browse and correct the machine-translation cache for user content."""
    if g.user["id"] != 1:
        abort(403)
    query       = (request.args.get("q") or "").strip() or None
    target_lang = request.args.get("lang_filter") or None
    if target_lang not in AVAILABLE_LANGS:
        target_lang = None
    rows = list_cached_translations(query=query, target_lang=target_lang)
    return render_template(
        "admin_translations.html",
        rows=rows,
        query=query or "",
        lang_filter=target_lang or "",
        lang=g.lang,
        t=get_translations(g.lang),
    )


@app.route("/admin/translations/update", methods=["POST"])
@login_required
def admin_translations_update():
    if g.user["id"] != 1:
        abort(403)
    data        = request.get_json(silent=True) or {}
    source_hash = data.get("source_hash")
    target_lang = data.get("target_lang")
    translated  = (data.get("translated") or "").strip()
    if not source_hash or not target_lang or not translated:
        return jsonify({"error": "source_hash, target_lang and translated are required"}), 400
    ok = update_cached_translation(source_hash, target_lang, translated)
    return jsonify({"ok": ok}) if ok else (jsonify({"error": "not found"}), 404)


@app.route("/admin/translations/delete", methods=["POST"])
@login_required
def admin_translations_delete():
    """Drop a cached entry so it is re-translated automatically next time."""
    if g.user["id"] != 1:
        abort(403)
    data        = request.get_json(silent=True) or {}
    source_hash = data.get("source_hash")
    target_lang = data.get("target_lang")
    if not source_hash or not target_lang:
        return jsonify({"error": "source_hash and target_lang are required"}), 400
    ok = delete_cached_translation(source_hash, target_lang)
    return jsonify({"ok": ok}) if ok else (jsonify({"error": "not found"}), 404)

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
            new_id = save_new_plant(form, event, g.user["id"])
            return redirect(url_for("view_plant", idx=new_id))
        # fall‑through: show form with errors
    else:
        form = get_empty_form()
        default_event = request.args.get("default_event")
        if default_event:
            form["status"] = default_event
        errors = []

    _starting_codes = {'order', 'acquire', 'sow', 'soak', 'strat', 'plant'}
    starting_event_specs = [s for s in get_event_specs() if s['code'] in _starting_codes]

    return render_template(
        "add.html",
        lang=lang,
        t=translations,
        form=form,
        errors=errors,
        locations=get_unique_locations(plants),
        today=date.today(),
        starting_event_specs=starting_event_specs,
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
            return redirect(url_for("view_plant", idx=plant["id"]))
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
    return redirect(url_for("index"))


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
                "variety": plant.get("variety") or "",
                "nickname": plant.get("nickname") or "",
                "rusticity": plant.get("rusticity") or "",
                "count": plant.get("count", 1),
            }
            update_plant(plant["id"], plant_data, new_event=event)
            return redirect(url_for("view_plant", idx=plant["id"]))
    else:
        form = get_empty_form()
        form.update({"common": plant["common"], "latin": plant["latin"]})
        default_event = request.args.get("default_event")
        if default_event:
            form["status"] = default_event
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
            return redirect(url_for("view_plant", idx=ev["plant_id"]))
    else:
        form = get_empty_form()
        form["status"] = ev["action"]
        form["common"] = ev["common"]
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
    ev = get_action_by_id(action_id)
    plant_id = ev["plant_id"] if ev else None
    process_delete_action(action_id, g.user["id"])
    if plant_id:
        return redirect(url_for("view_plant", idx=plant_id))
    return redirect(url_for("index"))


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
            # Drop any anonymous temporary override so the account preference wins.
            session.pop("lang_override", None)
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

###############################################################################
# Label / Printer Routes
###############################################################################

def _format_date(iso_date):
    """Convert YYYY-MM-DD to DD-MM-YYYY, or return today if None."""
    if iso_date:
        parts = iso_date.split("-")
        if len(parts) == 3:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return date.today().strftime("%d-%m-%Y")


def _tr_label(text, target_lang, source_lang):
    """Translate a free-text label field into ``target_lang`` (the display
    language chosen via the selector). Returns the original when translation is
    disabled, unneeded, or the field is empty. Never raises."""
    if not text or not target_lang or target_lang == source_lang:
        return text
    if not CONFIG.get("features", {}).get("translate_content", True):
        return text
    return translate_content(text, target_lang, source_lang)


def _make_label_image(plant, style, extra_notes=None, base_url=None,
                      target_lang=None, source_lang=None):
    # Descriptive free-text is translated into the display language; the Latin
    # (scientific) name, variety and nickname are proper names shown verbatim —
    # the same split the plant page uses (common + notes translate, rest don't).
    common      = _tr_label(plant.get("common", ""), target_lang, source_lang)
    latin       = plant.get("latin",  "")
    variety     = plant.get("variety") or None
    nickname    = plant.get("nickname") or None
    location    = _tr_label(plant.get("location"), target_lang, source_lang) or None
    notes       = _tr_label(plant.get("notes"), target_lang, source_lang) or None
    extra_notes = _tr_label(extra_notes, target_lang, source_lang) or None
    history     = plant.get("history") or []
    date_str    = _format_date(history[0]["start"] if history else None)

    if style == "circular":
        return create_label_circular(common, latin, date_str, variety, nickname, extra_notes)
    if style == "minimal":
        return create_label_minimal(common, latin, date_str, variety, nickname, extra_notes)
    if style == "detailed_v":
        return create_label_detailed_v(common, latin, date_str, variety, nickname, location, notes, extra_notes)
    if style == "detailed_h":
        plant_url = (base_url or request.url_root).rstrip("/") + "/p/" + str(plant.get("id", ""))
        return create_label_detailed_h(common, latin, date_str, variety, nickname, location, notes, extra_notes, plant_url=plant_url)
    if style == "qr":
        plant_url = (base_url or request.url_root).rstrip("/") + "/p/" + str(plant.get("id", ""))
        return create_label_qr(common, latin, date_str, plant_url, variety, nickname, extra_notes)
    if style == "stake_wrap":
        return create_label_stake_wrap(common, latin, date_str, variety, nickname, extra_notes)
    return create_label_classic(common, latin, date_str, variety, nickname, extra_notes)


@app.route("/label_preview/<int:idx>")
@login_required
def label_preview(idx):
    plant = load_one(idx) or abort(404)
    if plant.get("user_id") != g.user["id"]:
        abort(403)
    style       = request.args.get("style", "classic")
    extra_notes = request.args.get("extra") or None
    base_url    = request.args.get("base_url") or None
    img = _make_label_image(plant, style, extra_notes, base_url=base_url,
                            target_lang=g.lang, source_lang=g.content_lang)
    # Rotate the preview into reading orientation for sideways-printed labels.
    # The bytes sent to the printer are unchanged; this only affects the preview.
    if style in ("detailed_h", "stake_wrap"):
        img = img.transpose(PIL.Image.ROTATE_90)
    # The preview depends on the session display language, so it must never be
    # served from the browser cache after a language switch.
    return Response(label_to_png_bytes(img), mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


@app.route("/print_label/<int:idx>", methods=["POST"])
@login_required
def print_label_route(idx):
    """Queue a print job — label_client.py on the user's machine picks it up."""
    plant = load_one(idx) or abort(404)
    if plant.get("user_id") != g.user["id"]:
        abort(403)
    data        = request.get_json(silent=True) or {}
    style       = data.get("style", "classic")
    extra_notes = data.get("extra_notes") or None
    base_url    = data.get("base_url") or None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO print_jobs (user_id, plant_id, style, extra_notes, base_url, lang) VALUES (?,?,?,?,?,?)",
            (g.user["id"], idx, style, extra_notes, base_url, g.lang),
        )
        job_id = cur.lastrowid
        conn.commit()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/freetext_label", methods=["GET"])
@login_required
def freetext_label():
    return render_template(
        "freetext_label.html",
        lang=g.lang,
        t=get_translations(g.lang),
    )


@app.route("/freetext_label/preview")
@login_required
def freetext_label_preview():
    title    = request.args.get("title", "")
    subtitle = request.args.get("subtitle") or None
    body     = request.args.get("body") or None
    img = create_label_freetext(title, subtitle, body)
    return Response(label_to_png_bytes(img), mimetype="image/png")


@app.route("/freetext_label/print", methods=["POST"])
@login_required
def freetext_label_print():
    data     = request.get_json(silent=True) or {}
    title    = (data.get("title") or "").strip()
    subtitle = (data.get("subtitle") or "").strip() or None
    body     = (data.get("body") or "").strip() or None
    if not title:
        return jsonify({"error": "title required"}), 400
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO print_jobs (user_id, kind, style, title, subtitle, body)
               VALUES (?, 'freetext', 'freetext', ?, ?, ?)""",
            (g.user["id"], title, subtitle, body),
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
    plant_data = {k: plant.get(k, "") or "" for k in ("common", "latin", "location", "notes", "variety", "nickname", "rusticity")}
    update_plant(plant["id"], plant_data, new_event=event)
    return jsonify({"ok": True})


@app.route("/end_phase/<int:idx>", methods=["POST"])
@login_required
def end_phase(idx):
    """Set ended_on = today on the most recent flower/fruit event."""
    plant = load_one(idx)
    if plant is None or plant.get("user_id") != g.user["id"]:
        abort(403)
    data = request.get_json(silent=True) or {}
    ended = data.get("date") or date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT e.id, et.code FROM events e
               JOIN event_types et ON et.id = e.event_type_id
               WHERE e.plant_id = ? AND et.code IN ('flower','fruit')
               ORDER BY e.happened_on DESC, e.id DESC LIMIT 1""",
            (idx,),
        ).fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "No flower/fruit event found"}), 400
        conn.execute("UPDATE events SET ended_on = ? WHERE id = ?", (ended, row["id"]))
        conn.commit()
    return jsonify({"ok": True})


###############################################################################
# Clone Plant Route
###############################################################################

@app.route("/duplicate_plant/<int:idx>", methods=["POST"])
@login_required_for_plant
def duplicate_plant_route(idx):
    """Create one or more full copies of a plant and go to the result."""
    count   = max(1, min(int(request.form.get("count", 1) or 1), 50))
    new_ids = duplicate_plant(idx, g.user["id"], count)
    # A single copy lands on the new plant; several land back on the dashboard.
    if len(new_ids) == 1:
        return redirect(url_for("view_plant", idx=new_ids[0]))
    return redirect(url_for("index"))


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


@app.route("/download/label_client.exe")
@login_required
def download_label_client_exe():
    path = os.path.join(os.path.dirname(__file__), "dist", "plantlog-label-client.exe")
    if not os.path.exists(path):
        return ("Windows build not available", 404)
    return Response(
        open(path, "rb").read(),
        mimetype="application/vnd.microsoft.portable-executable",
        headers={"Content-Disposition": "attachment; filename=plantlog-label-client.exe"},
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
    return redirect(url_for("settings"))


@app.route("/settings/revoke_key", methods=["POST"])
@login_required
def settings_revoke_key():
    revoke_api_key(g.user["id"])
    flash(get_translations(g.lang).get("settings_key_revoked", "API key revoked."), "info")
    return redirect(url_for("settings"))


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
    return redirect(url_for("settings"))


@app.route("/settings/language", methods=["POST"])
@login_required
def settings_language():
    """Change the account's permanent language preference."""
    lang = request.form.get("lang")
    if lang in AVAILABLE_LANGS:
        update_user_lang(g.user["id"], lang)
        g.user = get_user_by_id(g.user["id"])
        # A permanent choice supersedes any temporary ?lang override.
        session.pop("lang_override", None)
        session["lang"] = lang
        g.lang = lang
        flash(get_translations(lang).get("settings_lang_saved", "Language updated."), "success")
    return redirect(url_for("settings"))


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
        "rusticity":         data.get("rusticity", ""),
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
        "rusticity": data.get("rusticity", p.get("rusticity") or ""),
        "count":     data.get("count",     p.get("count", 1)),
    }
    update_plant(idx, plant_data, new_event=None)
    return jsonify({"ok": True})


@app.route("/api/plants/<int:idx>/duplicate", methods=["POST"])
@_api_auth_required
def api_duplicate_plant(idx):
    p = load_one(idx)
    if p is None or p.get("user_id") != g.api_user["id"]:
        return jsonify({"error": "Not found"}), 404
    data  = request.get_json(silent=True) or {}
    count = max(1, int(data.get("count", 1)))
    ids   = duplicate_plant(idx, g.api_user["id"], count)
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


@app.route("/api/me", methods=["GET"])
@_api_auth_required
def api_me():
    """Return the authenticated user (used by the label client on startup)."""
    u = g.api_user
    return jsonify({"id": u["id"], "username": u["username"], "lang": u["lang"]})


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
            """SELECT j.id, j.style, j.kind, j.title, j.created_at,
                      p.common
               FROM print_jobs j
               LEFT JOIN plants p ON p.id = j.plant_id
               WHERE j.user_id = ? AND j.status = 'pending'
               ORDER BY j.created_at ASC""",
            (g.api_user["id"],),
        ).fetchall()
    return jsonify([
        {
            "job_id":     r["id"],
            "style":      r["style"],
            "created_at": r["created_at"],
            "plant": {"common": r["common"] if r["kind"] == "plant" else (r["title"] or "Free-text label")},
        }
        for r in rows
    ])


@app.route("/api/print_queue/<int:job_id>/bytes", methods=["GET"])
@_api_auth_required
def api_print_job_bytes(job_id):
    """Render the label and return raw ESC/POS bytes for the printer."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT j.style, j.kind, j.extra_notes, j.base_url, j.lang,
                      j.title, j.subtitle, j.body,
                      p.id AS plant_id,
                      p.common, p.latin, p.variety, p.nickname, p.location, p.notes,
                      COALESCE(
                        (SELECT MIN(e.happened_on) FROM events e
                         JOIN event_types et ON et.id = e.event_type_id
                         WHERE e.plant_id = p.id AND et.code IN ('sow','plant')),
                        (SELECT MIN(e.happened_on) FROM events e WHERE e.plant_id = p.id)
                      ) AS earliest_date
               FROM print_jobs j
               LEFT JOIN plants p ON p.id = j.plant_id
               WHERE j.id = ? AND j.user_id = ?""",
            (job_id, g.api_user["id"]),
        ).fetchone()
    if not row:
        abort(404)
    if row["kind"] == "freetext":
        img = create_label_freetext(row["title"], row["subtitle"], row["body"])
        return Response(label_to_printer_bytes(img), mimetype="application/octet-stream")
    date_str    = _format_date(row["earliest_date"])
    # Translate descriptive fields into the language chosen when the job was
    # queued. Source is the plant owner's account language. Latin name, variety
    # and nickname stay verbatim — see _make_label_image.
    target_lang = row["lang"]
    source_lang = g.api_user["lang"]
    common      = _tr_label(row["common"], target_lang, source_lang)
    variety     = row["variety"]  or None
    nickname    = row["nickname"] or None
    location    = _tr_label(row["location"], target_lang, source_lang) or None
    notes       = _tr_label(row["notes"], target_lang, source_lang) or None
    extra_notes = _tr_label(row["extra_notes"], target_lang, source_lang) or None
    style       = row["style"]
    latin       = row["latin"]

    if style == "circular":
        img = create_label_circular(common, latin, date_str, variety, nickname, extra_notes)
    elif style == "minimal":
        img = create_label_minimal(common, latin, date_str, variety, nickname, extra_notes)
    elif style == "detailed_v":
        img = create_label_detailed_v(common, latin, date_str, variety, nickname, location, notes, extra_notes)
    elif style == "detailed_h":
        base_url  = (row["base_url"] or request.url_root).rstrip("/")
        plant_url = base_url + "/p/" + str(row["plant_id"])
        img = create_label_detailed_h(common, latin, date_str, variety, nickname, location, notes, extra_notes, plant_url=plant_url)
    elif style == "qr":
        base_url  = (row["base_url"] or request.url_root).rstrip("/")
        plant_url = base_url + "/p/" + str(row["plant_id"])
        img = create_label_qr(common, latin, date_str, plant_url, variety, nickname, extra_notes)
    elif style == "stake_wrap":
        img = create_label_stake_wrap(common, latin, date_str, variety, nickname, extra_notes)
    else:
        img = create_label_classic(common, latin, date_str, variety, nickname, extra_notes)
    return Response(label_to_printer_bytes(img), mimetype="application/octet-stream")


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
