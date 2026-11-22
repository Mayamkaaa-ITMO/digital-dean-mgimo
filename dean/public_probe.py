"""One read-only live probe for a pinned group/week with a verified local parser."""
from . import public_schedule, public_transport, public_tusur


def run(source, group, monday, *, read=public_transport.read_week):
    week=public_schedule.week_scope(source,group,monday)
    result={"source":source,"group":group,"monday":week.monday.isoformat() if hasattr(week.monday,"isoformat") else week.monday,
            "publication":False,"live_refresh_enabled":public_schedule.live_available(source)}
    if source!="tusur-timetable":
        return {**result,"result":"adapter_unavailable"}
    try:
        body,_=read(week,None)
        events=public_tusur.parse(week,body) if body else ()
        if not events:
            return {**result,"result":"empty_or_unconfirmed"}
    except public_transport.FetchError as error:
        return {**result,"result":error.code}
    except ValueError:
        return {**result,"result":"parse_failed"}
    return {**result,"result":"read_parsed","records":len(events)}
