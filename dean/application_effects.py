def complete(db, user, row, now):
    kind = row["service_id"]
    if kind == "individual-plan":
        from .individual_plan import approve
        approve(db, user, row, now)
    elif kind in {"material-aid", "enhanced-stipend"} and db.execute("PRAGMA user_version").fetchone()[0] >= 10:
        from .finances import grant_support
        grant_support(db, user, row, now)
    elif kind == "club-join":
        from .teams import approve_club
        approve_club(db, user, row, now)
    elif kind == "practice-enrolment":
        from .practice import approve
        approve(db, user, row, now)
