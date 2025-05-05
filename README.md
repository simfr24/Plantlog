# 🌱 Plantlog – Responsive Seed / Plant Tracker

**Plantlog** is a lightweight and mobile-first web app for tracking plant propagation stages — from soaking and stratification to sowing and sprouting. It features a clear timeline view, language switching (English / French), and a Bootstrap 5.3 interface with modern UX elements like off-canvas forms and icon-based stage selection.

---

## ✨ Features

- 📱 **Mobile‑first design** using Bootstrap 5.3
- 🌐 **Multilingual UI** – English and French (toggle via dropdown)
- 🪴 Track propagation stages: **Soak**, **Stratify**, **Sow**, **Sprout**
- 🧾 **Collapsible plant timeline** with icons and status badges
- 🖊️ **Off‑canvas form** for adding or editing plant entries
- 📂 **Data stored in JSON** – easy to back up, export, or modify

---

## 🚀 Getting Started

### 1. Requirements

- Python 3.8+
- Flask (or any WSGI-compatible web server)

### 2. Installation

Clone the repo:

```bash
git clone https://github.com/yourusername/plantlog.git
cd plantlog
```

Install dependencies:

```bash
pip install flask
```

Run the Flask app:

```bash
python app.py
```

Then visit:

```
http://localhost:5000
```

---

## 📝 Data Format

Each plant and its propagation history are stored in a SQLite database with the following structure:

### Plants Table
- **`id`**: Unique identifier for each plant (primary key).
- **`common`**: Common name of the plant (e.g., "Tomato").
- **`latin`**: Latin name of the plant (e.g., "Solanum lycopersicum").
- **`location`**: Optional field to specify where the plant is located (e.g., "Greenhouse").
- **`notes`**: Additional notes about the plant.

### Actions Table
- **`id`**: Unique identifier for each action (primary key).
- **`plant_id`**: Foreign key linking the action to a specific plant.
- **`action`**: Type of action (e.g., "sow", "sprout").
- **`start`**: Date the action starts (formatted as `YYYY-MM-DD`).
- **`range_min` / `range_max`**: Optional range values for actions like sowing (e.g., germination time).
- **`range_min_u` / `range_max_u`**: Units for the range values (e.g., "days").
- **`dur_val` / `dur_unit`**: Optional duration values for actions like soaking (e.g., "24 hours").

This schema ensures data integrity and allows for easy tracking of propagation stages.

---

## 🌍 Language Switching

* Use the dropdown in the navbar
* Or add `?lang=fr` or `?lang=en` to the URL to switch manually

---

## 🔧 Customization Tips

* Want to add more stages? Modify the `STAGES` dictionary and form rendering logic.
* Adjust timeline style via custom CSS in the `static/` folder.
* Add translations in the `translations` dictionary for new languages.

---

## 📖 License

MIT — free to use, modify, and share.

---

## 🤝 Acknowledgments

* Built with ❤️ using [Flask](https://flask.palletsprojects.com/) and [Bootstrap 5.3](https://getbootstrap.com/)
* Icons by [Font Awesome](https://fontawesome.com/)