"""Intent routing/classification helpers (deterministic + optional local ML)."""
from __future__ import annotations

import difflib
import logging
import re
from collections import defaultdict

from .common import (
    INTENT_LABELS, _norm_text, _token_set, extract_slots,
)
from . import feedback as feedback_mod

logger = logging.getLogger(__name__)

# ── Weighted rule NLU (no deps) ──────────────────────────────────────────────

def _has_any(tl, keys):
    for k in keys:
        c = (k or "").lower()
        if not c:
            continue
        # avoid substring false positives for short tokens (hi in this, vat in reservation, etc.)
        if re.fullmatch(r"[a-z0-9&/\-']{1,4}", c) or c in ("hi","hey","pl","vat","adr","kpi","occ","pnl","ops","pos"):
            if re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", tl):
                return True
        elif c in tl:
            return True
    return False

_INTENT_CUES = {
  "greeting_smalltalk": (1.1, ["hi","hello","hey","good morning","good afternoon","good evening","thanks","thank you","thx","how are you","who are you","what's up","whats up"]),
  "capabilities_help": (1.5, ["what can you do","how can you help","capabilities","your abilities","menu of queries","what tools","how do i phrase","boundaries","assist day to day"]),
  "property_stats": (1.4, ["how many guests","number of guests","total guests","guest count","guests do i have","guests do we have","how many customers","how many reservations","number of reservations","total reservations","reservation count","how many rooms","number of rooms","total rooms","how many bookings","quick stats","property stats","overall stats"]),
  "today_ops": (1.4, ["today's ops","todays ops","ops today","today snapshot","morning briefing","what's happening today","whats happening today","arrivals today","departures today","in-house now","in house now","front desk today"]),
  "business_partner_briefing": (1.5, ["business partner","partner briefing","business briefing","executive briefing","how is the business","how's the business","hows the business","advise me","what should i focus on","priorities for me","recommendations","opportunities and risks","partner view"]),
  "decision_support": (1.3, ["decision support","kpi","how are we doing","manager's overview","manager’s overview","executive snapshot","signals","warning signs","strengths and risks","actionable insights","briefing me","performance indicators"]),
  "compare_periods": (1.4, ["compare","vs last","versus last","this month vs","compared to last","trend vs","previous period","month over month"]),
  "outstanding_balances": (1.4, ["who owes","outstanding balance","outstanding balances","unpaid","who hasn't paid","who hasnt paid","amount due overall","receivables","money owed","collectables","overdue","accounts receivable"]),
  "inventory_alerts": (1.4, ["low stock","running low","out of stock","negative stock","overstock","inventory alert","inventory alerts","restock","stock warnings","what's low","whats low"]),
  "top_selling_items": (1.4, ["top selling","best seller","best sellers","most sold","top items sold","popular items","what sells most","top pos items"]),
  "expenses_summary": (1.3, ["expense breakdown","expenses by category","spending breakdown","cost breakdown","operating expenses summary","where did we spend"]),
  "sales_breakdown": (1.3, ["sales breakdown","revenue breakdown","pos breakdown","sales by item","sales by category","revenue mix","how did guests pay breakdown"]),
  "reservation_pipeline": (1.3, ["reservation pipeline","upcoming arrivals","upcoming reservations","bookings pipeline","reservations by status","future bookings"]),
  "housekeeping_summary": (1.3, ["housekeeping","clean rooms","dirty rooms","room status summary","rooms ready","rooms to clean"]),
  "room_availability": (1.3, ["available rooms","room availability","any rooms free","rooms free","vacant rooms","availability for"]),
  "menu_info": (1.2, ["menu","meals","what meals","dishes","meal list","recipes"]),
  "rate_plans": (1.3, ["rate plan","pricing plan","plans/packages","catalog of rate plans"]),
  "ledger_integrity": (1.4, ["health check","integrity","ledger integrity","data integrity","reconcile ledger","orphaned","mismatched records","sanity check","audit the database","consistency","books/data consistency"]),
  "recent_activity": (1.2, ["recent activity","audit log","what changed lately","recent changes"]),
  "expiring_batches": (1.5, ["expiring batch","expiring batches","expired batch","what expires","expires soon","expiry soon","nearing expiry","batches expiring","items expiring"]),
  "inventory_batches": (1.4, ["list batches","show batches","inventory batches","batch list","stock batches","lots for","list lots","inventory lots"]),
  "trace_barcode": (1.5, ["trace barcode","trace lot","trace code","scan barcode","lookup barcode","barcode lookup","lot trace","track lot","batch trace","received when","when received","barcode trace","sku trace"]),
  "app_update_status": (1.5, ["check for updates","check update","app update","update app","new version","latest version","what version","app version","software update","changelog","update status"]),
  "list_activities": (1.4, ["list activities","available activities","available trips","available excursions","available tours","activities catalog","trips catalog","excursions catalog","register activities","registered activities","registered trips","what activities","what trips can","what excursions"]),
  "activity_stats": (1.4, ["activity stats","activity statistics","trip stats","trip statistics","excursion stats","bookings stats","how many booked","most popular activity","most popular trip","most popular excursion","activity revenue","trip bookings"]),
  "guest_portfolio": (1.5, ["full portfolio","guest portfolio","360","360°","all trips","all stays","all reservations","complete history","entire history","all invoices","all his invoices","all her invoices","all their invoices","all pos orders","all orders","all activities for guest","all payments for guest","everything for guest","full history","complete guest record","lifetime","all visits"]),
  "guest_activity_bookings": (1.4, ["booked activities","booked trips","booked excursions","booked tours","activities they ordered","activities he ordered","activities she ordered","trips they booked","excursions booked","tours booked","ordered activities","activity bookings for guest","guest activities","which activities did"]),
  "guest_last_visit_spend": (1.5, ["how much did","spend on last visit","spent last time","spending last visit","total on last visit"]),
  "guest_top_items_last_visit": (1.6, ["most ordered","ordered most","ordered the most","ordering the most","buy the most","bought the most","top-ordered","biggest pos orders","topped their list","most frequently","most of during that visit","consume/purchase most","what did they order","what did he order","what did she order","items did they buy","which items did","order the most"]),
  "guest_last_visit": (1.3, ["last visit","last stay","previous visit","previous trip","last time","most recent stay","most recent visit","latest stay","latest visit","last came","most recent checked-out","previous stay","when was the last time"]),
  "guest_profile": (1.3, ["profile","everything we know","contact info","past reservations","recent stay history","background for guest","account/profile view","history with us","details and latest bookings","full profile","who are they","what's on file","what’s on file","open the profile","their details"]),
  "guest_search": (1.2, ["find guest","search guest","look up a guest","locate a guest","trying to find a guest","anyone in the system","possible matches","who matches","who do we have on file","show candidates","resembling","search guests for","locate a contact","pull up any matching guest","can you find the guest"]),
  "reservation_balance": (1.4, ["balance","owe","outstanding","amount due","settled","paid versus due","left to collect","folio balance","how much does the guest"]),
  "reservation_summary": (1.3, ["reservation summary","booking summary","details on booking","details on reservation","folio snapshot","what's going on with reservation","rundown of booking","summarize reservation","reservation details"]),
}

def rule_classify(text: str, slots: dict | None = None) -> dict:
    slots = slots or extract_slots(text)
    tl = _norm_text(text)
    rid = slots.get("reservation_id"); gid = slots.get("guest_id"); gq = slots.get("guest_query")

    # greeting/smalltalk (only if short and no data ask)
    if re.fullmatch(r"(hi|hello|hey|good morning|good afternoon|good evening|thanks|thank you|thx|yo)[!.\s]*", tl) or (len(tl.split()) <= 5 and _has_any(tl,["hi","hello","hey","thanks","thank you"])):
        if not (rid or gid or gq or slots.get("report_kind")):
            return {"intent":"greeting_smalltalk","confidence":0.95,"slots":slots,"router":"rules"}

    # unsafe (read-only assistant)
    if _has_any(tl, ["go ahead and delete","delete reservation","delete room","and refund","refund the guest","check the guest in","check in the guest","change room price","change price to","reset the super password","reset password for me","email the guest","send invoice","sync bookings","add a new room","import 500","override checkout","hack into","ignore the rules","hidden admin"]):
        return {"intent":"unsupported_or_unsafe","confidence":0.97,"slots":slots,"router":"rules"}

    def _cue_match(cue, tl):
        c = cue.lower()
        # word-boundary match for short tokens and common acronym/word cues to avoid substring false positives
        if re.fullmatch(r"[a-z0-9&/\-']{1,5}", c) or c in ("hi","hey","pl","vat","adr","kpi","occ","pnl"):
            return re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", tl) is not None
        return c in tl

    scores = defaultdict(float)
    for intent,(w,cues) in _INTENT_CUES.items():
        for c in cues:
            if _cue_match(c, tl): scores[intent] += w
        for c in cues[:6]:
            if len(c)>=6 and not _cue_match(c, tl):
                cs=set(c.split()); ts=_token_set(tl)
                if cs and len(cs & ts)/len(cs) >= 0.8: scores[intent] += w*0.6
    # structural boosts
    if gid or gq or "guest #" in tl or "customer" in tl or "visitor" in tl:
        for k in ("guest_profile","guest_portfolio","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend","guest_activity_bookings","guest_search"): scores[k]+=0.2
    if rid or "reservation #" in tl or "booking #" in tl or "res #" in tl:
        for k in ("reservation_summary","reservation_balance"): scores[k]+=0.25
    rk = slots.get("report_kind")
    if rk:
        mapk={"pl":"report_pl","cash_flow":"report_cash_flow","vat":"report_vat","payment_methods":"report_payment_methods","occupancy":"report_occupancy","daily_ops":"report_daily_ops"}
        if rk in mapk: scores[mapk[rk]] += 1.2
    if rk=="occupancy":
        if _has_any(tl, ["report","adr","revpar","performance","room-nights","metrics","revenue-per-room"]): scores["report_occupancy"] += 0.6
        else: scores["occupancy_summary"] += 0.4
    if scores.get("guest_top_items_last_visit",0)>0 or scores.get("guest_last_visit_spend",0)>0:
        scores["guest_last_visit"] += 0.1
    if _has_any(tl, ["when did","when was the last time","last time they stayed","last stay with us","previous visit here"]) and scores.get("guest_profile",0)>0 and scores.get("guest_top_items_last_visit",0)==0:
        scores["guest_last_visit"] += 0.5
    # ambiguity
    if _has_any(tl, ["he ordered","she ordered","they ordered","his last visit","her last visit","their last visit","that guest","they owe","do they owe","his portfolio","her portfolio"]) and not gid and not gq and not slots.get("context",{}).get("guest_id"):
        scores["ambiguous_needs_clarification"] += 1.2
    if tl.strip() in {"balance","show me the balance","what's the balance","what is the balance"} and not rid:
        scores["ambiguous_needs_clarification"] += 1.3
    if ("report for" in tl or "give me the report" in tl) and not rk and not _has_any(tl, ["daily ops","arrivals","departures"]):
        scores["ambiguous_needs_clarification"] += 0.8
    # compound detection
    report_hits = sum(1 for k in ["report_pl","report_cash_flow","report_vat","report_payment_methods","report_occupancy","report_daily_ops"] if scores.get(k,0)>0)
    guest_chain = (gid or gq or "guest" in tl) and sum(1 for k in ["guest_profile","guest_portfolio","guest_last_visit","guest_top_items_last_visit","guest_last_visit_spend","guest_activity_bookings"] if scores.get(k,0)>0) >= 2
    res_chain = rid and scores.get("reservation_balance",0)>0 and scores.get("reservation_summary",0)>0
    if report_hits>=2 or guest_chain or res_chain or _has_any(tl, ["and also"," as well as"," plus ","p&l and cash flow","compare this month vs","list rate plans and also"]):
        scores["compound_multi_intent"] += 1.0

    if not scores:
        if _has_any(tl, ["report","revenue","expenses","profit"]): return {"intent":"report_pl","confidence":0.5,"slots":slots,"router":"rules"}
        if "guest" in tl and _has_any(tl, ["find","search","look up"]): return {"intent":"guest_search","confidence":0.5,"slots":slots,"router":"rules"}
        return {"intent":"capabilities_help","confidence":0.2,"slots":slots,"router":"rules","low_confidence":True}

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best, best_s = ranked[0]
    second_s = ranked[1][1] if len(ranked)>1 else 0.0
    margin = best_s - second_s
    conf = min(0.98, 0.45 + best_s*0.18 + margin*0.12)
    if best_s < 0.9: conf = min(conf, 0.55)
    if margin < 0.25 and best_s < 1.4: conf = min(conf, 0.6)
    low = conf < 0.45 and not (rid or gid)
    if best=="capabilities_help" and conf < 0.55: low = True
    return {"intent":best,"confidence":round(conf,3),"slots":slots,"router":"rules","margin":round(margin,3),"low_confidence":low,"candidates":ranked[:3]}

def classify_intent(text: str, history=None) -> dict:
    slots = extract_slots(text, history)
    ex = feedback_mod.exemplar_match(text)
    if ex:
        ex_slots = dict(ex.get("slots") or {}); ex_slots.update({k:v for k,v in slots.items() if v not in (None,"",[])})
        ex_slots["context"]=slots.get("context",{})
        return {"intent":ex.get("intent"),"confidence":ex.get("confidence",0.9),"slots":ex_slots,"router":ex.get("source","exemplar")}
    ml = feedback_mod._predict_intent_ml(text)
    rl = rule_classify(text, slots)
    if ml and ml.get("intent") in INTENT_LABELS:
        if rl.get("intent")=="unsupported_or_unsafe": return rl
        if ml.get("confidence",0) >= 0.6 and not rl.get("low_confidence"):
            return {"intent":ml["intent"],"confidence":float(ml.get("confidence",0.7)),"slots":slots,"router":ml.get("router","ml")}
        if rl.get("confidence",0) >= ml.get("confidence",0): return rl
        return {"intent":ml["intent"],"confidence":float(ml.get("confidence",0.6)),"slots":slots,"router":ml.get("router","ml")}
    return rl

