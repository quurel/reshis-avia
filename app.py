import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from forecast import ForecastData, InputError
from models import Attempt, Base, User

ROOT = Path(__file__).resolve().parent


def create_app(config=None):
    load_dotenv(ROOT / ".env")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        app.config.update(config)
    key = app.config["SECRET_KEY"]
    if not key or len(key) < 32 or key.startswith("replace_"):
        raise RuntimeError("Задайте случайный SECRET_KEY длиной не менее 32 символов в .env")
    engine = app.config.get("DB_ENGINE")
    if engine is None:
        db_url = URL.create(
            "mysql+pymysql", username=os.getenv("DB_USER", "reshis_user"),
            password=os.getenv("DB_PASSWORD", ""), host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "3306")), database=os.getenv("DB_NAME", "reshis_avia"),
            query={"charset": "utf8mb4"},
        )
        engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=280,
                               connect_args={"connect_timeout": 5})
    app.extensions["db_engine"] = engine
    data = ForecastData(app.config.get("DATA_FOLDER", ROOT / "data"))
    app.extensions["forecast"] = data

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def prepare_request():
        g.user = None
        if request.endpoint == "static":
            return
        g.db = Session(engine)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            if not supplied or not hmac.compare_digest(session.get("csrf", "").encode(), supplied.encode()):
                if request.path.startswith("/api/"):
                    return jsonify(error="Сессия страницы устарела. Обновите страницу и попробуйте снова."), 400
                return render_template("error.html", title="Обновите страницу",
                                       message="Сессия страницы устарела. Обновите форму и попробуйте снова."), 400
        uid = session.get("user_id")
        if isinstance(uid, int):
            g.user = g.db.get(User, uid)
            if g.user is None:
                session.clear()

    @app.teardown_request
    def close_db(error=None):
        db = g.pop("db", None)
        if db:
            db.close()

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    def limited(action, identity, limit):
        hashed = hashlib.sha256(identity.encode()).hexdigest()
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=15)
        g.db.execute(delete(Attempt).where(Attempt.created_at < cutoff))
        count = g.db.scalar(select(func.count()).select_from(Attempt).where(
            Attempt.action == action, Attempt.key == hashed, Attempt.created_at >= cutoff))
        if count >= limit:
            g.db.commit()
            return True
        g.db.add(Attempt(action=action, key=hashed))
        g.db.commit()
        return False

    @app.get("/")
    def index():
        return render_template("index.html", catalog=data.catalog())

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("index"))
        error, values = None, {"name": "", "email": ""}
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().casefold()
            password = request.form.get("password", "")
            values = {"name": name, "email": email}
            if not 1 <= len(name) <= 60:
                error = "Укажите имя длиной от 1 до 60 символов."
            elif len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                error = "Проверьте адрес электронной почты."
            elif not 8 <= len(password) <= 128:
                error = "Пароль должен содержать от 8 до 128 символов."
            elif limited("register", request.remote_addr or "unknown", 10):
                error = "Слишком много попыток. Повторите через 15 минут."
            else:
                user = User(name=name, email=email,
                            password_hash=generate_password_hash(password, method="pbkdf2:sha256:600000"))
                g.db.add(user)
                try:
                    g.db.commit()
                except IntegrityError:
                    g.db.rollback()
                    error = "Эта почта уже зарегистрирована. Войдите в аккаунт."
                else:
                    session.clear()
                    session["user_id"] = user.id
                    session.permanent = True
                    return redirect(url_for("index"))
        return render_template("auth.html", mode="register", error=error, values=values), (400 if error else 200)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("index"))
        error, values = None, {"email": ""}
        if request.method == "POST":
            email = request.form.get("email", "").strip().casefold()
            password = request.form.get("password", "")
            values["email"] = email
            if len(email) > 254 or len(password) > 128:
                error = "Неверная почта или пароль."
            elif limited("login", email, 8) or limited("login_ip", request.remote_addr or "unknown", 40):
                error = "Слишком много попыток. Повторите через 15 минут."
            else:
                user = g.db.scalar(select(User).where(User.email == email))
                if user is None or not check_password_hash(user.password_hash, password):
                    error = "Неверная почта или пароль."
                else:
                    session.clear()
                    session["user_id"] = user.id
                    session.permanent = True
                    return redirect(url_for("index"))
        return render_template("auth.html", mode="login", error=error, values=values), (400 if error else 200)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.post("/api/forecast")
    def forecast():
        if g.user is None:
            return jsonify(error="Войдите, чтобы рассчитать прогноз.", login_url=url_for("login")), 401
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Не удалось прочитать параметры расчёта."), 400
        try:
            if payload.get("origin", "MOW") != "MOW":
                raise InputError("В прогнозе доступны только вылеты из Москвы.")
            result = data.calculate(payload.get("route"), payload.get("date"), payload.get("price"))
        except InputError as error:
            return jsonify(error=str(error)), 400
        return jsonify(result)

    @app.errorhandler(SQLAlchemyError)
    def database_error(error):
        app.logger.error("Ошибка соединения или операции с базой данных: %s", type(error).__name__)
        if request.path.startswith("/api/"):
            return jsonify(error="Сервис временно недоступен. Попробуйте позже."), 503
        return render_template("error.html", title="Не удалось подключиться",
                               message="Сервис временно недоступен. Попробуйте ещё раз позже."), 503

    @app.errorhandler(404)
    def missing_page(error):
        return render_template("error.html", title="Страница не найдена",
                               message="Кажется, здесь нет нужного рейса. Вернитесь к прогнозу."), 404

    @app.errorhandler(413)
    def too_large(error):
        if request.path.startswith("/api/"):
            return jsonify(error="Слишком большой запрос."), 413
        return render_template("error.html", title="Слишком много данных",
                               message="Сократите введённый текст и попробуйте снова."), 413

    @app.cli.command("init-db")
    def init_db():
        Base.metadata.create_all(engine)
        click.echo("Таблицы MySQL созданы. Теперь можно зарегистрироваться на сайте.")

    return app
