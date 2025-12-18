import os
import time
from datetime import datetime, timezone

from flask import Flask, render_template, redirect, url_for, request, session
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, TIMESTAMP, case, text
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
import logging
import sys

load_dotenv()

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    user_id = Column(String(100), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(String(1024), nullable=True)
    priority = Column(Integer, nullable=False, default=0)
    due_date = Column(TIMESTAMP(timezone=True), nullable=True)
    completed = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


def build_mysql_uri(prefix):
    user = os.getenv(f"{prefix}_DB_USER")
    password = os.getenv(f"{prefix}_DB_PASSWORD")
    host = os.getenv(f"{prefix}_DB_HOST")
    port = os.getenv(f"{prefix}_DB_PORT")
    name = os.getenv(f"{prefix}_DB_NAME")

    host = host or "localhost"
    port = port or "3306"

    auth = user or ""
    if password:
        auth = f"{user}:{password}" if user else password
    elif user:
        auth = user

    return f"mysql+pymysql://{auth}@{host}:{port}/{name}"

master_uri = build_mysql_uri("MASTER")
slave_uri = build_mysql_uri("SLAVE") if any(os.getenv(f"SLAVE_DB_{k}") for k in ["USER", "HOST", "NAME"]) else master_uri

master_engine = create_engine(master_uri, pool_pre_ping=True)
slave_engine = create_engine(slave_uri, pool_pre_ping=True)

MasterSession = scoped_session(sessionmaker(bind=master_engine, autoflush=False, autocommit=False))
SlaveSession = scoped_session(sessionmaker(bind=slave_engine, autoflush=False, autocommit=False))

def create_app():
    app = Flask(__name__)
    app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret")

    Base.metadata.create_all(bind=master_engine)

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1
    )

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stdout
    )
    logger = logging.getLogger("access")
    db_logger = logging.getLogger("database")

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        MasterSession.remove()
        SlaveSession.remove()

    def should_force_master():
        value = session.get("force_master_until")
        if not value:
            return False
        try:
            until = float(value)
        except (TypeError, ValueError):
            session.pop("force_master_until", None)
            return False
        now = time.time()
        if now <= until:
            return True
        session.pop("force_master_until", None)
        return False

    def mark_write():
        window_seconds = int(os.getenv("READ_AFTER_WRITE_WINDOW_SECONDS", "5"))
        session["force_master_until"] = str(time.time() + window_seconds)

    def get_read_session():
        if should_force_master():
            db_logger.debug("Using MASTER for read (write consistency window)")
            return MasterSession()
        
        try:
            db_logger.debug("Using SLAVE for read")
            session = SlaveSession()
            # Test connection by executing a simple query
            session.execute(text("SELECT 1"))
            return session
        except Exception as e:
            db_logger.warning(f"SLAVE database unavailable ({e}), falling back to MASTER for read operations")
            SlaveSession.remove()
            return MasterSession()

    def get_user_id():
        return session.get("user_id")

    @app.after_request
    def log_request(response):
        ips = request.headers.get("X-Forwarded-For", request.remote_addr)
        ips = ", ".join([ip.strip() for ip in ips.split(",")])
        now_str = datetime.now(timezone.utc).strftime("%d/%b/%Y:%H:%M:%S %z")
        method = request.method
        path = request.full_path if request.query_string else request.path
        protocol = request.environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        status = response.status_code
        length = response.content_length or 0
        referer = request.headers.get("Referer", "-")
        user_agent = request.headers.get("User-Agent", "-")
        logger.info(f'{ips} - - [{now_str}] "{method} {path} {protocol}" {status} {length} "{referer}" "{user_agent}"')
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            if not username:
                return render_template("login.html", error="Username is required")
            if not password:
                return render_template("login.html", error="Password is required")
            if len(password) < 5:
                return render_template("login.html", error="Password must be at least 5 characters")

            db = MasterSession()
            user = db.query(User).filter(User.username == username).first()
            if user:
                if not check_password_hash(user.password_hash, password):
                    return render_template("login.html", error="Invalid username or password")
            else:
                password_hash = generate_password_hash(password)
                new_user = User(username=username, password_hash=password_hash)
                db.add(new_user)
                db.commit()

            session["user_id"] = username
            mark_write()
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.pop("user_id", None)
        return redirect(url_for("login"))

    @app.route("/", methods=["GET"])
    def index():
        user_id = get_user_id()
        if not user_id:
            return redirect(url_for("login"))
        
        db = get_read_session()
        
        sort = request.args.get("sort", "due")
        query = db.query(Task).filter(Task.user_id == user_id)
        if sort == "priority":
            query = query.order_by(
                Task.completed.asc(),
                Task.priority.desc(),
                case((Task.due_date.is_(None), 1), else_=0),
                Task.due_date.asc(),
                Task.created_at.desc()
            )
        else:
            query = query.order_by(
                Task.completed.asc(),
                case((Task.due_date.is_(None), 1), else_=0),
                Task.due_date.asc(),
                Task.priority.desc(),
                Task.created_at.desc()
            )
        tasks = query.all()
        now = datetime.now(timezone.utc)
        for task in tasks:
            if task.due_date:
                task_due = task.due_date
                if task_due.tzinfo is None:
                    task_due = task_due.replace(tzinfo=timezone.utc)
                task.is_overdue = not task.completed and task_due < now
            else:
                task.is_overdue = False
        return render_template("index.html", tasks=tasks, sort=sort, user_id=user_id)

    @app.route("/task/new", methods=["GET", "POST"])
    def create_task():
        user_id = get_user_id()
        if not user_id:
            return redirect(url_for("login"))
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip() or None
            priority_raw = request.form.get("priority", "0").strip()
            due_raw = request.form.get("due_date", "").strip()
            try:
                priority = int(priority_raw)
            except ValueError:
                priority = 0
            if due_raw:
                try:
                    due_date = datetime.strptime(due_raw, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        due_date = datetime.strptime(due_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        due_date = None
            else:
                due_date = None
            if not title:
                return render_template("task_form.html", error="Title is required", task=None, user_id=user_id)
            db = MasterSession()
            task = Task(user_id=user_id, title=title, description=description, priority=priority, due_date=due_date, completed=False)
            db.add(task)
            db.commit()
            mark_write()
            return redirect(url_for("index"))
        return render_template("task_form.html", task=None, user_id=user_id)

    @app.route("/task/<int:task_id>/edit", methods=["GET", "POST"])
    def edit_task(task_id):
        user_id = get_user_id()
        if not user_id:
            return redirect(url_for("login"))
        db = MasterSession()
        task = db.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
        if not task:
            return redirect(url_for("index"))
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip() or None
            priority_raw = request.form.get("priority", "0").strip()
            due_raw = request.form.get("due_date", "").strip()
            completed_raw = request.form.get("completed", "off")
            try:
                priority = int(priority_raw)
            except ValueError:
                priority = task.priority
            if due_raw:
                try:
                    due_date = datetime.strptime(due_raw, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        due_date = datetime.strptime(due_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        due_date = task.due_date
            else:
                due_date = None
            if not title:
                return render_template("task_form.html", error="Title is required", task=task, user_id=user_id)
            task.title = title
            task.description = description
            task.priority = priority
            task.due_date = due_date
            task.completed = completed_raw == "on"
            db.commit()
            mark_write()
            return redirect(url_for("index"))
        return render_template("task_form.html", task=task, user_id=user_id)

    @app.route("/task/<int:task_id>/delete", methods=["POST"])
    def delete_task(task_id):
        user_id = get_user_id()
        if not user_id:
            return redirect(url_for("login"))
        db = MasterSession()
        task = db.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
        if task:
            db.delete(task)
            db.commit()
            mark_write()
        return redirect(url_for("index"))

    @app.route("/task/<int:task_id>/toggle", methods=["POST"])
    def toggle_task(task_id):
        user_id = get_user_id()
        if not user_id:
            return redirect(url_for("login"))
        db = MasterSession()
        task = db.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
        if task:
            task.completed = not task.completed
            db.commit()
            mark_write()
        return redirect(url_for("index"))

    return app

app = create_app()
