from flask import Flask, render_template, request, redirect, url_for, session, abort, g, flash
import os
from datetime import date
from werkzeug.security import check_password_hash
from functools import wraps


from py.users import (
    create_user, login_user, logout_user,
    get_user_by_username, get_user_by_id, update_user_lang
)
from py.helpers import (
    load_data, load_one, get_translations, duration_to_days, dateadd,
    get_empty_form, get_form_data, get_form_from_plant, validate_form,
    save_new_plant, update_plant, process_delete_plant, process_delete_action,
    get_action_by_id, update_action, form_keys_for
)
from py.db import init_db
from py.processing import sort_key, get_unique_locations


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'plantlog-secret-key')
app.template_filter("dateadd")(dateadd)
init_db()


AVAILABLE_LANGS = ['en', 'fr']

@app.template_filter("todate")
def todate(value):
    return date.fromisoformat(value) if isinstance(value, str) else value

@app.before_request
def load_logged_in_user_and_language():
    """Load g.user (if any) **and** work out what lang should be."""
    uid = session.get("uid")
    g.user = get_user_by_id(uid) if uid else None

    # 1️⃣ explicit ?lang overrides & persists when logged‑in
    lang_param = request.args.get("lang")
    if lang_param in AVAILABLE_LANGS:
        if g.user:
            update_user_lang(g.user["id"], lang_param)
            g.user = get_user_by_id(g.user["id"])    # refresh row
        session["tmp_lang"] = lang_param             # for anonymous use
    # 2️⃣ authoritatively decide the language for this request
    if g.user:
        g.lang = g.user["lang"]
    elif "tmp_lang" in session:
        g.lang = session["tmp_lang"]
    else:
        g.lang = "en"

def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("You must be logged in to access this page.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(**kwargs)
    return wrapped_view

@app.route('/')
@login_required
def index():
    lang = g.lang
    translations = get_translations(lang)

    try:
        plants = load_data(g.user['id'])
    except Exception as e:
        return f"<pre style='color:red'>{str(e)}</pre>"

    sorted_plants = sorted(plants, key=sort_key)

    # Precompute extra info per plant if needed
    today = date.today()

    return render_template(
        'index.html',
        lang=lang,
        t=translations,
        plants=sorted_plants,
        today=today,
        duration_to_days=duration_to_days  # ← passed manually to template context
    )

@app.route('/favicon.ico', methods=['GET'])
def favicon():
    return app.send_static_file('favicon.ico')

@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_plant():
    lang = g.lang
    translations = get_translations(lang)
    plants = load_data(g.user['id'])

    if request.method == 'POST':
        form = get_form_data(request)
        errors, action = validate_form(form, translations)

        if not errors:
            save_new_plant(form, action, g.user['id'])
            return redirect(url_for('index', lang=lang))

        return render_template('add.html',
                               lang=lang,
                               t=translations,
                               form=form,
                               errors=errors,
                               locations=get_unique_locations(plants),
                               today=date.today())

    form = get_empty_form()
    return render_template('add.html',
                           lang=lang,
                           t=translations,
                           form=form,
                           errors=[],
                           locations=get_unique_locations(plants),
                           today=date.today())


@app.route('/edit_plant/<int:idx>', methods=['GET','POST'])
@login_required
def edit_plant(idx):
    # Load exactly one plant by its ID
    plant = load_one(idx)
    if plant is None:
        abort(404)

    # Determine language and translations
    lang = session.get('lang', 'en')
    translations = get_translations(lang)

    errors = []
    # Handle form submission
    if request.method == 'POST':
        form = get_form_data(request)
        errors, _ = validate_form(form, translations)
        if not errors:
            # Update plant metadata only (no new action)
            update_plant(plant['id'], form, new_action=None)
            return redirect(url_for('index', lang=lang))
    else:
        # Pre-populate form from existing plant data
        form = get_form_from_plant(plant)

    # Render template, passing necessary context
    return render_template(
        'edit_plant.html',
        form=form,
        errors=errors,
        edit_idx=idx,
        lang=lang,
        t=translations
    )


@app.route('/add_stage/<int:idx>', methods=['GET', 'POST'])
@login_required
def add_stage(idx):
    plant = load_one(idx)
    if plant is None:
        abort(404)
    errors = {}
    lang = request.args.get('lang', 'en')
    translations = get_translations(lang)

    if request.method == 'POST':
        form = get_form_data(request)
        errors, action = validate_form(form, translations)
        if not errors:
            # build only the plant metadata dict for update_plant
            plant_data = {
                'common':  plant['common'],
                'latin':   plant['latin'],
                'location': plant['location'],
                'notes':   plant['notes']
            }
            # new_action causes insert of one action row
            update_plant(plant['id'], plant_data, new_action=action)
            return redirect(url_for('index', lang=lang))
    else:
        # show only the stage fields; metadata is read-only if shown
        form = get_empty_form()
        form.update({
            'common':  plant['common'],
            'latin':   plant['latin']
        })

    return render_template(
        'add_stage.html',
        form=form,
        errors=errors,
        plant_idx=idx,
        lang=lang,
        t=translations 
    )

@app.route('/edit_stage/<int:action_id>', methods=['GET', 'POST'])
@login_required
def edit_stage(action_id):
    # Fetch the single action and its parent plant if needed
    a = get_action_by_id(action_id)
    if a is None:
        abort(404)

    errors = []
    lang = request.args.get('lang', session.get('lang', 'en'))
    translations = get_translations(lang)

    if request.method == 'POST':
        form = get_form_data(request)
        errors, action = validate_form(form, translations)
        if not errors:
            update_action(action_id, action)
            return redirect(url_for('index', lang=lang))
    else:
        # Pre-populate form with the existing action’s data
        form = get_empty_form()
        form['status'] = a['action']
        # Pass the full action dict, not just the action string
        date_field, extras = form_keys_for(a)
        form[date_field] = a['start']
        for field, val in extras.items():
            form[field] = val

    return render_template(
        'edit_stage.html',
        form=form,
        errors=errors,
        action_id=action_id,
        lang=lang,
        t=translations
    )

    return render_template(
        'edit_stage.html',
        form=form,
        errors=errors,
        action_id=action_id,
        lang=lang
    )

@app.route('/delete_plant/<int:idx>', methods=['GET'])
@login_required
def delete_plant(idx):
    process_delete_plant(idx, g.user['id'])
    return redirect(url_for('index', lang=request.args.get('lang','en')))

@app.route('/delete_stage/<int:action_id>', methods=['GET'])
@login_required
def delete_stage(action_id):
    process_delete_action(action_id, g.user['id'])
    return redirect(url_for('index', lang=request.args.get('lang','en')))

@app.route('/register', methods=['GET', 'POST'])
def register():
    t = get_translations(g.lang)
    if request.method == 'POST':
        username = request.form['username'].strip()
        pw = request.form['password']
        if not username or not pw:
            flash(t['Username & password required'], 'danger')
        elif get_user_by_username(username):
            flash(t['Username already taken'], 'danger')
        else:
            create_user(username, pw, request.form.get('lang', 'en'))
            flash(t['Account created – you can now log in'], 'success')
            return redirect(url_for('login'))
    return render_template('register.html', lang=g.lang, t=t)


@app.route('/login', methods=['GET', 'POST'])
def login():
    t = get_translations(g.lang)
    next_page = request.args.get('next', url_for('index'))
    if request.method == 'POST':
        user = get_user_by_username(request.form['username'])
        if user and check_password_hash(user['pw_hash'], request.form['password']):
            login_user(user)
            return redirect(next_page)
        flash(t['Bad credentials'], 'danger')
    return render_template('login.html', lang=g.lang, t=t)


@app.route('/logout')
def logout():
    t = get_translations(g.lang)
    logout_user()
    flash(t['Logged out'], 'info')
    return redirect(url_for('login'))



if __name__ == '__main__':
    app.run(debug=True)
