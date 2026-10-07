# -*- coding: utf-8 -*-
"""Seed ONLY the 35 launch categories. No users, no listings.

Run once on a fresh database:  python seed.py
Safe to re-run: existing categories (matched by key) are updated, not duplicated.
Photo files (<key>.jpg) live in static/img/categories/ and are managed separately.
"""
from app import db, app, Category
from sqlalchemy import inspect, text

# (key, ar, fr, en)
CATEGORIES = [
    ("painting", "صباغ", "Peintre", "Painter"),
    ("electrician", "كهربائي", "Électricien", "Electrician"),
    ("plumber", "سباك", "Plombier", "Plumber"),
    ("barber", "حلاق", "Barbier", "Barber"),
    ("carpenter", "نجار", "Menuisier", "Carpenter"),
    ("cleaner", "عامل نظافة", "Agent de nettoyage", "Cleaner"),
    ("mechanic", "ميكانيكي", "Mécanicien", "Mechanic"),
    ("mason", "بنّاء", "Maçon", "Mason"),
    ("blacksmith", "حداد", "Forgeron", "Blacksmith"),
    ("phone_repair", "مصلح هواتف", "Réparateur de téléphones", "Phone repair"),
    ("photographer", "مصور", "Photographe", "Photographer"),
    ("cook_events", "طباخ للمناسبات", "Cuisinier événementiel", "Event cook"),
    ("tutor", "مدرس دروس دعم", "Professeur de soutien", "Tutor"),
    ("delivery", "سائق توصيل", "Livreur", "Delivery driver"),
    ("gardener", "بستاني", "Jardinier", "Gardener"),
    ("tailor", "خياط", "Tailleur", "Tailor"),
    ("appliance_repair", "مصلح أجهزة كهرومنزلية", "Réparateur d'électroménager", "Appliance repair"),
    ("waiter", "نادل للمناسبات", "Serveur événementiel", "Event waiter"),
    ("makeup", "خبيرة تجميل", "Maquilleuse", "Makeup artist"),
    ("cook_women", "طباخة للأعراس", "Cuisinière pour mariages", "Wedding cook"),
    ("driver", "سائق خاص", "Chauffeur privé", "Private driver"),
    ("psychologist", "أخصائي نفسي", "Psychologue", "Psychologist"),
    ("marketing", "خبير تسويق", "Expert marketing", "Marketing expert"),
    ("it_expert", "خبير معلوميات", "Expert informatique", "IT expert"),
    ("network_expert", "خبير شبكات", "Expert réseaux", "Network expert"),
    ("ecommerce", "خبير تجارة إلكترونية", "Expert e-commerce", "E-commerce expert"),
    ("coach", "مدرب رياضي", "Coach sportif", "Sports coach"),
    ("interior_design", "مصمم ديكور", "Décorateur d'intérieur", "Interior designer"),
    ("babysitter", "مربية أطفال", "Nounou", "Babysitter"),
    ("graphic_design", "مصمم جرافيك", "Graphiste", "Graphic designer"),
    ("car_wash", "غسيل السيارات", "Lavage auto", "Car wash"),
    ("dj", "DJ ومنسق مناسبات", "DJ événementiel", "Event DJ"),
    ("henna", "نقاشة حناء", "Artiste henné", "Henna artist"),
    ("video_editor", "مونتير فيديو", "Monteur vidéo", "Video editor"),
    ("pet_sitter", "جليس حيوانات أليفة", "Gardien d'animaux", "Pet sitter"),
]

assert len(CATEGORIES) == 35, f"expected 35 categories, got {len(CATEGORIES)}"

with app.app_context():
    db.create_all()

    # lightweight migration for existing DBs (safe to re-run, any backend)
    existing = {c["name"] for c in inspect(db.engine).get_columns("user")}
    for col, typ in (
        ("photo_file", "VARCHAR(255)"),
        ("bio", "TEXT"),
        ("experience_years", "INTEGER"),
        ("phone_verified", "BOOLEAN"),
        ("id_verified", "BOOLEAN"),
    ):
        if col not in existing:
            db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {col} {typ}'))
    # backfill: everyone registered with a phone counts as phone-verified
    # NOTE: use TRUE/FALSE keywords (not 1/0) — Postgres rejects integer=boolean
    db.session.execute(text('UPDATE "user" SET phone_verified = TRUE WHERE phone_verified IS NULL'))
    db.session.execute(text('UPDATE "user" SET id_verified = FALSE WHERE id_verified IS NULL'))
    existing_job = {c["name"] for c in inspect(db.engine).get_columns("job_request")}
    for col, typ in (
        ("counter_price", "INTEGER"),
        ("counter_by", "INTEGER"),
        ("counter_with", "INTEGER"),
        ("completed_at", "TIMESTAMP"),
        ("final_price", "INTEGER"),
    ):
        if col not in existing_job:
            db.session.execute(text(f'ALTER TABLE "job_request" ADD COLUMN {col} {typ}'))
    db.session.commit()

    created, updated = 0, 0
    for key, ar, fr, en in CATEGORIES:
        cat = Category.query.filter_by(key=key).first()
        if cat:
            cat.name_ar, cat.name_fr, cat.name_en = ar, fr, en
            cat.photo = f"{key}.jpg"
            updated += 1
        else:
            db.session.add(Category(
                key=key, name_ar=ar, name_fr=fr, name_en=en, photo=f"{key}.jpg",
            ))
            created += 1
    db.session.commit()
    print(f"[seed] {created} categories created, {updated} updated (35 total)")
    print("[seed] done.")
