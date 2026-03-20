# Smart Expense Tracker

This project now includes two versions:

- `app.py`: the existing desktop `tkinter` version
- `web_app.py`: a new mobile-friendly responsive web version built with Flask

Both versions use the same SQLite database file: `expenses.db`.

## Features

- Add and delete expenses
- Multiple users with separate spending history
- Mobile-friendly responsive layout for phones and tablets
- Filters like `Today`, `Last 4 Days`, `Last 7 Days`, `Last 30 Days`, and custom range
- Daily spending bars and category breakdown cards
- Local SQLite persistence

## Run The Mobile-Friendly Web App

```powershell
python -m pip install -r requirements.txt
python web_app.py
```

Then open:

```text
http://127.0.0.1:5000
```

## Run The Old Desktop App

```powershell
python app.py
```

## Notes

- Dates must use `YYYY-MM-DD`
- Existing old records are automatically assigned to `User 1`
- The web app is the better path if you want mobile support and future free hosting

## Free Hosting On Render

This project is now prepared for free Render deployment with `render.yaml`.

### Steps

1. Push this project to GitHub
2. Create a free account on Render
3. Choose `New +` -> `Blueprint`
4. Select your GitHub repo
5. Render will detect `render.yaml` and create the web service
6. After deploy, open the generated `onrender.com` URL

### Important Free Hosting Note

- Render free web services spin down after 15 minutes of inactivity
- Render free web services use an ephemeral filesystem, so local SQLite data can be lost on restart or redeploy
- For real persistent hosted data, move from local `expenses.db` to a hosted database later
