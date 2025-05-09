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
    get_event_specs
)
from py.processing import sort_key, get_unique_locations

###############################################################################
# Flask app setup
###############################################################################

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "plantlog-secret-key")
app.template_filter("dateadd")(dateadd)
init_db()

@app.context_processor
def inject_event_specs():
    # event_specs -> list[dict]   event_map -> dict[code -> dict]
    specs = get_event_specs()
    return dict(event_specs=specs,
                event_map={s["code"]: s for s in specs})

AVAILABLE_LANGS = ["en", "fr", "ru"]


@app.template_filter("todate")
def todate(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


###############################################################################
# Request/Session helpers
###############################################################################

@app.before_request
def load_logged_in_user_and_language():
    uid = session.get("uid")
    g.user = get_user_by_id(uid) if uid else None

    lang_param = request.args.get("lang")
    if lang_param in AVAILABLE_LANGS:
        if g.user:
            update_user_lang(g.user["id"], lang_param)
            g.user = get_user_by_id(g.user["id"])  # refresh
        session["tmp_lang"] = lang_param

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


###############################################################################
# Routes – dashboard & list views
###############################################################################

@app.route("/")
@login_required
def index():
    lang = g.lang
    translations = get_translations(lang)

    try:
        plants = load_data(g.user["id"])
    except Exception as e:  # pragma: no cover – debug / sanity guard
        return f"<pre style='color:red'>{e!s}</pre>"

    return render_template(
        "index.html",
        lang=lang,
        t=translations,
        plants=sorted(plants, key=sort_key),
        today=date.today(),
        duration_to_days=duration_to_days,
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
@login_required
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
@login_required
def delete_plant(idx):
    process_delete_plant(idx, g.user["id"])
    return redirect(url_for("index", lang=g.lang))


###############################################################################
# Routes – event (stage) CRUD
###############################################################################

@app.route("/add_stage/<int:idx>", methods=["GET", "POST"])
@login_required
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
@login_required
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
@login_required
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


###############################################################################
# Static helpers
###############################################################################

@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("favicon.ico")


###############################################################################
# Main entry point
###############################################################################

if __name__ == "__main__":
    app.run(debug=True)
