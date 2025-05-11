# -*- coding: utf-8 -*-
"""
English UI strings for Plantlog
"""
translations = {
    # ────────── navigation / layout ──────────
    "Plant Tracker": "Plant Tracker",
    "My plants": "My plants",
    "{name}'s plants": "{name}'s plants",
    "Add plant": "Add plant",
    "Add stage": "Add stage",
    "Add stage for": "Add stage for",
    "Edit plant": "Edit plant",
    "Edit stage": "Edit stage",
    "Edit stage for": "Edit stage for",
    "Save": "Save",
    "Save changes": "Save changes",
    "Language": "Language",
    "Log out": "Log out",

    # ────────── plant & stage forms ──────────
    "Common name": "Common name",
    "Latin name": "Latin name",
    "Location": "Location",
    "Notes": "Notes",
    "Current stage": "Current stage",
    "e.g. Tomato": "e.g. Tomato",
    "e.g. Latin name": "e.g. Solanum lycopersicum",
    "e.g. Greenhouse": "e.g. Greenhouse",
    "e.g. Notes": "e.g. Germinates quickly, likes warmth",

    # stage-specific inputs
    "Event date": "Event date",
    "Est. sprout time (min)": "Est. sprout time (min)",
    "Est. sprout time (max)": "Est. sprout time (max)",
    "Duration": "Duration",
    "Size": "Size",

    # ────────── units ──────────
    "hours": "hours",
    "days": "days",
    "day": "day",
    "week": "week",
    "weeks": "weeks",
    "month": "month",
    "months": "months",
    "years": "years",
    "year": "year",

    # ────────── timeline / badges ──────────
    "Overdue": "Overdue",
    "Anytime soon": "Anytime soon",
    "left": "left",
    "Done": "Done",
    "Expected sprout time": "Expected sprout time",
    "Sprouted on": "Sprouted on",
    "Sprouted in": "Sprouted in",
    "No plants yet": "No plants yet",
    "Confirm delete plant": "Are you sure you want to delete this plant?",
    "Delete plant": "Delete plant",
    "Confirm delete stage": "Are you sure you want to delete this stage?",
    "Delete stage": "Delete stage",

    # ────────── authentication ──────────
    "login_title": "Login",
    "login_heading": "Log in to your account",
    "username_label": "Username",
    "password_label": "Password",
    "login_button": "Log in",
    "create_account_link": "Create an account",
    "register": "Register",
    "username": "Username",
    "password": "Password",
    "preferred_language": "Preferred language",
    "create_account": "Create account",
    "i_have_an_account": "I already have an account",

    # ────────── core event labels ──────────
    "Sow": "Sow",
    "Soak": "Soak",
    "Strat": "Strat",
    "Sprout": "Sprout",
    "Flower": "Flower",
    "Fruit": "Fruit",
    "Death": "Death",
    "Measurement": "Measurement",
    "Plant": "Plant",

    # ────────── human-readable summaries ──────────
    "Sowing started on": "Sowing started on",
    "Soaking since": "Soaking since",
    "Stratification started on": "Stratification started on",
    "Germinated on": "Germinated on",
    "Flowering since": "Flowering since",
    "Fruiting since": "Fruiting since",
    "Died on": "Died on",
    "Measurement taken on": "Measurement taken on",
    "Planted on": "Planted on",
    "Age": "Age",

    # ────────── Custom events ──────────
    "{event} on": "{event} on",
    "Event title": "Event title",
    "Note content": "Details",
    "Custom Event": "Custom Event",

    # ────────── core state labels ──────────
    "Sown": "Sown",
    "Soaking": "Soaking",
    "Stratifying": "Stratifying",
    "Growing": "Growing",
    "Flowering": "Flowering",
    "Fruiting": "Fruiting",
    "Dead": "Dead",
    "Planted": "Planté",

    # ────────── backend flash messages (login / register) ──────────
    "Username & password required": "Username & password required",
    "Username already taken": "Username already taken",
    "Account created – you can now log in": "Account created – you can now log in",
    "Logged in": "Logged in",
    "Logged out": "Logged out",
    "Bad credentials": "Bad credentials",
    "You must be logged in to access this page.": "You must be logged in to access this page.",
    "Size must be > 0.": "Size must be > 0.",

    # ─────────────── admin ───────────────
    "Logins today": "Logins today",
    "ID": "ID",
    "Username": "Username",
    "Plants": "Plants",
    "Public page": "Public page",
    "Created": "Created",
    "Last login": "Last login",

    # ─────────────── confirm modals ───────────────
    "Confirm delete plant": "Confirm delete plant",
    "Are you sure you want to delete this plant?": "Are you sure you want to delete this plant?",
    "Confirm delete stage": "Confirm delete stage",
    "Are you sure you want to delete this stage?": "Are you sure you want to delete this stage?",
    "Delete": "Delete",
    "Cancel": "Cancel",
    "Close": "Close",

    # ─────────────── help page ───────────────
    "Help": "Help",
    "Overview": "Overview",
    
    # Help – enriched content
    "Help overview detailed": "Welcome to Plantlog! This app helps you track your plants’ development step by step. You can log key events, view state changes, and maintain a detailed history for each plant.",

    # Plant states
    "Help state Sown detailed": "The 'Sown' state means the seed has been placed in a substrate like soil or moist cotton in hopes it will germinate.",
    "Help state Soaking detailed": "Soaking is often used to soften hard seed coats before sowing. It can last from a few hours to several days.",
    "Help state Stratifying detailed": "Cold stratification mimics winter: some seeds need a period of cold and moisture to break dormancy. This is often done in a refrigerator.",
    "Help state Growing detailed": "Once the seed has germinated and a seedling begins to grow, the plant enters the growth stage, producing its first true leaves.",
    "Help state Flowering detailed": "The plant has started flowering. This is a key stage for species grown for their flowers or fruit.",
    "Help state Fruiting detailed": "The plant is bearing fruit, indicating successful pollination after flowering.",
    "Help state Dead detailed": "The 'Dead' state means the plant has died or no growth has been observed for a long time.",

    # Plant events
    "Help event Sow detailed": "Log the moment you sow a seed. You can also estimate how long it will take to germinate (minimum and maximum duration).",
    "Help event Soak detailed": "Record the start of soaking. This is useful for species that germinate better after prolonged contact with water.",
    "Help event Strat detailed": "Indicate when stratification begins. You can specify the expected duration (e.g. 6 weeks at 4°C).",
    "Help event Plant detailed": "Use this event to note when a seedling or cutting is planted into a pot or the ground.",
    "Help event Sprout detailed": "Note when germination is observed—when the root or shoot emerges.",
    "Help event Flower detailed": "Log when the first visible flowers appear.",
    "Help event Fruit detailed": "Add an event when fruits start forming on the plant.",
    "Help event Measurement detailed": "Used to log the current size of the plant (in cm, mm, or m).",
    "Help event Custom Event detailed": "A free-form event you can name and describe, such as 'Repotting', 'Disease', etc.",
    "Help event Death detailed": "Marks that the plant has died (wilted, rotted, dried out…).",

    # State transitions
    "Help transitions description detailed": "Each event can cause a change in the plant's state. Here's how it works:",
    "Help transition Sow detailed": "After sowing, the plant enters the 'Sown' state.",
    "Help transition Soak detailed": "Soaking puts the seed into the 'Soaking' state.",
    "Help transition Strat detailed": "Stratification sets the seed to the 'Stratifying' state.",
    "Help transition Plant detailed": "The 'Plant' event sets the plant to 'Growing'.",
    "Help transition Sprout detailed": "Germination moves the plant into the 'Growing' state.",
    "Help transition Flower detailed": "The start of flowering is recorded, the plant remains in the 'Growing' state.",
    "Help transition Fruit detailed": "The appearance of fruits is noted, the plant stays in the 'Growing' state.",
    "Help transition Measurement detailed": "A measurement doesn’t change the plant’s state.",
    "Help transition Custom Event detailed": "Custom events do not change the state.",
    "Help transition Death detailed": "The plant transitions to the 'Dead' state.",

    "Plant States": "Plant States",
    "Plant Events": "Plant Events",
    "How States Change": "How States Change",

    # Footer
    "Back to dashboard": "Back to Dashboard",
    
}
