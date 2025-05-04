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

- PHP 7.4+
- Web server (e.g., Apache, Nginx, or PHP’s built-in server)

### 2. Installation

Clone the repo:

```bash
git clone https://github.com/yourusername/plantlog.git
cd plantlog
````

Start a PHP server (for local use):

```bash
php -S localhost:8000
```

Then visit:

```
http://localhost:8000
```

---

## 🗂️ Project Structure

```
plantlog/
├── index.php  # Main app file
├── plants.json                 # Your saved plants data, will be created on first save
├── README.md
├── favicon.ico
```

---

## 📝 Data Format

Each plant is saved in `plants.json` with this structure:

```json
{
  "common": "Tomato",
  "latin": "Solanum lycopersicum",
  "location": "Greenhouse",
  "notes": "Started indoors",
  "history": [
    {
      "action": "sow",
      "start": "2025-03-17",
      "range": [7, "days", 14, "days"]
    },
    {
      "action": "sprout",
      "start": "2025-03-28"
    }
  ]
}
```

---

## 🌍 Language Switching

* Use the dropdown in the navbar
* Or add `?lang=fr` or `?lang=en` to the URL to switch manually

---

## 🔧 Customization Tips

* Want to add more stages? Modify the `$icon` map and form rendering.
* Adjust timeline style via embedded CSS or your own stylesheet.
* Add translations in the `$T` array for new languages.

---

## 📖 License

MIT — free to use, modify, and share.

---

## 🤝 Acknowledgments

* Built with ❤️ using [Bootstrap 5.3](https://getbootstrap.com/)
* Icons by [Font Awesome](https://fontawesome.com/)