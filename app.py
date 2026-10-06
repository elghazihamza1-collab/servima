# -*- coding: utf-8 -*-
"""
Servima — inDrive-style services marketplace (Morocco), v2.

inDrive flow: clients post job requests (price in MAD), workers browse & accept.
100% FREE during launch — no commissions.

Run:  pip install -r requirements.txt
      python seed.py      # first run only: creates the 35 launch categories
      python app.py
"""
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_from_directory, abort, flash, Response,
)
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from translations import (
    STRINGS, SUPPORTED, DEFAULT, CITY_KEYS, city_name,
)

# ---------------------------------------------------------------- config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me-in-prod")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "marketplace.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(BASE_DIR, "uploads")
app.config["CAT_UPLOAD_FOLDER"] = os.path.join(BASE_DIR, "static", "img", "categories")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB uploads
app.config["SESSION_TYPE"] = "filesystem"          # server-side sessions
app.config["SESSION_FILE_DIR"] = os.path.join(BASE_DIR, "instance", "sessions")

SUPPORT_WHATSAPP = "212621850430"  # public support number (password help)
app.config["SESSION_PERMANENT"] = False

ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["CAT_UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)

Session(app)          # server-side sessions (filesystem)
db = SQLAlchemy(app)  # ORM -> parameterized queries, no raw SQL


# ---------------------------------------------------------------- models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), unique=True, nullable=False)  # login identity
    city = db.Column(db.String(40), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    trade = db.Column(db.String(40))              # worker's trade (category key)
    is_worker = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_verified = db.Column(db.Boolean, default=False)  # "trusted" badge
    is_blocked = db.Column(db.Boolean, default=False)
    photo_file = db.Column(db.String(255))   # optional profile photo (uploads/)
    bio = db.Column(db.Text)                 # optional short bio
    experience_years = db.Column(db.Integer)  # optional years of experience
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    listings = db.relationship("Listing", backref="worker", cascade="all, delete-orphan")

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    # --- template helpers (frontend contract) ---
    @property
    def trade_name(self):
        return cat_name(self.trade, get_lang()) if self.trade else ""

    @property
    def member_since(self):
        return self.created_at.strftime("%Y-%m-%d") if self.created_at else ""

    @property
    def photo(self):
        # optional uploaded profile photo; templates guard with {% if %}
        if self.photo_file:
            return url_for("uploaded_file", filename=self.photo_file)
        return None


class Category(db.Model):
    """DB-managed trade categories (admin can add/edit/delete)."""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), unique=True, nullable=False)  # slug
    name_en = db.Column(db.String(80), nullable=False)
    name_fr = db.Column(db.String(80), nullable=False)
    name_ar = db.Column(db.String(80), nullable=False)
    photo = db.Column(db.String(255))  # filename in static/img/categories/

    def label(self, lang):
        return {"ar": self.name_ar, "fr": self.name_fr}.get(lang, self.name_en)


class JobRequest(db.Model):
    """Core inDrive flow: a client posts a job, workers accept it."""
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(120), nullable=False)
    client_phone = db.Column(db.String(40), nullable=False)
    category_key = db.Column(
        db.String(40), db.ForeignKey("category.key", ondelete="SET NULL"),
        nullable=True,
    )
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    city = db.Column(db.String(40), nullable=False)
    price = db.Column(db.Integer, nullable=False)  # MAD, proposed
    status = db.Column(db.String(20), default="open")  # open|accepted|done|cancelled
    accepted_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship("Category")
    accepter = db.relationship("User", foreign_keys=[accepted_by])

    # --- template helpers (frontend contract) ---
    @property
    def category_name(self):
        return cat_name(self.category_key, get_lang()) if self.category_key else ""

    @property
    def city_name(self):
        return city_name(self.city, get_lang())

    @property
    def show_phone(self):
        u = current_user()
        return bool(u and (u.is_admin or u.phone == self.client_phone
                           or (self.accepted_by and self.accepted_by == u.id)))


class Notification(db.Model):
    """Free in-app notifications: a worker is notified of new jobs in their trade+city."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey("job_request.id"), nullable=True)
    kind = db.Column(db.String(20), default="new_job")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="notifications")
    job = db.relationship("JobRequest")


class Message(db.Model):
    """In-site chat: text/photo messages between the client and the accepted worker."""
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job_request.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    text = db.Column(db.Text)
    image_file = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    job = db.relationship("JobRequest", backref="messages")
    sender = db.relationship("User")


class Visit(db.Model):
    """Lightweight page-view log for the admin visitor counter."""
    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(255), nullable=False)
    ip = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Listing(db.Model):
    """Worker service listings / portfolio (kept from v1)."""
    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Integer, nullable=False)  # MAD per day
    city = db.Column(db.String(40), nullable=False)
    photo = db.Column(db.String(255))             # filename in uploads/
    status = db.Column(db.String(20), default="pending")  # pending|approved|rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    bookings = db.relationship("Booking", backref="listing", cascade="all, delete-orphan")


class Booking(db.Model):
    """Booking requests on worker listings (kept from v1)."""
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False)
    ref = db.Column(db.String(12), unique=True, nullable=False)
    client_name = db.Column(db.String(120), nullable=False)
    client_phone = db.Column(db.String(40), nullable=False)
    date = db.Column(db.String(40), nullable=False)
    message = db.Column(db.Text, default="")
    status = db.Column(db.String(20), default="pending")  # pending|accepted|declined
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Rating(db.Model):
    """Client ratings for workers."""
    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rater_name = db.Column(db.String(120), nullable=False)
    stars = db.Column(db.Integer, nullable=False)  # 1-5
    comment = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- i18n
def get_lang():
    lang = session.get("lang", DEFAULT)
    return lang if lang in SUPPORTED else DEFAULT


def t(key, **kw):
    text = STRINGS[get_lang()].get(key, STRINGS[DEFAULT].get(key, key))
    return text.format(**kw) if kw else text


def cat_name(key, lang=None):
    """Category display name; accepts a key string or a Category object."""
    lang = lang or get_lang()
    if isinstance(key, Category):
        return key.label(lang)
    c = Category.query.filter_by(key=key).first()
    return c.label(lang) if c else key


def cat_dicts(lang=None):
    """Categories as plain dicts for templates: [{key, name, photo}]."""
    lang = lang or get_lang()
    return [
        {"key": c.key, "name": c.label(lang), "photo": c.photo}
        for c in Category.query.order_by(Category.id).all()
    ]


def avg_rating(worker_id):
    ratings = Rating.query.filter_by(worker_id=worker_id).all()
    if not ratings:
        return None
    return round(sum(r.stars for r in ratings) / len(ratings), 1)


@app.template_filter("timeago")
def timeago_filter(dt):
    """Human relative time in the current language, e.g. 'منذ ساعتين'."""
    if not dt:
        return ""
    lang = get_lang()
    try:
        secs = int((datetime.utcnow() - dt).total_seconds())
    except Exception:
        return ""
    if secs < 0:
        secs = 0
    if secs < 60:
        return {"ar": "الآن", "fr": "à l'instant", "en": "just now"}.get(lang, "just now")
    if lang == "ar":
        def ar(n, one, two, few, many):
            if n == 1:
                return "منذ " + one
            if n == 2:
                return "منذ " + two
            if 3 <= n <= 10:
                return "منذ %d %s" % (n, few)
            return "منذ %d %s" % (n, many)
        if secs < 3600:
            return ar(secs // 60, "دقيقة", "دقيقتين", "دقائق", "دقيقة")
        if secs < 86400:
            return ar(secs // 3600, "ساعة", "ساعتين", "ساعات", "ساعة")
        if secs < 86400 * 7:
            return ar(secs // 86400, "يوم", "يومين", "أيام", "يوماً")
        if secs < 86400 * 30:
            return ar(secs // (86400 * 7), "أسبوع", "أسبوعين", "أسابيع", "أسبوعاً")
        if secs < 86400 * 365:
            return ar(secs // (86400 * 30), "شهر", "شهرين", "أشهر", "شهراً")
        return dt.strftime("%d/%m/%Y")
    if lang == "fr":
        def fr(n, one, many):
            return "il y a %d %s" % (n, one if n == 1 else many)
        if secs < 3600:
            return fr(secs // 60, "minute", "minutes")
        if secs < 86400:
            return fr(secs // 3600, "heure", "heures")
        if secs < 86400 * 7:
            return fr(secs // 86400, "jour", "jours")
        if secs < 86400 * 30:
            return fr(secs // (86400 * 7), "semaine", "semaines")
        if secs < 86400 * 365:
            return fr(secs // (86400 * 30), "mois", "mois")
        return dt.strftime("%d/%m/%Y")
    def en(n, one):
        return "%d %s ago" % (n, one if n == 1 else one + "s")
    if secs < 3600:
        return en(secs // 60, "minute")
    if secs < 86400:
        return en(secs // 3600, "hour")
    if secs < 86400 * 7:
        return en(secs // 86400, "day")
    if secs < 86400 * 30:
        return en(secs // (86400 * 7), "week")
    if secs < 86400 * 365:
        return en(secs // (86400 * 30), "month")
    return dt.strftime("%d/%m/%Y")


@app.context_processor
def inject_i18n():
    lang = get_lang()
    me = current_user()
    notif_count = 0
    if me:
        try:
            notif_count = Notification.query.filter_by(user_id=me.id, is_read=False).count()
        except Exception:
            notif_count = 0
    return {
        "t": t,
        "lang": lang,
        "langs": SUPPORTED,
        "is_rtl": lang == "ar",
        "cat_name": cat_name,
        "city_name": lambda k: city_name(k, lang),
        "all_categories": Category.query.order_by(Category.id).all(),
        "cities": [(k, city_name(k, lang)) for k in CITY_KEYS],
        "avg_rating": avg_rating,
        "current_user": me,
        "notif_count": notif_count,
    }


_BOT_HINTS = ("bot", "crawl", "spider", "slurp", "uptime", "monitor", "pingdom")


@app.before_request
def log_visit():
    """Count real page views for the admin dashboard (skip files & bots)."""
    if request.path.startswith(("/static/", "/uploads/")):
        return
    ua = (request.headers.get("User-Agent") or "").lower()
    if any(h in ua for h in _BOT_HINTS):
        return
    try:
        db.session.add(Visit(path=request.path[:255], ip=(request.remote_addr or "")[:45]))
        db.session.commit()
    except Exception:
        db.session.rollback()


# ---------------------------------------------------------------- helpers
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    u = db.session.get(User, uid)
    if u and u.is_blocked:
        return None  # blocked users are effectively logged out
    return u


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapper(*a, **kw):
        if not current_user():
            flash(t("login_required"), "error")
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)

    return wrapper


def admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapper(*a, **kw):
        u = current_user()
        if not u or not u.is_admin:
            flash(t("admin_only"), "error")
            return redirect(url_for("home"))
        return view(*a, **kw)

    return wrapper


def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXT


def _valid_image(file_storage):
    """Extension allowlist + magic-byte check. Returns True/False."""
    if not file_storage or not file_storage.filename:
        return False
    if not allowed_file(file_storage.filename):
        return False
    head = file_storage.read(12)
    file_storage.seek(0)
    return (
        head[:3] == b"\xff\xd8\xff"          # jpeg
        or head[:8] == b"\x89PNG\r\n\x1a\n"  # png
        or head[:6] in (b"GIF87a", b"GIF89a")  # gif
        or head[:4] == b"RIFF" and head[8:12] == b"WEBP"  # webp
    )


def _store_upload(file_storage, folder):
    if not _valid_image(file_storage):
        return None
    fname = secrets.token_hex(8) + "_" + secure_filename(file_storage.filename)
    file_storage.save(os.path.join(folder, fname))
    return fname


def save_upload(file_storage):
    """Validate and store a worker listing photo in uploads/. Returns filename or None."""
    return _store_upload(file_storage, app.config["UPLOAD_FOLDER"])


def save_category_photo(file_storage):
    """Validate and store a category photo in static/img/categories/."""
    return _store_upload(file_storage, app.config["CAT_UPLOAD_FOLDER"])


def _delete_file(folder, filename):
    if filename:
        try:
            os.remove(os.path.join(folder, filename))
        except OSError:
            pass


# ---------------------------------------------------------------- public routes
@app.route("/lang/<code>")
def set_lang(code):
    if code in SUPPORTED:
        session["lang"] = code
    return redirect(request.referrer or url_for("home"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/")
def home():
    lang = get_lang()
    latest_jobs = (
        JobRequest.query.filter_by(status="open")
        .order_by(JobRequest.created_at.desc())
        .limit(6)
        .all()
    )
    stats = {
        "users": User.query.count(),
        "workers": User.query.filter_by(is_worker=True).count(),
        "jobs": JobRequest.query.filter_by(status="open").count(),
    }
    worker_counts = dict(
        db.session.query(User.trade, db.func.count(User.id))
        .filter(User.is_worker.is_(True), User.is_blocked.is_(False), User.trade.isnot(None))
        .group_by(User.trade)
        .all()
    )
    return render_template(
        "home.html",
        categories=cat_dicts(lang),
        latest_jobs=latest_jobs,
        stats=stats,
        worker_counts=worker_counts,
    )


# ---------------------------------------------------------------- jobs (inDrive flow)
@app.route("/jobs")
def jobs():
    lang = get_lang()
    category = request.args.get("category", "").strip()
    city = request.args.get("city", "").strip()
    q = request.args.get("q", "").strip()
    query = JobRequest.query.filter_by(status="open")
    if category and Category.query.filter_by(key=category).first():
        query = query.filter_by(category_key=category)
    else:
        category = ""
    if city in CITY_KEYS:
        query = query.filter_by(city=city)
    else:
        city = ""
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(JobRequest.title.ilike(like), JobRequest.description.ilike(like))
        )
    jobs = query.order_by(JobRequest.created_at.desc()).all()
    return render_template(
        "jobs.html",
        jobs=jobs,
        categories=cat_dicts(lang),
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
        category=category,
        city=city,
        q=q,
    )


@app.route("/jobs/new", methods=["GET", "POST"])
@login_required
def job_new():
    lang = get_lang()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category_key = request.form.get("category", "").strip()
        city = request.form.get("city", "").strip()
        try:
            price = int(request.form.get("price", "0"))
            assert price > 0
        except (ValueError, AssertionError):
            price = 0
        cat_ok = bool(Category.query.filter_by(key=category_key).first())
        if not (title and description and cat_ok and city in CITY_KEYS and price):
            flash(t("err_required"), "error")
            return render_template(
                "job_new.html",
                categories=cat_dicts(lang),
                cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
            )
        u = current_user()
        job = JobRequest(
            client_name=u.name, client_phone=u.phone, category_key=category_key,
            title=title, description=description, city=city, price=price,
            status="open",
        )
        db.session.add(job)
        db.session.commit()
        # free in-app notifications: alert matching workers (same trade + city)
        try:
            workers = User.query.filter_by(
                is_worker=True, is_blocked=False, trade=category_key, city=city,
            ).all()
            for w in workers:
                if w.id != u.id:
                    db.session.add(Notification(user_id=w.id, job_id=job.id))
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash(t("msg_job_posted"), "ok")
        return redirect(url_for("job_detail", job_id=job.id))
    return render_template(
        "job_new.html",
        categories=cat_dicts(lang),
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
    )


@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = db.get_or_404(JobRequest, job_id)
    accepter = db.session.get(User, job.accepted_by) if job.accepted_by else None
    share_text = "%s — %s MAD (%s)\n%s" % (
        job.title, job.price, job.city_name, url_for("job_detail", job_id=job.id, _external=True),
    )
    wa_url = "https://wa.me/?text=" + quote(share_text)
    me = current_user()
    can_chat = bool(me and job.status in ("accepted", "done") and (
        job.client_phone == me.phone or (job.accepted_by and job.accepted_by == me.id)
    ))
    can_edit = bool(me and job.status == "open" and (
        job.client_phone == me.phone or me.is_admin
    ))
    return render_template("job_detail.html", job=job, accepted_worker=accepter, wa_url=wa_url,
                           can_chat=can_chat, can_edit=can_edit)


@app.route("/jobs/<int:job_id>/accept", methods=["POST"])
@login_required
def job_accept(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    if not u.is_worker:
        flash(t("err_not_worker"), "error")
    elif job.status != "open":
        flash(t("err_not_open"), "error")
    elif job.client_phone == u.phone:
        flash(t("err_self_accept"), "error")
    else:
        job.status = "accepted"
        job.accepted_by = u.id
        db.session.commit()
        flash(t("msg_job_accepted", phone=job.client_phone), "ok")
    return redirect(url_for("job_detail", job_id=job.id))


@app.route("/jobs/<int:job_id>/done", methods=["POST"])
@login_required
def job_done(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    allowed = (
        u.is_admin
        or (job.accepted_by and job.accepted_by == u.id)
        or job.client_phone == u.phone
    )
    if not allowed:
        abort(403)
    if job.status != "accepted":
        flash(t("err_not_open"), "error")
    else:
        job.status = "done"
        db.session.commit()
        flash(t("msg_job_done"), "ok")
    return redirect(url_for("job_detail", job_id=job.id))


# ---------------------------------------------------------------- in-site chat
def _chat_party(job, u):
    """True if u is the client or the accepted worker of this job."""
    return bool(
        u
        and job.status in ("accepted", "done")
        and (job.client_phone == u.phone or (job.accepted_by and job.accepted_by == u.id))
    )


def _chat_other_party(job, u):
    """Return the User on the other side of the chat (for notifications)."""
    if job.accepted_by and job.accepted_by == u.id:
        return User.query.filter_by(phone=job.client_phone).first()
    return db.session.get(User, job.accepted_by) if job.accepted_by else None


@app.route("/jobs/<int:job_id>/chat")
@login_required
def job_chat(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    if not _chat_party(job, u):
        abort(403)
    # mark the other side's messages as read
    Message.query.filter_by(job_id=job.id, is_read=False).filter(
        Message.sender_id != u.id
    ).update({"is_read": True})
    db.session.commit()
    messages = (
        Message.query.filter_by(job_id=job.id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    other = _chat_other_party(job, u)
    return render_template("chat.html", job=job, messages=messages, other=other, me=u)


@app.route("/jobs/<int:job_id>/chat/messages")
@login_required
def job_chat_messages(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    if not _chat_party(job, u):
        abort(403)
    after = request.args.get("after", 0, type=int)
    messages = (
        Message.query.filter(Message.job_id == job.id, Message.id > after)
        .order_by(Message.id.asc())
        .all()
    )
    return {
        "messages": [
            {
                "id": m.id,
                "mine": m.sender_id == u.id,
                "text": m.text or "",
                "image": url_for("uploaded_file", filename=m.image_file) if m.image_file else "",
                "time": m.created_at.strftime("%H:%M") if m.created_at else "",
            }
            for m in messages
        ]
    }


@app.route("/jobs/<int:job_id>/chat/send", methods=["POST"])
@login_required
def job_chat_send(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    if not _chat_party(job, u):
        abort(403)
    text = (request.form.get("text") or "").strip()
    image_file = None
    photo = request.files.get("photo")
    if photo and photo.filename:
        image_file = save_upload(photo)
    if not text and not image_file:
        return redirect(url_for("job_chat", job_id=job.id))
    msg = Message(job_id=job.id, sender_id=u.id, text=text or None, image_file=image_file)
    db.session.add(msg)
    # free in-app notification for the other party (drives the 🔔 badge)
    try:
        other = _chat_other_party(job, u)
        if other and other.id != u.id:
            db.session.add(Notification(user_id=other.id, job_id=job.id, kind="new_message"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    return redirect(url_for("job_chat", job_id=job.id))


@app.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@login_required
def job_cancel(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    if not (u.is_admin or job.client_phone == u.phone):
        abort(403)
    if job.status in ("open", "accepted"):
        job.status = "cancelled"
        db.session.commit()
        flash(t("msg_job_cancelled"), "ok")
    return redirect(url_for("job_detail", job_id=job.id))


def _job_owner(job, u):
    """True if u posted this job (or is admin)."""
    return bool(u and (u.is_admin or job.client_phone == u.phone))


@app.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@login_required
def job_edit(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    # edit allowed only while the job is still open (not accepted yet)
    if not _job_owner(job, u) or job.status != "open":
        abort(403)
    lang = get_lang()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category_key = request.form.get("category", "").strip()
        city = request.form.get("city", "").strip()
        try:
            price = int(request.form.get("price", "0"))
            assert price > 0
        except (ValueError, AssertionError):
            price = 0
        cat_ok = bool(Category.query.filter_by(key=category_key).first())
        if not (title and description and cat_ok and city in CITY_KEYS and price):
            flash(t("err_required"), "error")
        else:
            job.title = title
            job.description = description
            job.category_key = category_key
            job.city = city
            job.price = price
            db.session.commit()
            flash(t("msg_job_updated"), "ok")
            return redirect(url_for("job_detail", job_id=job.id))
    return render_template(
        "job_edit.html", job=job,
        categories=cat_dicts(lang),
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
    )


@app.route("/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
def job_delete(job_id):
    job = db.get_or_404(JobRequest, job_id)
    u = current_user()
    # delete allowed only while the job is still open (not accepted yet)
    if not _job_owner(job, u) or job.status != "open":
        abort(403)
    Message.query.filter_by(job_id=job.id).delete()
    Notification.query.filter_by(job_id=job.id).delete()
    db.session.delete(job)
    db.session.commit()
    flash(t("msg_job_deleted"), "ok")
    return redirect(url_for("jobs"))


# ---------------------------------------------------------------- workers
@app.route("/workers")
def workers():
    lang = get_lang()
    q = request.args.get("q", "").strip()
    trade = request.args.get("trade", "").strip()
    city = request.args.get("city", "").strip()
    query = User.query.filter_by(is_worker=True, is_blocked=False)
    if trade and Category.query.filter_by(key=trade).first():
        query = query.filter_by(trade=trade)
    if city in CITY_KEYS:
        query = query.filter_by(city=city)
    if q:
        like = f"%{q}%"
        query = query.filter(User.name.ilike(like))
    workers = query.order_by(User.created_at.desc()).all()
    for w in workers:
        w.avg = avg_rating(w.id)
        w.rating_count = Rating.query.filter_by(worker_id=w.id).count()
    return render_template(
        "workers.html",
        workers=workers,
        categories=cat_dicts(lang),
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
        q=q, trade=trade, city=city,
    )


@app.route("/worker/<int:user_id>")
def worker_detail(user_id):
    worker = db.get_or_404(User, user_id)
    if not worker.is_worker or worker.is_blocked:
        abort(404)
    listings = (
        Listing.query.filter_by(worker_id=worker.id, status="approved")
        .order_by(Listing.created_at.desc())
        .all()
    )
    ratings = (
        Rating.query.filter_by(worker_id=worker.id)
        .order_by(Rating.created_at.desc())
        .all()
    )
    # WhatsApp chat link (Moroccan numbers: 06.. -> 2126..)
    wa_num = re.sub(r"\D", "", worker.phone or "")
    if wa_num.startswith("0"):
        wa_num = "212" + wa_num[1:]
    worker_wa_url = "https://wa.me/%s?text=%s" % (wa_num, quote(t("wa_worker_msg", name=worker.name)))
    return render_template(
        "worker_detail.html",
        worker=worker,
        listings=listings,
        ratings=ratings,
        avg_rating=avg_rating(worker.id) or 0,
        worker_wa_url=worker_wa_url,
    )


@app.route("/worker/<int:user_id>/rate", methods=["POST"])
def worker_rate(user_id):
    worker = db.get_or_404(User, user_id)
    if not worker.is_worker or worker.is_blocked:
        abort(404)
    rater_name = request.form.get("rater_name", "").strip()
    comment = request.form.get("comment", "").strip()
    try:
        stars = int(request.form.get("stars", "0"))
    except ValueError:
        stars = 0
    if not rater_name or stars not in (1, 2, 3, 4, 5):
        flash(t("err_required"), "error")
        return redirect(url_for("worker_detail", user_id=worker.id))
    existing = Rating.query.filter(
        Rating.worker_id == worker.id,
        db.func.lower(Rating.rater_name) == rater_name.lower(),
    ).first()
    if existing:
        flash(t("already_rated"), "error")
        return redirect(url_for("worker_detail", user_id=worker.id))
    db.session.add(Rating(
        worker_id=worker.id, rater_name=rater_name, stars=stars, comment=comment
    ))
    db.session.commit()
    flash(t("msg_rating_saved"), "ok")
    return redirect(url_for("worker_detail", user_id=worker.id))


# ---------------------------------------------------------------- auth (phone + password)
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    lang = get_lang()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        city = request.form.get("city", "").strip()
        password = request.form.get("password", "")
        account_type = request.form.get("account_type", "client")
        trade = request.form.get("trade", "").strip()
        is_worker = account_type == "worker"
        cat_ok = (not is_worker) or bool(Category.query.filter_by(key=trade).first())
        if not (name and phone and city and password and city in CITY_KEYS and cat_ok):
            flash(t("err_required"), "error")
        elif User.query.filter_by(phone=phone).first():
            flash(t("err_phone_taken"), "error")
        else:
            user = User(
                name=name, phone=phone, city=city,
                trade=trade if is_worker else None,
                is_worker=is_worker,
                is_admin=(User.query.count() == 0),  # first user = admin
            )
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:  # double-submit / race: phone already taken
                db.session.rollback()
                flash(t("err_phone_taken"), "error")
                return redirect(url_for("register"))
            session["user_id"] = user.id
            flash(t("welcome", name=user.name), "ok")
            return redirect(url_for("dashboard"))
    return render_template(
        "register.html",
        categories=cat_dicts(lang),
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(phone=phone).first()
        if user and user.is_blocked:
            flash(t("err_blocked"), "error")
        elif user and user.check_password(password):
            session["user_id"] = user.id
            flash(t("welcome", name=user.name), "ok")
            nxt = request.args.get("next")
            return redirect(nxt or url_for("dashboard"))
        else:
            flash(t("err_bad_login"), "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash(t("logged_out"), "ok")
    return redirect(url_for("home"))


@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    u = current_user()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not u.check_password(current):
            flash(t("err_wrong_current_pw"), "error")
        elif len(new) < 4:
            flash(t("err_pw_too_short"), "error")
        elif new != confirm:
            flash(t("err_pw_mismatch"), "error")
        else:
            u.set_password(new)
            db.session.commit()
            flash(t("msg_pw_changed"), "ok")
            return redirect(url_for("dashboard"))
    return render_template("change_password.html")


@app.route("/forgot-password")
def forgot_password():
    wa_url = "https://wa.me/%s?text=%s" % (SUPPORT_WHATSAPP, quote(t("wa_forgot_pw")))
    return render_template("forgot_password.html", wa_url=wa_url)


# ---------------------------------------------------------------- dashboard
@app.route("/notifications")
@login_required
def notifications():
    u = current_user()
    notifs = (
        Notification.query.filter_by(user_id=u.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    Notification.query.filter_by(user_id=u.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return render_template("notifications.html", notifs=notifs)


@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    posted_jobs = (
        JobRequest.query.filter_by(client_phone=u.phone)
        .order_by(JobRequest.created_at.desc())
        .all()
    )
    accepted_jobs = (
        JobRequest.query.filter_by(accepted_by=u.id)
        .order_by(JobRequest.created_at.desc())
        .all()
    )
    listings = (
        Listing.query.filter_by(worker_id=u.id)
        .order_by(Listing.created_at.desc())
        .all()
    )
    listing_ids = [l.id for l in listings]
    requests = (
        Booking.query.filter(Booking.listing_id.in_(listing_ids))
        .order_by(Booking.created_at.desc())
        .all()
        if listing_ids
        else []
    )
    return render_template(
        "dashboard.html",
        posted_jobs=posted_jobs,
        accepted_jobs=accepted_jobs,
        listings=listings,
        requests=requests,
        categories=cat_dicts(get_lang()),
    )


@app.route("/become-worker", methods=["POST"])
@login_required
def become_worker():
    """inDrive-style: a client activates worker mode (or updates their trade)."""
    u = current_user()
    trade = request.form.get("trade", "").strip()
    if not Category.query.filter_by(key=trade).first():
        flash(t("err_required"), "error")
        return redirect(url_for("dashboard"))
    first_time = not u.is_worker
    u.is_worker = True
    u.trade = trade
    db.session.commit()
    flash(t("msg_now_worker") if first_time else t("msg_trade_updated"), "ok")
    return redirect(url_for("dashboard"))


@app.route("/profile")
@login_required
def profile():
    """Consolidated 'My account' page: everything the user can change."""
    lang = get_lang()
    return render_template(
        "profile.html",
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
    )


@app.route("/profile", methods=["POST"])
@login_required
def profile_update():
    """Consolidated profile update: photo, name, city (+ bio/experience for workers)."""
    u = current_user()
    name = request.form.get("name", "").strip()
    city = request.form.get("city", "").strip()
    bio = request.form.get("bio", "").strip()
    exp_raw = request.form.get("experience_years", "").strip()
    try:
        exp = int(exp_raw) if exp_raw else None
        assert exp is None or 0 <= exp <= 60
    except (ValueError, AssertionError):
        exp = None
    if name:
        u.name = name
    if city in CITY_KEYS:
        u.city = city
    photo = save_upload(request.files.get("photo"))
    if photo:
        _delete_file(app.config["UPLOAD_FOLDER"], u.photo_file)
        u.photo_file = photo
    u.bio = bio or None
    u.experience_years = exp
    db.session.commit()
    flash(t("msg_profile_updated"), "ok")
    return redirect(url_for("profile"))


def _listing_form_data(listing=None):
    return {
        "title": request.form.get("title", listing.title if listing else "").strip(),
        "category": request.form.get("category", listing.category if listing else ""),
        "description": request.form.get("description", listing.description if listing else "").strip(),
        "price": request.form.get("price", listing.price if listing else "").strip(),
        "city": request.form.get("city", listing.city if listing else ""),
    }


def _valid_cat_keys():
    return {c.key for c in Category.query.all()}


@app.route("/dashboard/listings/new", methods=["GET", "POST"])
@login_required
def listing_new():
    lang = get_lang()
    cats = _valid_cat_keys()
    if request.method == "POST":
        data = _listing_form_data()
        try:
            price = int(data["price"])
            assert price > 0
        except (ValueError, AssertionError):
            price = 0
        if not (data["title"] and data["category"] in cats
                and data["description"] and price and data["city"] in CITY_KEYS):
            flash(t("err_required"), "error")
            return render_template(
                "listing_form.html", data=data, listing=None,
                categories=cat_dicts(lang),
                cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
            )
        photo = save_upload(request.files.get("photo"))
        listing = Listing(
            worker_id=current_user().id, category=data["category"],
            title=data["title"], description=data["description"],
            price=price, city=data["city"], photo=photo, status="pending",
        )
        db.session.add(listing)
        db.session.commit()
        flash(t("msg_created"), "ok")
        return redirect(url_for("dashboard"))
    return render_template(
        "listing_form.html", data={}, listing=None,
        categories=cat_dicts(lang),
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
    )


@app.route("/dashboard/listings/<int:listing_id>/edit", methods=["GET", "POST"])
@login_required
def listing_edit(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.worker_id != current_user().id:
        abort(403)
    lang = get_lang()
    cats = _valid_cat_keys()
    if request.method == "POST":
        data = _listing_form_data(listing)
        try:
            price = int(data["price"])
            assert price > 0
        except (ValueError, AssertionError):
            price = 0
        if not (data["title"] and data["category"] in cats
                and data["description"] and price and data["city"] in CITY_KEYS):
            flash(t("err_required"), "error")
            return render_template(
                "listing_form.html", data=data, listing=listing,
                categories=cat_dicts(lang),
                cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
            )
        listing.title = data["title"]
        listing.category = data["category"]
        listing.description = data["description"]
        listing.price = price
        listing.city = data["city"]
        new_photo = save_upload(request.files.get("photo"))
        if new_photo:
            _delete_file(app.config["UPLOAD_FOLDER"], listing.photo)
            listing.photo = new_photo
        listing.status = "pending"  # re-approval after edits
        db.session.commit()
        flash(t("msg_updated"), "ok")
        return redirect(url_for("dashboard"))
    return render_template(
        "listing_form.html",
        data={"title": listing.title, "category": listing.category,
              "description": listing.description, "price": listing.price,
              "city": listing.city},
        listing=listing,
        categories=cat_dicts(lang),
        cities=[(k, city_name(k, lang)) for k in CITY_KEYS],
    )


@app.route("/dashboard/listings/<int:listing_id>/delete", methods=["POST"])
@login_required
def listing_delete(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.worker_id != current_user().id:
        abort(403)
    _delete_file(app.config["UPLOAD_FOLDER"], listing.photo)
    db.session.delete(listing)
    db.session.commit()
    flash(t("msg_deleted"), "ok")
    return redirect(url_for("dashboard"))


@app.route("/dashboard/bookings/<int:booking_id>/<action>", methods=["POST"])
@login_required
def booking_action(booking_id, action):
    booking = db.get_or_404(Booking, booking_id)
    if booking.listing.worker_id != current_user().id:
        abort(403)
    if action == "accept":
        booking.status = "accepted"
        flash(t("msg_accepted"), "ok")
    elif action == "decline":
        booking.status = "declined"
        flash(t("msg_declined"), "ok")
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------- listings & bookings (kept from v1)
@app.route("/listing/<int:listing_id>")
def listing_detail(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    u = current_user()
    is_owner_or_admin = u and (u.id == listing.worker_id or u.is_admin)
    if listing.status != "approved" and not is_owner_or_admin:
        abort(404)
    return render_template("listing.html", listing=listing)


@app.route("/booking/new/<int:listing_id>", methods=["POST"])
def booking_new(listing_id):
    listing = db.get_or_404(Listing, listing_id)
    if listing.status != "approved":
        abort(404)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    date = request.form.get("date", "").strip()
    message = request.form.get("message", "").strip()
    if not (name and phone and date):
        flash(t("err_required"), "error")
        return redirect(url_for("listing_detail", listing_id=listing.id))
    ref = secrets.token_hex(4).upper()
    while Booking.query.filter_by(ref=ref).first():
        ref = secrets.token_hex(4).upper()
    booking = Booking(
        listing_id=listing.id, ref=ref, client_name=name,
        client_phone=phone, date=date, message=message,
    )
    db.session.add(booking)
    db.session.commit()
    flash(t("msg_req_sent"), "ok")
    return render_template("booking_confirm.html", booking=booking, listing=listing)


@app.route("/booking/lookup", methods=["GET", "POST"])
def booking_lookup():
    booking = None
    tried = False
    if request.method == "POST":
        tried = True
        ref = request.form.get("ref", "").strip().upper()
        booking = Booking.query.filter_by(ref=ref).first()
    return render_template("booking_lookup.html", booking=booking, tried=tried)


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/faq")
def faq():
    return render_template("faq.html")


# ---------------------------------------------------------------- admin
@app.route("/admin")
@admin_required
def admin():
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = datetime.utcnow() - timedelta(days=7)
    stats = {
        "users": User.query.count(),
        "workers": User.query.filter_by(is_worker=True).count(),
        "jobs": JobRequest.query.count(),
        "visitors_today": db.session.query(
            db.func.count(db.func.distinct(Visit.ip))
        ).filter(Visit.created_at >= today).scalar() or 0,
        "visitors_week": db.session.query(
            db.func.count(db.func.distinct(Visit.ip))
        ).filter(Visit.created_at >= week_ago).scalar() or 0,
        "pageviews_week": Visit.query.filter(Visit.created_at >= week_ago).count(),
    }
    top_pages = (
        db.session.query(Visit.path, db.func.count(Visit.id).label("n"))
        .filter(Visit.created_at >= week_ago)
        .group_by(Visit.path)
        .order_by(db.desc("n"))
        .limit(5)
        .all()
    )
    users = User.query.order_by(User.created_at.desc()).all()
    jobs = JobRequest.query.order_by(JobRequest.created_at.desc()).all()
    categories = Category.query.order_by(Category.id).all()
    pending = (
        Listing.query.filter_by(status="pending")
        .order_by(Listing.created_at.desc())
        .all()
    )
    return render_template(
        "admin.html", stats=stats, users=users, jobs=jobs,
        categories=categories, pending=pending, top_pages=top_pages,
    )


@app.route("/admin/users/<int:user_id>/<action>", methods=["POST"])
@admin_required
def admin_user_action(user_id, action):
    return _admin_user_do(user_id, action)


def _admin_user_do(user_id, action):
    """Shared logic for admin user actions (also used by named aliases)."""
    user = db.get_or_404(User, user_id)
    me = current_user()
    if action == "delete":
        if user.id == me.id:
            flash(t("err_self_action"), "error")
            return redirect(url_for("admin"))
        # release jobs they had accepted back to open
        for job in JobRequest.query.filter_by(accepted_by=user.id).all():
            job.accepted_by = None
            job.status = "open"
        Rating.query.filter_by(worker_id=user.id).delete()
        db.session.delete(user)  # listings cascade via relationship
        db.session.commit()
        flash(t("msg_deleted"), "ok")
    elif action in ("block", "unblock", "verify", "unverify"):
        if user.id == me.id:
            flash(t("err_self_action"), "error")
            return redirect(url_for("admin"))
        if action == "block":
            user.is_blocked = True
            flash(t("msg_user_blocked"), "ok")
        elif action == "unblock":
            user.is_blocked = False
            flash(t("msg_user_unblocked"), "ok")
        elif action == "verify":
            user.is_verified = True
            flash(t("msg_verified"), "ok")
        elif action == "unverify":
            user.is_verified = False
            flash(t("msg_unverified"), "ok")
        db.session.commit()
    else:
        abort(404)
    return redirect(url_for("admin"))


# Named aliases used by templates/admin.html
@app.route("/admin/users/<int:user_id>/verify", methods=["POST"])
@admin_required
def admin_user_verify(user_id):
    return _admin_user_do(user_id, "verify")


@app.route("/admin/users/<int:user_id>/unverify", methods=["POST"])
@admin_required
def admin_user_unverify(user_id):
    return _admin_user_do(user_id, "unverify")


@app.route("/admin/users/<int:user_id>/block", methods=["POST"])
@admin_required
def admin_user_block(user_id):
    return _admin_user_do(user_id, "block")


@app.route("/admin/users/<int:user_id>/unblock", methods=["POST"])
@admin_required
def admin_user_unblock(user_id):
    return _admin_user_do(user_id, "unblock")


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    return _admin_user_do(user_id, "delete")


@app.route("/admin/users/<int:user_id>/reset-pw", methods=["POST"])
@admin_required
def admin_user_reset_pw(user_id):
    """Admin generates a temporary password for a user who forgot theirs."""
    user = db.get_or_404(User, user_id)
    temp = "%06d" % secrets.randbelow(1000000)
    user.set_password(temp)
    db.session.commit()
    flash(t("msg_pw_reset", name=user.name, password=temp), "ok")
    return redirect(url_for("admin"))


CAT_KEY_RE = re.compile(r"^[a-z0-9_]+$")


@app.route("/admin/categories/new", methods=["GET", "POST"])
@admin_required
def admin_cat_new():
    if request.method == "POST":
        key = request.form.get("key", "").strip().lower()
        name_ar = request.form.get("name_ar", "").strip()
        name_fr = request.form.get("name_fr", "").strip()
        name_en = request.form.get("name_en", "").strip()
        if not key:
            # auto-generate a slug from the English name (admin inline form has no key field)
            key = re.sub(r"[^a-z0-9]+", "_", name_en.lower()).strip("_") or "cat"
            i, candidate = 2, key
            while Category.query.filter_by(key=candidate).first():
                candidate = f"{key}_{i}"
                i += 1
            key = candidate
        if (not (key and name_ar and name_fr and name_en)
                or not CAT_KEY_RE.match(key)
                or Category.query.filter_by(key=key).first()):
            flash(t("err_required"), "error")
            return render_template("category_form.html", category=None)
        photo = save_category_photo(request.files.get("photo"))
        db.session.add(Category(
            key=key, name_ar=name_ar, name_fr=name_fr, name_en=name_en, photo=photo
        ))
        db.session.commit()
        flash(t("msg_cat_saved"), "ok")
        return redirect(url_for("admin"))
    return render_template("category_form.html", category=None)


@app.route("/admin/categories/<int:cat_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_cat_edit(cat_id):
    cat = db.get_or_404(Category, cat_id)
    if request.method == "POST":
        name_ar = request.form.get("name_ar", "").strip()
        name_fr = request.form.get("name_fr", "").strip()
        name_en = request.form.get("name_en", "").strip()
        if not (name_ar and name_fr and name_en):
            flash(t("err_required"), "error")
            return render_template("category_form.html", category=cat)
        cat.name_ar, cat.name_fr, cat.name_en = name_ar, name_fr, name_en
        new_photo = save_category_photo(request.files.get("photo"))
        if new_photo:
            _delete_file(app.config["CAT_UPLOAD_FOLDER"], cat.photo)
            cat.photo = new_photo
        db.session.commit()
        flash(t("msg_cat_saved"), "ok")
        return redirect(url_for("admin"))
    return render_template("category_form.html", category=cat)


@app.route("/admin/categories/<int:cat_id>/delete", methods=["POST"])
@admin_required
def admin_cat_delete(cat_id):
    cat = db.get_or_404(Category, cat_id)
    # keep jobs, just detach them from the deleted category
    for job in JobRequest.query.filter_by(category_key=cat.key).all():
        job.category_key = None
    _delete_file(app.config["CAT_UPLOAD_FOLDER"], cat.photo)
    db.session.delete(cat)
    db.session.commit()
    flash(t("msg_cat_deleted"), "ok")
    return redirect(url_for("admin"))


# Named aliases used by templates/admin.html (key-based)
@app.route("/admin/categories/add", methods=["POST"])
@admin_required
def admin_category_add():
    # same handling as admin_cat_new (POST branch)
    return admin_cat_new()


@app.route("/admin/categories/key/<key>/edit")
@admin_required
def admin_category_edit(key):
    cat = Category.query.filter_by(key=key).first_or_404()
    return redirect(url_for("admin_cat_edit", cat_id=cat.id))


@app.route("/admin/categories/key/<key>/delete", methods=["POST"])
@admin_required
def admin_category_delete(key):
    cat = Category.query.filter_by(key=key).first_or_404()
    return admin_cat_delete(cat.id)


@app.route("/admin/jobs/<int:job_id>/delete", methods=["POST"])
@admin_required
def admin_job_delete(job_id):
    job = db.get_or_404(JobRequest, job_id)
    # delete dependents first: Postgres FKs block deleting the job otherwise
    Message.query.filter_by(job_id=job.id).delete()
    Notification.query.filter_by(job_id=job.id).delete()
    db.session.delete(job)
    db.session.commit()
    flash(t("msg_deleted"), "ok")
    return redirect(url_for("admin"))


@app.route("/admin/listings/<int:listing_id>/<action>", methods=["POST"])
@app.route("/admin/<int:listing_id>/<action>", methods=["POST"])  # legacy path
@admin_required
def admin_listing_action(listing_id, action):
    listing = db.get_or_404(Listing, listing_id)
    if action == "approve":
        listing.status = "approved"
        flash(t("msg_approved"), "ok")
    elif action == "reject":
        listing.status = "rejected"
        flash(t("msg_rejected"), "ok")
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for("admin"))


# ---------------------------------------------------------------- SEO helpers
@app.route("/robots.txt")
def robots_txt():
    body = "User-agent: *\nAllow: /\nSitemap: %ssitemap.xml\n" % request.host_url
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    pages = ["", "jobs", "workers", "register", "login", "terms"]
    urls = "\n".join(
        "  <url><loc>%s%s</loc><changefreq>daily</changefreq></url>" % (request.host_url, p)
        for p in pages
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + urls + "\n</urlset>"
    )
    return Response(body, mimetype="application/xml")


# ---------------------------------------------------------------- main
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
